from __future__ import annotations
import uuid

from sqlalchemy import select

from src.db.models import PipelineRow, StageResultRow
from src.db.session import async_session
from src.models.messages import StageResult
from src.models.pipeline import PipelineSpec


async def list_pipelines() -> list[PipelineSpec]:
    """Load all pipelines, most recent first."""
    async with async_session() as session:
        result = await session.execute(
            select(PipelineRow).order_by(PipelineRow.created_at.desc())
        )
        return [
            PipelineSpec.model_validate_json(row.spec_json)
            for row in result.scalars()
        ]


async def save_pipeline(spec: PipelineSpec) -> None:
    """Persist a pipeline spec to the database."""
    row = PipelineRow(
        pipeline_id=spec.pipeline_id,
        name=spec.name,
        repo_url=spec.repo_url,
        goal=spec.goal,
        created_at=spec.created_at,
        work_dir=spec.work_dir,
        spec_json=spec.model_dump_json(),
    )
    async with async_session() as session:
        session.add(row)
        await session.commit()


async def get_pipeline(pipeline_id: str) -> PipelineSpec | None:
    """Load a pipeline spec by ID."""
    async with async_session() as session:
        result = await session.execute(
            select(PipelineRow).where(PipelineRow.pipeline_id == pipeline_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return PipelineSpec.model_validate_json(row.spec_json)


async def save_results(pipeline_id: str, results: dict[str, StageResult]) -> None:
    """Persist execution results for a pipeline."""
    async with async_session() as session:
        # Delete old results for this pipeline
        old = await session.execute(
            select(StageResultRow).where(StageResultRow.pipeline_id == pipeline_id)
        )
        for row in old.scalars():
            session.delete(row)

        # Insert new results
        for stage_id, result in results.items():
            row = StageResultRow(
                id=str(uuid.uuid4()),
                pipeline_id=pipeline_id,
                stage_id=stage_id,
                status=result.status.value,
                exit_code=str(result.exit_code),
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=str(result.duration_seconds),
                result_json=result.model_dump_json(),
            )
            session.add(row)
        await session.commit()


async def update_pipeline(spec: PipelineSpec) -> bool:
    """Update an existing pipeline spec in the database."""
    async with async_session() as session:
        result = await session.execute(
            select(PipelineRow).where(PipelineRow.pipeline_id == spec.pipeline_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return False
        row.name = spec.name
        row.goal = spec.goal
        row.spec_json = spec.model_dump_json()
        await session.commit()
        return True


async def delete_pipeline(pipeline_id: str) -> bool:
    """Delete a pipeline and its results from the database."""
    async with async_session() as session:
        result = await session.execute(
            select(PipelineRow).where(PipelineRow.pipeline_id == pipeline_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return False
        session.delete(row)
        await session.commit()
        return True


async def get_results(pipeline_id: str) -> dict[str, StageResult] | None:
    """Load execution results for a pipeline."""
    async with async_session() as session:
        result = await session.execute(
            select(StageResultRow).where(StageResultRow.pipeline_id == pipeline_id)
        )
        rows = result.scalars().all()
        if not rows:
            return None
        return {
            row.stage_id: StageResult.model_validate_json(row.result_json)
            for row in rows
        }
