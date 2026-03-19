import json
import logging
from typing import Awaitable, Callable

from huggingface_hub import InferenceClient

from src.config import settings
from src.executor.scheduler import DAGScheduler
from src.models.messages import RecoveryPlan, RecoveryStrategy, StageResult, StageStatus
from src.models.pipeline import PipelineSpec, Stage

logger = logging.getLogger(__name__)

HF_MODEL = "meta-llama/Meta-Llama-3-8B-Instruct"

SYSTEM_PROMPT = """You are a CI/CD failure analyst. A pipeline stage has failed.
Analyze the error output and respond with a JSON object containing your recovery plan.

The JSON must have these fields:
- strategy: one of "FIX_AND_RETRY", "SKIP_STAGE", "ROLLBACK", "ABORT"
- reason: brief explanation of what went wrong and why you chose this strategy
- modified_command: (only for FIX_AND_RETRY) the corrected command to try
- rollback_steps: (only for ROLLBACK) list of commands to undo changes

Guidelines:
- FIX_AND_RETRY: Use when the error is fixable by modifying the command (e.g., missing flag, wrong path)
- SKIP_STAGE: Use for non-critical stages where failure is acceptable
- ROLLBACK: Use when a deployment or destructive action needs to be undone
- ABORT: Use when the error is fundamental and cannot be recovered from

Respond with ONLY the JSON object, no markdown or explanation."""


async def analyze_failure(
    stage: Stage, result: StageResult, spec: PipelineSpec
) -> RecoveryPlan:
    """Use Hugging Face Inference API to analyze a stage failure and recommend a recovery strategy."""
    if not settings.hf_api_key:
        logger.warning("No HF_API_KEY configured — skipping AI recovery for stage %s", stage.id)
        return RecoveryPlan(
            strategy=RecoveryStrategy.SKIP_STAGE if not stage.critical else RecoveryStrategy.ABORT,
            reason="No HF API key configured for AI-powered recovery analysis",
        )

    try:
        client = InferenceClient(api_key=settings.hf_api_key)

        # Truncate stderr to last 200 lines
        stderr_lines = result.stderr.strip().split("\n")
        truncated_stderr = "\n".join(stderr_lines[-200:])

        user_message = (
            f"Failed stage details:\n"
            f"- Stage ID: {stage.id}\n"
            f"- Agent type: {stage.agent.value}\n"
            f"- Command: {stage.command}\n"
            f"- Exit code: {result.exit_code}\n"
            f"- Duration: {result.duration_seconds:.1f}s\n\n"
            f"Stderr (last 200 lines):\n{truncated_stderr}\n\n"
            f"Pipeline goal: {spec.goal}\n"
            f"Stage is critical: {stage.critical}\n"
            f"Retry count remaining: {stage.retry_count}"
        )

        logger.info("Calling Hugging Face API for failure analysis of stage %s", stage.id)

        response = client.chat.completions.create(
            model=HF_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            max_tokens=1024,
        )

        response_text = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            response_text = "\n".join(lines)

        try:
            plan_data = json.loads(response_text)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse recovery plan JSON: %s", e)
            return RecoveryPlan(
                strategy=RecoveryStrategy.ABORT,
                reason=f"Failed to parse AI recovery suggestion: {e}",
            )

        plan = RecoveryPlan(**plan_data)
        logger.info(
            "Recovery plan for stage %s: strategy=%s reason=%s",
            stage.id,
            plan.strategy.value,
            plan.reason,
        )
        return plan

    except Exception as e:
        logger.error("HF API call failed for stage %s: %s", stage.id, e)
        return RecoveryPlan(
            strategy=RecoveryStrategy.SKIP_STAGE if not stage.critical else RecoveryStrategy.ABORT,
            reason=f"AI recovery analysis failed: {e}",
        )


