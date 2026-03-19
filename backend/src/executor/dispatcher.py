import asyncio
import logging
import re
from typing import Callable, Awaitable

from src.executor.agents import (
    BuildAgent,
    DeployAgent,
    SecurityAgent,
    TestAgent,
    VerifyAgent,
)
from src.executor.docker_runner import run_in_docker
from src.executor.port_utils import (
    detect_port_conflict,
    extract_port_from_command,
    find_free_port,
    replace_port_in_command,
)
from src.executor.replanner import analyze_failure, execute_recovery
from src.executor.scheduler import DAGScheduler
from src.models.messages import StageRequest, StageResult, StageStatus
from src.models.pipeline import AgentType, PipelineSpec

logger = logging.getLogger(__name__)

_PORT_PATTERNS = [
    re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})"),
    re.compile(r"(?:listening|running|started|serving)\s+(?:on|at)\s+(?:port\s+)?(\d{2,5})", re.IGNORECASE),
    re.compile(r"port\s+(\d{2,5})", re.IGNORECASE),
    re.compile(r"-p\s+(\d{2,5}):\d+"),
]


def extract_deploy_url(stdout: str, stderr: str, command: str = "") -> str | None:
    """Extract a deploy URL from stage output or command by detecting port numbers."""
    for text in (stdout, stderr, command):
        if not text:
            continue
        for pattern in _PORT_PATTERNS:
            match = pattern.search(text)
            if match:
                port = match.group(1)
                return f"http://localhost:{port}"
    return None


AGENT_MAP = {
    AgentType.BUILD: BuildAgent,
    AgentType.TEST: TestAgent,
    AgentType.SECURITY: SecurityAgent,
    AgentType.DEPLOY: DeployAgent,
    AgentType.VERIFY: VerifyAgent,
}


def _collect_upstream_context(
    stage_id: str, scheduler: DAGScheduler
) -> tuple[dict[str, str], list[str]]:
    """Collect environment variables and artifact paths from upstream stages.

    Injects STAGE_<ID>_STATUS, STAGE_<ID>_EXIT_CODE, and STAGE_<ID>_DURATION
    for each direct predecessor, enabling inter-stage communication.
    """
    env_from_upstream: dict[str, str] = {}
    artifacts_from: list[str] = []

    predecessors = list(scheduler.graph.predecessors(stage_id))
    for pred_id in predecessors:
        result = scheduler.get_result(pred_id)
        if not result:
            continue

        # Inject predecessor status/exit_code/duration as env vars
        prefix = f"STAGE_{pred_id.upper().replace('-', '_')}"
        env_from_upstream[f"{prefix}_STATUS"] = result.status.value
        env_from_upstream[f"{prefix}_EXIT_CODE"] = str(result.exit_code)
        env_from_upstream[f"{prefix}_DURATION"] = f"{result.duration_seconds:.1f}"

        # Forward any metadata from predecessor as env vars
        for key, value in result.metadata.items():
            env_from_upstream[f"{prefix}_{key.upper()}"] = str(value)

        # Collect artifact paths
        if result.artifacts:
            artifacts_from.extend(result.artifacts)

    return env_from_upstream, artifacts_from


async def _execute_stage(
    stage_id: str,
    scheduler: DAGScheduler,
    agents: dict,
    working_dir: str,
    use_docker: bool = False,
    language: str = "",
    command_override: str | None = None,
) -> StageResult:
    """Execute a single stage using the appropriate agent."""
    stage = scheduler.get_stage(stage_id)
    command = command_override or stage.command

    scheduler.mark_running(stage_id)

    # Collect context from upstream stages for inter-stage communication
    upstream_env, artifacts_from = _collect_upstream_context(stage_id, scheduler)

    # Merge upstream env vars with stage-defined env vars (stage takes precedence)
    merged_env = {**upstream_env, **(stage.env_vars or {})}

    # Docker execution path
    if use_docker:
        result = await run_in_docker(
            command=command,
            work_dir=working_dir,
            language=language,
            timeout=stage.timeout_seconds,
            env_vars=merged_env or None,
        )
        # If Docker fails (not installed), fall back to local execution
        if result.status == StageStatus.FAILED and "Docker not installed" in result.stderr:
            logger.warning("Docker unavailable, falling back to local execution for stage %s", stage_id)
        else:
            result.stage_id = stage_id
            return result

    # Local execution path
    agent = agents.get(stage.agent)
    if not agent:
        logger.error("No agent for type %s", stage.agent)
        return StageResult(
            stage_id=stage_id,
            status=StageStatus.FAILED,
            stderr=f"No agent registered for type {stage.agent}",
        )

    request = StageRequest(
        stage_id=stage_id,
        command=command,
        working_dir=working_dir,
        env_vars=merged_env,
        timeout=stage.timeout_seconds,
        artifacts_from=artifacts_from,
    )

    result = await agent.execute(request)
    return result


