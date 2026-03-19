import logging

import networkx as nx

from src.creator.llm_generator import generate_with_llm
from src.creator.templates.go import generate_go_pipeline
from src.creator.templates.java import generate_java_pipeline
from src.creator.templates.nodejs import generate_nodejs_pipeline
from src.creator.templates.python_tmpl import generate_python_pipeline
from src.creator.templates.rust import generate_rust_pipeline
from src.models.pipeline import PipelineSpec, RepoAnalysis, Stage

logger = logging.getLogger(__name__)

from typing import Callable

TEMPLATE_MAP: dict[str, Callable] = {
    "javascript": generate_nodejs_pipeline,
    "typescript": generate_nodejs_pipeline,
    "python": generate_python_pipeline,
    "go": generate_go_pipeline,
    "java": generate_java_pipeline,
    "rust": generate_rust_pipeline,
}


def _validate_dag(stages: list[Stage]) -> None:
    """Validate that stage dependencies form a valid DAG (no cycles)."""
    graph = nx.DiGraph()
    stage_ids = {s.id for s in stages}

    for stage in stages:
        graph.add_node(stage.id)
        for dep in stage.depends_on:
            if dep not in stage_ids:
                raise ValueError(
                    f"Stage '{stage.id}' depends on unknown stage '{dep}'"
                )
            graph.add_edge(dep, stage.id)

    if not nx.is_directed_acyclic_graph(graph):
        cycles = list(nx.simple_cycles(graph))
        raise ValueError(f"Pipeline has circular dependencies: {cycles}")


async def generate_pipeline(
    analysis: RepoAnalysis, goal: str, repo_url: str = ""
) -> PipelineSpec:
    """Generate a complete pipeline spec from repo analysis.

    Uses built-in templates for known languages, falls back to LLM
    for unknown project types.
    """
    template_fn = TEMPLATE_MAP.get(analysis.language)

    if template_fn:
        logger.info("Using %s template for pipeline generation", analysis.language)
        stages = template_fn(analysis, goal)
    else:
        logger.info(
            "No template for language=%s, falling back to LLM", analysis.language
        )
        stages = await generate_with_llm(analysis, goal)

    _validate_dag(stages)
    logger.info("DAG validation passed with %d stages", len(stages))

    return PipelineSpec(
        repo_url=repo_url,
        goal=goal,
        analysis=analysis,
        stages=stages,
    )
