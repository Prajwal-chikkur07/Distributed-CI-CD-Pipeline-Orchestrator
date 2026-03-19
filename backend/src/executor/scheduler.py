from __future__ import annotations
import logging
from typing import Optional

import networkx as nx

from src.models.messages import StageResult, StageStatus
from src.models.pipeline import PipelineSpec, Stage

logger = logging.getLogger(__name__)


class DAGScheduler:
    """Schedules pipeline stages based on their dependency DAG."""

    def __init__(self, spec: PipelineSpec) -> None:
        self.spec = spec
        self.graph = nx.DiGraph()
        self._stages: dict[str, Stage] = {}
        self._statuses: dict[str, StageStatus] = {}
        self._results: dict[str, StageResult] = {}

        # Build the graph
        for stage in spec.stages:
            self._stages[stage.id] = stage
            self._statuses[stage.id] = StageStatus.PENDING
            self.graph.add_node(stage.id)

        for stage in spec.stages:
            for dep in stage.depends_on:
                if dep not in self._stages:
                    raise ValueError(
                        f"Stage '{stage.id}' depends on unknown stage '{dep}'"
                    )
                self.graph.add_edge(dep, stage.id)

        if not nx.is_directed_acyclic_graph(self.graph):
            raise ValueError("Pipeline stages contain a cycle")

        logger.info(
            "DAGScheduler initialized with %d stages, order: %s",
            len(spec.stages),
            list(nx.topological_sort(self.graph)),
        )

    def get_ready_stages(self) -> list[str]:
        """Return stage IDs that are ready to run.

        A stage is ready when it is PENDING and all its predecessors
        have completed with SUCCESS.
        """
        ready = []
        for stage_id, status in self._statuses.items():
            if status != StageStatus.PENDING:
                continue
            predecessors = list(self.graph.predecessors(stage_id))
            if all(
                self._statuses[p] in (StageStatus.SUCCESS, StageStatus.SKIPPED)
                for p in predecessors
            ):
                ready.append(stage_id)
        return ready

    def mark_complete(
        self, stage_id: str, status: StageStatus, result: StageResult
    ) -> None:
        """Mark a stage as complete with the given status and result."""
        self._statuses[stage_id] = status
        self._results[stage_id] = result
        logger.info("Stage %s marked as %s", stage_id, status.value)

    def mark_running(self, stage_id: str) -> None:
        """Mark a stage as currently running."""
        self._statuses[stage_id] = StageStatus.RUNNING

    def reset_stage(self, stage_id: str) -> None:
        """Reset a stage to PENDING status (e.g. for retry)."""
        self._statuses[stage_id] = StageStatus.PENDING
        self._results.pop(stage_id, None)

    def is_finished(self) -> bool:
        """Check if all stages are finished (no PENDING or RUNNING remain)."""
        return all(
            s not in (StageStatus.PENDING, StageStatus.RUNNING)
            for s in self._statuses.values()
        )

    def get_stage(self, stage_id: str) -> Stage:
        """Look up a stage by ID."""
        stage = self._stages.get(stage_id)
        if not stage:
            raise KeyError(f"Unknown stage: {stage_id}")
        return stage

    def get_status(self, stage_id: str) -> StageStatus:
        """Get the current status of a stage."""
        return self._statuses[stage_id]

    def get_result(self, stage_id: str) -> Optional[StageResult]:
        """Get the result of a completed stage."""
        return self._results.get(stage_id)

    def get_all_results(self) -> dict[str, StageResult]:
        """Return all collected stage results."""
        return dict(self._results)

    def skip_dependents(self, stage_id: str) -> None:
        """Skip all stages that depend on the given failed stage."""
        for successor in nx.descendants(self.graph, stage_id):
            if self._statuses[successor] == StageStatus.PENDING:
                self._statuses[successor] = StageStatus.SKIPPED
                logger.info(
                    "Stage %s skipped due to failed dependency %s",
                    successor,
                    stage_id,
                )
