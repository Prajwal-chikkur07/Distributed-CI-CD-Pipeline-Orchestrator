import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.executor.replanner import analyze_failure
from src.models.messages import RecoveryStrategy, StageResult, StageStatus
from src.models.pipeline import AgentType, PipelineSpec, RepoAnalysis, Stage


@pytest.fixture(autouse=True)
def mock_hf_key():
    with patch("src.executor.replanner.settings.hf_api_key", "dummy_key"):
        yield


def _make_spec() -> PipelineSpec:
    return PipelineSpec(
        repo_url="https://github.com/test/repo",
        goal="deploy to production",
        analysis=RepoAnalysis(language="python", package_manager="pip"),
        stages=[
            Stage(id="build", agent=AgentType.BUILD, command="pip install ."),
        ],
    )


def _make_failed_result(stage_id: str = "build") -> StageResult:
    return StageResult(
        stage_id=stage_id,
        status=StageStatus.FAILED,
        exit_code=1,
        stdout="",
        stderr="ModuleNotFoundError: No module named 'missing_dep'\n" * 5,
        duration_seconds=2.5,
    )


def _mock_hf_response(text: str) -> MagicMock:
    """Create a mock Hugging Face Inference API response."""
    mock_message = MagicMock()
    mock_message.content = text
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    return mock_response


def _mock_hf_client(response_text: str):
    """Create a patched InferenceClient that returns the given response text."""
    mock_response = _mock_hf_response(response_text)
    mock_client = MagicMock()
    mock_client.chat.completions.create = MagicMock(return_value=mock_response)
    return mock_client


class TestAnalyzeFailure:
    @pytest.mark.asyncio
    async def test_returns_fix_and_retry(self) -> None:
        stage = Stage(id="build", agent=AgentType.BUILD, command="pip install .")
        result = _make_failed_result()
        spec = _make_spec()

        response_data = {
            "strategy": "FIX_AND_RETRY",
            "reason": "Missing dependency",
            "modified_command": "pip install missing_dep && pip install .",
        }

        with patch("src.executor.replanner.InferenceClient") as mock_cls:
            mock_cls.return_value = _mock_hf_client(json.dumps(response_data))
            plan = await analyze_failure(stage, result, spec)

        assert plan.strategy == RecoveryStrategy.FIX_AND_RETRY
        assert plan.modified_command is not None
        assert "missing_dep" in plan.modified_command

    @pytest.mark.asyncio
    async def test_returns_abort_on_invalid_json(self) -> None:
        stage = Stage(id="build", agent=AgentType.BUILD, command="pip install .")
        result = _make_failed_result()
        spec = _make_spec()

        with patch("src.executor.replanner.InferenceClient") as mock_cls:
            mock_cls.return_value = _mock_hf_client("This is not valid JSON at all")
            plan = await analyze_failure(stage, result, spec)

        assert plan.strategy == RecoveryStrategy.ABORT
        assert "parse" in plan.reason.lower()

    @pytest.mark.asyncio
    async def test_returns_skip_for_non_critical(self) -> None:
        stage = Stage(
            id="lint",
            agent=AgentType.TEST,
            command="flake8 .",
            critical=False,
        )
        result = _make_failed_result("lint")
        spec = _make_spec()

        response_data = {
            "strategy": "SKIP_STAGE",
            "reason": "Lint failures are non-critical",
        }

        with patch("src.executor.replanner.InferenceClient") as mock_cls:
            mock_cls.return_value = _mock_hf_client(json.dumps(response_data))
            plan = await analyze_failure(stage, result, spec)

        assert plan.strategy == RecoveryStrategy.SKIP_STAGE

    @pytest.mark.asyncio
    async def test_returns_rollback_with_steps(self) -> None:
        stage = Stage(id="deploy", agent=AgentType.DEPLOY, command="kubectl apply -f .")
        result = _make_failed_result("deploy")
        spec = _make_spec()

        response_data = {
            "strategy": "ROLLBACK",
            "reason": "Deployment failed, need to rollback",
            "rollback_steps": ["kubectl rollout undo deployment/app"],
        }

        with patch("src.executor.replanner.InferenceClient") as mock_cls:
            mock_cls.return_value = _mock_hf_client(json.dumps(response_data))
            plan = await analyze_failure(stage, result, spec)

        assert plan.strategy == RecoveryStrategy.ROLLBACK
        assert len(plan.rollback_steps) == 1