async def run_pipeline(
    spec: PipelineSpec,
    working_dir: str = ".",
    on_update: Callable[[dict], Awaitable[None]] | None = None,
) -> dict[str, StageResult]:
    """Execute a full pipeline using DAG-based scheduling.

    Dispatches ready stages concurrently, handles failures with
    retry logic and AI-powered replanning.
    """
    scheduler = DAGScheduler(spec)
    agents = {agent_type: cls() for agent_type, cls in AGENT_MAP.items()}
    use_docker = spec.use_docker
    language = spec.analysis.language if spec.analysis else ""

    if use_docker:
        logger.info("Docker execution enabled for pipeline %s", spec.pipeline_id)

    async def _broadcast(data: dict) -> None:
        if on_update:
            await on_update(data)

    logger.info(
        "Starting pipeline %s with %d stages", spec.pipeline_id, len(spec.stages)
    )

    await _broadcast({
        "log_type": "pipeline_start",
        "log_message": f"Pipeline started with {len(spec.stages)} stages",
        "stage_id": "",
        "status": "running",
    })

    while not scheduler.is_finished():
        ready = scheduler.get_ready_stages()

        if not ready:
            logger.warning("No ready stages but pipeline not finished — breaking")
            await _broadcast({
                "log_type": "info",
                "log_message": "No stages ready to execute — pipeline stalled",
                "stage_id": "",
                "status": "failed",
            })
            break

        logger.info("Dispatching %d stages: %s", len(ready), ready)

        # Broadcast running status for all ready stages
        for stage_id in ready:
            stage = scheduler.get_stage(stage_id)
            await _broadcast({
                "stage_id": stage_id,
                "status": "running",
                "log_type": "stage_start",
                "log_message": f"Starting stage '{stage_id}' ({stage.agent.value} agent) — {stage.command[:80]}",
            })

        # Run all ready stages concurrently
        tasks = [
            _execute_stage(stage_id, scheduler, agents, working_dir, use_docker, language)
            for stage_id in ready
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for stage_id, result in zip(ready, results):
            if isinstance(result, Exception):
                logger.error("Stage %s raised exception: %s", stage_id, result)
                error_result = StageResult(
                    stage_id=stage_id,
                    status=StageStatus.FAILED,
                    stderr=str(result),
                )
                scheduler.mark_complete(stage_id, StageStatus.FAILED, error_result)
                stage = scheduler.get_stage(stage_id)
                await _broadcast({
                    "stage_id": stage_id,
                    "status": "failed",
                    "log_type": "stage_failed",
                    "log_message": f"Stage '{stage_id}' crashed: {str(result)[:120]}",
                })
                if stage.critical:
                    scheduler.skip_dependents(stage_id)
                    await _broadcast({
                        "stage_id": stage_id,
                        "status": "failed",
                        "log_type": "info",
                        "log_message": f"Skipping dependents of critical stage '{stage_id}'",
                    })
                continue

            if result.status == StageStatus.SUCCESS:
                # Check if this is a deploy stage and extract the URL
                stage_spec = scheduler.get_stage(stage_id)
                deploy_url = None
                if stage_spec.agent == AgentType.DEPLOY:
                    deploy_url = extract_deploy_url(
                        result.stdout or "", result.stderr or "", stage_spec.command
                    )
                    # Fallback: check health_check stage command for a port/URL
                    if not deploy_url:
                        for succ_id in scheduler.graph.successors(stage_id):
                            succ_stage = scheduler.get_stage(succ_id)
                            if succ_stage.agent == AgentType.VERIFY:
                                deploy_url = extract_deploy_url("", "", succ_stage.command)
                                if deploy_url:
                                    break
                    if deploy_url:
                        result.metadata["deploy_url"] = deploy_url
                        logger.info("Detected deploy URL for stage %s: %s", stage_id, deploy_url)

                scheduler.mark_complete(stage_id, StageStatus.SUCCESS, result)
                broadcast_data = {
                    "stage_id": stage_id,
                    "status": "success",
                    "duration_seconds": result.duration_seconds,
                    "log_type": "stage_success",
                    "log_message": f"Stage '{stage_id}' succeeded in {result.duration_seconds:.1f}s",
                }
                if deploy_url:
                    broadcast_data["deploy_url"] = deploy_url
                await _broadcast(broadcast_data)
                continue

            # Stage failed — handle recovery
            stage = scheduler.get_stage(stage_id)

            # Check if non-critical — skip and continue
            if not stage.critical:
                logger.info("Non-critical stage %s failed, skipping", stage_id)
                result.status = StageStatus.SKIPPED
                scheduler.mark_complete(stage_id, StageStatus.SKIPPED, result)
                stderr_preview = (result.stderr or "")[:100]
                await _broadcast({
                    "stage_id": stage_id,
                    "status": "skipped",
                    "log_type": "stage_skipped",
                    "log_message": f"Stage '{stage_id}' failed but is non-critical — skipped. {stderr_preview}",
                })
                continue

            # Port conflict auto-recovery for deploy stages
            if stage.agent == AgentType.DEPLOY and detect_port_conflict(result.stderr or ""):
                old_port = extract_port_from_command(stage.command)
                try:
                    free_port = find_free_port(preferred=old_port or 8000)
                except RuntimeError:
                    free_port = None

                if free_port and old_port:
                    new_command = replace_port_in_command(stage.command, old_port, free_port)
                    logger.info(
                        "Port %d in use for stage %s, retrying on port %d",
                        old_port, stage_id, free_port,
                    )
                    await _broadcast({
                        "stage_id": stage_id,
                        "status": "running",
                        "log_type": "recovery_plan",
                        "log_message": f"Port {old_port} was in use. Retrying deploy on port {free_port}.",
                        "recovery_strategy": "FIX_AND_RETRY",
                        "recovery_reason": f"Port {old_port} was in use. Deployed on port {free_port} instead.",
                        "modified_command": new_command,
                    })

                    # Reset status so _execute_stage can mark_running again
                    scheduler.reset_stage(stage_id)
                    retry_result = await _execute_stage(
                        stage_id, scheduler, agents, working_dir,
                        use_docker, language, command_override=new_command,
                    )

                    if retry_result.status == StageStatus.SUCCESS:
                        deploy_url = f"http://localhost:{free_port}"
                        retry_result.metadata["deploy_url"] = deploy_url
                        # Also update the health_check stage command with new port
                        for succ_id in scheduler.graph.successors(stage_id):
                            succ_stage = scheduler.get_stage(succ_id)
                            if succ_stage.agent == AgentType.VERIFY and old_port:
                                succ_stage.command = replace_port_in_command(
                                    succ_stage.command, old_port, free_port
                                )

                        scheduler.mark_complete(stage_id, StageStatus.SUCCESS, retry_result)
                        await _broadcast({
                            "stage_id": stage_id,
                            "status": "success",
                            "duration_seconds": retry_result.duration_seconds,
                            "deploy_url": deploy_url,
                            "log_type": "recovery_success",
                            "log_message": f"Port conflict resolved. Stage '{stage_id}' succeeded on port {free_port} in {retry_result.duration_seconds:.1f}s",
                        })
                        continue
                    # Port retry also failed — fall through to normal recovery
                    result = retry_result

            # Check retry count
            if stage.retry_count > 0:
                retries_left = stage.retry_count
                logger.info(
                    "Retrying stage %s (%d retries remaining)",
                    stage_id,
                    retries_left,
                )
                stage.retry_count -= 1
                scheduler.mark_complete(stage_id, StageStatus.FAILED, result)
                scheduler.reset_stage(stage_id)
                await _broadcast({
                    "stage_id": stage_id,
                    "status": "running",
                    "log_type": "retry",
                    "log_message": f"Retrying stage '{stage_id}' ({retries_left} retries remaining)",
                })
                continue

            # Use AI replanner for critical failures
            try:
                await _broadcast({
                    "stage_id": stage_id,
                    "status": "failed",
                    "log_type": "recovery_start",
                    "log_message": f"Stage '{stage_id}' failed (exit code {result.exit_code}). Analyzing failure for self-healing...",
                    "log_tail": (result.stderr or result.stdout or "")[:200],
                })

                plan = await analyze_failure(stage, result, spec)

                # Broadcast recovery plan through WebSocket
                await _broadcast({
                    "stage_id": stage_id,
                    "status": "failed",
                    "recovery_strategy": plan.strategy.value,
                    "recovery_reason": plan.reason,
                    "modified_command": plan.modified_command,
                    "log_type": "recovery_plan",
                    "log_message": f"Recovery plan for '{stage_id}': {plan.strategy.value} — {plan.reason}" + (f" | New command: {plan.modified_command[:80]}" if plan.modified_command else ""),
                })

                recovery_result = await execute_recovery(
                    plan, stage, scheduler, agents, working_dir,
                    use_docker=use_docker, language=language, on_update=_broadcast,
                )
                if recovery_result is None:
                    scheduler.mark_complete(stage_id, StageStatus.FAILED, result)
                    scheduler.skip_dependents(stage_id)
                    await _broadcast({
                        "stage_id": stage_id,
                        "status": "failed",
                        "log_type": "recovery_failed",
                        "log_message": f"Recovery for '{stage_id}' did not produce a result — stage failed. Skipping dependents.",
                    })
                elif recovery_result.status == StageStatus.SUCCESS:
                    await _broadcast({
                        "stage_id": stage_id,
                        "status": "success",
                        "duration_seconds": recovery_result.duration_seconds,
                        "log_type": "recovery_success",
                        "log_message": f"Self-healing succeeded for '{stage_id}' in {recovery_result.duration_seconds:.1f}s",
                    })
                else:
                    scheduler.mark_complete(stage_id, StageStatus.FAILED, result)
                    scheduler.skip_dependents(stage_id)
                    await _broadcast({
                        "stage_id": stage_id,
                        "status": "failed",
                        "log_type": "recovery_failed",
                        "log_message": f"Recovery for '{stage_id}' failed — {(recovery_result.stderr or '')[:100]}",
                    })
            except Exception as e:
                logger.error("Recovery failed for stage %s: %s", stage_id, e)
                scheduler.mark_complete(stage_id, StageStatus.FAILED, result)
                scheduler.skip_dependents(stage_id)
                await _broadcast({
                    "stage_id": stage_id,
                    "status": "failed",
                    "log_type": "recovery_failed",
                    "log_message": f"Recovery error for '{stage_id}': {str(e)[:120]}",
                })

    all_results = scheduler.get_all_results()
    succeeded = sum(1 for r in all_results.values() if r.status == StageStatus.SUCCESS)
    failed = sum(1 for r in all_results.values() if r.status == StageStatus.FAILED)
    skipped = sum(1 for r in all_results.values() if r.status == StageStatus.SKIPPED)

    logger.info(
        "Pipeline %s complete: %d succeeded, %d failed, %d skipped",
        spec.pipeline_id,
        succeeded,
        failed,
        skipped,
    )

    overall = "succeeded" if failed == 0 else "failed"
    await _broadcast({
        "stage_id": "",
        "status": "success" if failed == 0 else "failed",
        "log_type": "pipeline_done",
        "log_message": f"Pipeline {overall}: {succeeded} succeeded, {failed} failed, {skipped} skipped",
    })

    return all_results
