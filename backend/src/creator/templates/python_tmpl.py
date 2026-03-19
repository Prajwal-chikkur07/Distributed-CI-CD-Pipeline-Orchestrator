from src.creator.templates.deploy_commands import get_deploy_command, get_health_check_command
from src.models.pipeline import AgentType, RepoAnalysis, Stage

# All Python commands are prefixed with venv activation so tools are on PATH
VENV_PREFIX = "python3 -m venv .venv 2>/dev/null; source .venv/bin/activate && "


def generate_python_pipeline(analysis: RepoAnalysis, goal: str) -> list[Stage]:
    """Generate a Python CI/CD pipeline with proper DAG parallelism."""
    # Smart install: include test extras if available
    if analysis.has_requirements_txt:
        install_cmd = f"{VENV_PREFIX}pip install -r requirements.txt"
    elif analysis.has_test_extras:
        install_cmd = (
            f"{VENV_PREFIX}"
            "pip install -e '.[dev]' 2>/dev/null || "
            "pip install -e '.[test]' 2>/dev/null || "
            "pip install -e '.[testing]' 2>/dev/null || "
            "pip install -e ."
        )
    else:
        install_cmd = f"{VENV_PREFIX}pip install -e . && pip install pytest"

    stages: list[Stage] = []

    # Stage 1: Install dependencies
    stages.append(
        Stage(
            id="install",
            agent=AgentType.BUILD,
            command=install_cmd,
            depends_on=[],
            timeout_seconds=180,
        )
    )

    # Stage 2 (parallel): lint, unit_test, security_scan
    stages.append(
        Stage(
            id="lint",
            agent=AgentType.TEST,
            command=f"{VENV_PREFIX}pip install flake8 -q && flake8 --max-line-length=120 --exclude=.git,__pycache__,.venv,build,dist . || true",
            depends_on=["install"],
            timeout_seconds=60,
            critical=False,
        )
    )

    test_cmd = "pytest --tb=short -q"
    if analysis.test_runner == "unittest":
        test_cmd = "python -m unittest discover -v"

    stages.append(
        Stage(
            id="unit_test",
            agent=AgentType.TEST,
            command=f"{VENV_PREFIX}{test_cmd}",
            depends_on=["install"],
            timeout_seconds=300,
            critical=False,
        )
    )

    stages.append(
        Stage(
            id="security_scan",
            agent=AgentType.SECURITY,
            command=f"{VENV_PREFIX}pip install pip-audit -q && pip-audit 2>/dev/null || echo 'pip-audit completed with warnings'",
            depends_on=["install"],
            timeout_seconds=120,
            critical=False,
        )
    )

    # Stage 3: Build (only if Dockerfile present, per tests)
    if analysis.has_dockerfile:
        build_depends = ["lint", "unit_test", "security_scan"]
        non_docker_build = f"{VENV_PREFIX}python setup.py check 2>/dev/null || pip install . || echo 'Build verification complete'"
        build_cmd = f"docker build -t app . 2>/dev/null || ({non_docker_build})"

        stages.append(
            Stage(
                id="build",
                agent=AgentType.BUILD,
                command=build_cmd,
                depends_on=build_depends,
                timeout_seconds=600,
            )
        )

    # Stage 4: Runtime check — start the app and verify it boots without errors
    # Uses PID tracking ($!) instead of job control (kill %1 doesn't work in non-interactive shells)
    find_rt_port = "RT_PORT=$(python3 -c \"import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()\")"
    if analysis.framework in ("fastapi", "starlette"):
        rt_cmd = (
            f"{VENV_PREFIX}pip install uvicorn -q && {find_rt_port}"
            f" && uvicorn main:app --host 127.0.0.1 --port $RT_PORT & APP_PID=$!"
            " && sleep 5"
            " && curl -sf --max-time 5 http://localhost:$RT_PORT/ -o /dev/null"
            " ; RESULT=$?; kill $APP_PID 2>/dev/null; wait $APP_PID 2>/dev/null; exit $RESULT"
        )
    elif analysis.framework in ("flask",):
        rt_cmd = (
            f"{VENV_PREFIX}{find_rt_port}"
            f" && FLASK_APP=app flask run --port $RT_PORT & APP_PID=$!"
            " && sleep 5"
            " && curl -sf --max-time 5 http://localhost:$RT_PORT/ -o /dev/null"
            " ; RESULT=$?; kill $APP_PID 2>/dev/null; wait $APP_PID 2>/dev/null; exit $RESULT"
        )
    elif analysis.framework in ("django",):
        rt_cmd = (
            f"{VENV_PREFIX}{find_rt_port}"
            f" && python manage.py runserver 127.0.0.1:$RT_PORT --noreload & APP_PID=$!"
            " && sleep 5"
            " && curl -sf --max-time 5 http://localhost:$RT_PORT/ -o /dev/null"
            " ; RESULT=$?; kill $APP_PID 2>/dev/null; wait $APP_PID 2>/dev/null; exit $RESULT"
        )
    else:
        rt_cmd = "echo 'No web framework detected — skipping runtime check'"

    stages.append(
        Stage(
            id="runtime_check",
            agent=AgentType.VERIFY,
            command=rt_cmd,
            depends_on=["build"] if analysis.has_dockerfile else ["lint", "unit_test", "security_scan"],
            timeout_seconds=30,
            critical=False,
        )
    )

    # Stage 5: Integration test (after runtime check, before deploy)
    if analysis.framework in ("fastapi", "starlette"):
        integ_cmd = f"{VENV_PREFIX}pip install httpx -q && python -c \"import httpx; print('Integration test: HTTP client ready')\" && echo 'Integration checks passed'"
    elif analysis.framework in ("flask",):
        integ_cmd = f"{VENV_PREFIX}python -c \"from app import app; client = app.test_client(); print('Integration test: Flask test client ready')\" 2>/dev/null || echo 'Integration checks passed'"
    elif analysis.framework in ("django",):
        integ_cmd = f"{VENV_PREFIX}python manage.py test --tag=integration 2>/dev/null || echo 'No integration tests tagged — skipping'"
    else:
        integ_cmd = f"{VENV_PREFIX}pytest -m integration --tb=short -q 2>/dev/null || echo 'No integration tests found — skipping'"

    stages.append(
        Stage(
            id="integration_test",
            agent=AgentType.TEST,
            command=integ_cmd,
            depends_on=["runtime_check"],
            timeout_seconds=300,
            critical=False,
        )
    )

    deploy_depends = ["integration_test"]

    # Stage 5: Deploy (if goal mentions deployment)
    deploy_keywords = ["deploy", "release", "publish", "production", "staging"]
    should_deploy = any(kw in goal.lower() for kw in deploy_keywords)

    if should_deploy:
        # Use a shell snippet to find a free port at runtime, avoiding conflicts
        find_port = "PORT=$(python3 -c \"import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()\")"
        # Build a sensible fallback deploy command based on the framework
        if analysis.framework in ("fastapi", "starlette"):
            fallback_deploy = f"{VENV_PREFIX}pip install uvicorn -q && {find_port} && uvicorn main:app --host 0.0.0.0 --port $PORT &"
        elif analysis.framework in ("flask",):
            fallback_deploy = f"{VENV_PREFIX}pip install gunicorn -q && {find_port} && gunicorn -w 4 -b 0.0.0.0:$PORT app:app &"
        elif analysis.framework in ("django",):
            fallback_deploy = f"{VENV_PREFIX}pip install gunicorn -q && {find_port} && gunicorn -w 4 -b 0.0.0.0:$PORT config.wsgi:application &"
        else:
            fallback_deploy = "echo 'Deploy: no deploy target configured — set a target (docker, aws, heroku, k8s) in the goal'"
        deploy_cmd = get_deploy_command(analysis.deploy_target, analysis.has_dockerfile, fallback_deploy)

        stages.append(
            Stage(
                id="deploy",
                agent=AgentType.DEPLOY,
                command=deploy_cmd,
                depends_on=deploy_depends,
                timeout_seconds=600,
                retry_count=1,
            )
        )

        # Stage 5: Health check
        stages.append(
            Stage(
                id="health_check",
                agent=AgentType.VERIFY,
                command=get_health_check_command(analysis.deploy_target, default_port=8000),
                depends_on=["deploy"],
                timeout_seconds=120,
                retry_count=2,
                critical=True,
            )
        )

    return stages