async def execute_recovery(
    plan: RecoveryPlan,
    stage: Stage,
    scheduler: DAGScheduler,
    agents: dict,
    working_dir: str = ".",
    use_docker: bool = False,
    language: str = "",
    on_update: Callable[[dict], Awaitable[None]] | None = None,
) -> StageResult | None:
    """Execute a recovery plan and return the result if retried.

    Supports Docker execution, broadcasts step-by-step progress via on_update,
    and handles all four recovery strategies.
    """
    from src.executor.docker_runner import run_in_docker
    from src.models.messages import StageRequest

    async def _broadcast(data: dict) -> None:
        if on_update:
            await on_update(data)

    if plan.strategy == RecoveryStrategy.FIX_AND_RETRY:
        if not plan.modified_command:
            logger.error("FIX_AND_RETRY plan has no modified_command")
            scheduler.skip_dependents(stage.id)
            return None

        logger.info("Retrying stage %s with modified command: %s", stage.id, plan.modified_command)

        # Broadcast that we're applying the fix
        await _broadcast({
            "stage_id": stage.id,
            "status": "running",
            "log_type": "recovery_applying",
            "log_message": f"Applying fix for '{stage.id}': {plan.modified_command[:120]}",
        })

        # Docker execution path
        if use_docker:
            result = await run_in_docker(
                command=plan.modified_command,
                work_dir=working_dir,
                language=language,
                timeout=stage.timeout_seconds,
                env_vars=stage.env_vars or None,
            )
            result.stage_id = stage.id
            # Fall back to local if Docker not available
            if not (result.status == StageStatus.FAILED and "Docker not installed" in result.stderr):
                scheduler.mark_complete(stage.id, result.status, result)
                if result.status == StageStatus.FAILED:
                    scheduler.skip_dependents(stage.id)
                return result
            logger.warning("Docker unavailable for recovery, falling back to local execution")

        # Local execution path
        agent = agents.get(stage.agent)
        if not agent:
            logger.error("No agent found for type %s", stage.agent)
            return None

        request = StageRequest(
            stage_id=stage.id,
            command=plan.modified_command,
            working_dir=working_dir,
            env_vars=stage.env_vars,
            timeout=stage.timeout_seconds,
        )
        result = await agent.execute(request)
        scheduler.mark_complete(stage.id, result.status, result)
        if result.status == StageStatus.FAILED:
            scheduler.skip_dependents(stage.id)
        return result

    elif plan.strategy == RecoveryStrategy.SKIP_STAGE:
        logger.info("Skipping stage %s: %s", stage.id, plan.reason)
        skip_result = StageResult(
            stage_id=stage.id,
            status=StageStatus.SKIPPED,
            stdout=f"Skipped: {plan.reason}",
        )
        scheduler.mark_complete(stage.id, StageStatus.SKIPPED, skip_result)

        await _broadcast({
            "stage_id": stage.id,
            "status": "skipped",
            "log_type": "stage_skipped",
            "log_message": f"Stage '{stage.id}' skipped by AI replanner: {plan.reason}",
        })
        return skip_result

    elif plan.strategy == RecoveryStrategy.ROLLBACK:
        total_steps = len(plan.rollback_steps)
        logger.info("Rolling back stage %s with %d steps", stage.id, total_steps)

        await _broadcast({
            "stage_id": stage.id,
            "status": "running",
            "log_type": "rollback_step",
            "log_message": f"Rolling back '{stage.id}': executing {total_steps} rollback steps",
        })

        for i, step in enumerate(plan.rollback_steps):
            step_num = i + 1
            logger.info("Rollback step %d/%d: %s", step_num, total_steps, step)

            await _broadcast({
                "stage_id": stage.id,
                "status": "running",
                "log_type": "rollback_step",
                "log_message": f"Rollback step {step_num}/{total_steps}: {step[:100]}",
            })

            agent = agents.get(stage.agent)
            if agent:
                request = StageRequest(
                    stage_id=f"{stage.id}_rollback",
                    command=step,
                    working_dir=working_dir,
                    timeout=120,
                )
                await agent.execute(request)

        scheduler.skip_dependents(stage.id)

        await _broadcast({
            "stage_id": stage.id,
            "status": "failed",
            "log_type": "recovery_failed",
            "log_message": f"Rollback complete for '{stage.id}' ({total_steps} steps executed). Stage failed, dependents skipped.",
        })
        return None

    else:  # ABORT
        logger.error("Aborting pipeline: %s", plan.reason)
        scheduler.skip_dependents(stage.id)

        await _broadcast({
            "stage_id": stage.id,
            "status": "failed",
            "log_type": "recovery_failed",
            "log_message": f"Pipeline aborted: {plan.reason}",
        })
        return None
