from src.creator.templates.deploy_commands import get_deploy_command, get_health_check_command
from src.models.pipeline import AgentType, RepoAnalysis, Stage


def generate_nodejs_pipeline(analysis: RepoAnalysis, goal: str) -> list[Stage]:
    """Generate a Node.js CI/CD pipeline with proper DAG parallelism."""
    use_yarn = analysis.has_yarn_lock or analysis.package_manager == "yarn"
    use_pnpm = analysis.package_manager == "pnpm"
    scripts = analysis.available_scripts

    if use_yarn:
        run = "yarn"
        install_cmd = "yarn install --frozen-lockfile"
        audit_cmd = "yarn audit --level moderate || true"
    elif use_pnpm:
        run = "pnpm"
        install_cmd = "pnpm install --frozen-lockfile"
        audit_cmd = "pnpm audit --audit-level moderate || true"
    else:
        run = "npm"
        install_cmd = "npm ci || npm install" if analysis.has_package_lock else "npm install"
        audit_cmd = "npm audit --audit-level=moderate || true"

    stages: list[Stage] = []

    # Stage 1: Install dependencies
    stages.append(
        Stage(
            id="install",
            agent=AgentType.BUILD,
            command=install_cmd,
            depends_on=[],
            timeout_seconds=120,
        )
    )

    # Stage 2 (parallel): lint, unit_test, security_scan
    has_lint = "lint" in scripts
    has_test = "test" in scripts
    has_build = "build" in scripts

    if has_lint:
        lint_cmd = f"{run} run lint"
    else:
        lint_cmd = "echo 'No lint script found, skipping'"

    stages.append(
        Stage(
            id="lint",
            agent=AgentType.TEST,
            command=lint_cmd,
            depends_on=["install"],
            timeout_seconds=60,
            critical=False,
        )
    )

    if has_test:
        test_cmd = f"{run} test"
        if analysis.test_runner == "vitest":
            test_cmd = "npx vitest run"
        elif analysis.test_runner == "mocha":
            test_cmd = "npx mocha"
    else:
        test_cmd = "echo 'No test script found, skipping'"

    stages.append(
        Stage(
            id="unit_test",
            agent=AgentType.TEST,
            command=test_cmd,
            depends_on=["install"],
            timeout_seconds=300,
            critical=False,
        )
    )

    stages.append(
        Stage(
            id="security_scan",
            agent=AgentType.SECURITY,
            command=audit_cmd,
            depends_on=["install"],
            timeout_seconds=120,
            critical=False,
        )
    )

    # Stage 3: Build
    if has_build:
        build_cmd = f"{run} run build"
    else:
        build_cmd = "echo 'No build script found — install verified, package is ready'"

    # Next.js builds are heavier — give them more time and a retry
    is_nextjs = analysis.framework in ("nextjs", "next")
    stages.append(
        Stage(
            id="build",
            agent=AgentType.BUILD,
            command=build_cmd,
            depends_on=["lint", "unit_test", "security_scan"],
            timeout_seconds=600 if is_nextjs else 300,
            retry_count=1 if is_nextjs else 0,
        )
    )

    # Stage 4: Runtime check — start the app and verify it boots without errors
    # Uses a subshell + PID tracking instead of job control (kill %1 doesn't work in non-interactive shells)
    if "start" in scripts:
        rt_start = f"PORT=$RT_PORT {run} start"
    elif is_nextjs and has_build:
        rt_start = f"PORT=$RT_PORT npx -y next start"
    elif has_build:
        rt_start = f"npx -y serve -s build -l $RT_PORT"
    else:
        rt_start = None

    if rt_start:
        runtime_cmd = (
            "RT_PORT=$(node -e \"const s=require('net').createServer();"
            "s.listen(0,()=>{process.stdout.write(String(s.address().port));s.close()})\")"
            f" && {rt_start} & APP_PID=$!"
            " && sleep 5"
            " && curl -sf --max-time 5 http://localhost:$RT_PORT/ -o /dev/null"
            " ; RESULT=$?; kill $APP_PID 2>/dev/null; wait $APP_PID 2>/dev/null; exit $RESULT"
        )
    else:
        runtime_cmd = "echo 'No start script — skipping runtime check'"

    stages.append(
        Stage(
            id="runtime_check",
            agent=AgentType.VERIFY,
            command=runtime_cmd,
            depends_on=["build"],
            timeout_seconds=30,
            critical=False,
        )
    )

    # Stage 5: Integration test (after build, before deploy)
    has_integ_test = "test:integration" in scripts or "test:e2e" in scripts
    if has_integ_test:
        integ_script = "test:integration" if "test:integration" in scripts else "test:e2e"
        integ_cmd = f"{run} run {integ_script}"
    else:
        integ_cmd = f"echo 'No integration test script found — skipping'"

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

    # Stage 6: Deploy (if goal mentions deployment)
    deploy_keywords = ["deploy", "release", "publish", "production", "staging"]
    should_deploy = any(kw in goal.lower() for kw in deploy_keywords)

    if should_deploy:
        # Find a free port at runtime to avoid conflicts with other running services
        find_port = "PORT=$(node -e \"const s=require('net').createServer();s.listen(0,()=>{console.log(s.address().port);s.close()})\")"
        # Build a sensible fallback: prefer deploy script, then start, then serve
        if "deploy" in scripts:
            node_fallback = f"{run} run deploy"
        elif "start" in scripts:
            node_fallback = f"{find_port} && PORT=$PORT {run} start &"
        elif "serve" in scripts:
            node_fallback = f"{find_port} && PORT=$PORT {run} run serve &"
        elif has_build:
            node_fallback = f"{find_port} && npx -y serve -s build -l $PORT &"
        else:
            node_fallback = "echo 'Deploy: no start/deploy script found — skipping'"
        deploy_cmd = get_deploy_command(analysis.deploy_target, analysis.has_dockerfile, node_fallback)

        stages.append(
            Stage(
                id="deploy",
                agent=AgentType.DEPLOY,
                command=deploy_cmd,
                depends_on=["integration_test"],
                timeout_seconds=600,
                retry_count=1,
            )
        )

        # Stage 5: Health check
        stages.append(
            Stage(
                id="health_check",
                agent=AgentType.VERIFY,
                command=get_health_check_command(analysis.deploy_target, default_port=3000),
                depends_on=["deploy"],
                timeout_seconds=120,
                retry_count=2,
                critical=True,
            )
        )

    return stages
