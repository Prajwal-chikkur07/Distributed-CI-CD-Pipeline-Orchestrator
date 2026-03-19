import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.api.websocket import manager
from src.creator.analyzer import analyze_repo
from src.creator.generator import generate_pipeline
from src.db import repository as db
from src.db.session import init_db
from src.executor.dispatcher import run_pipeline
from src.models.messages import StageResult
from src.models.pipeline import PipelineSpec, Stage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="CI/CD Pipeline Orchestrator")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield

app = FastAPI(title="CI/CD Pipeline Orchestrator", lifespan=lifespan)


@app.get("/pipelines")
async def list_pipelines() -> list[dict]:
    """List all pipelines with their execution results."""
    specs = await db.list_pipelines()
    history = []
    for spec in specs:
        results = await db.get_results(spec.pipeline_id)
        overall = "success"
        if results:
            has_failed = any(r.status.value == "failed" for r in results.values())
            overall = "failed" if has_failed else "success"
        history.append({
            "pipeline": spec.model_dump(mode="json"),
            "results": {k: v.model_dump(mode="json") for k, v in results.items()} if results else None,
            "completedAt": spec.created_at.isoformat(),
            "overallStatus": overall,
        })
    return history


@app.post("/pipelines")
async def create_pipeline(
    repo_url: str = Query(..., description="Git repository URL"),
    goal: str = Query(..., description="Deployment goal"),
    use_docker: bool = Query(False, description="Run stages in Docker containers"),
    name: str = Query("", description="Pipeline name"),
) -> PipelineSpec:
    """Analyze a repository and generate a pipeline spec."""
    repo_url = repo_url.strip()
    goal = goal.strip()
    analysis, clone_dir = await analyze_repo(repo_url, goal=goal)
    spec = await generate_pipeline(analysis, goal, repo_url=repo_url)
    spec.name = name.strip()
    spec.work_dir = clone_dir
    spec.use_docker = use_docker
    await db.save_pipeline(spec)
    logger.info("Created pipeline %s (%s) for %s (work_dir=%s)", spec.pipeline_id, spec.name, repo_url, clone_dir)
    return spec


@app.post("/pipelines/{pipeline_id}/execute")
async def execute_pipeline(pipeline_id: str) -> dict[str, StageResult]:
    """Execute a generated pipeline."""
    spec = await db.get_pipeline(pipeline_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    async def on_update(data: dict) -> None:
        await manager.broadcast(pipeline_id, data)

    result = await run_pipeline(spec, working_dir=spec.work_dir or ".", on_update=on_update)
    await db.save_results(pipeline_id, result)
    return result


@app.get("/pipelines/{pipeline_id}")
async def get_pipeline(pipeline_id: str) -> PipelineSpec:
    """Get a pipeline spec by ID."""
    spec = await db.get_pipeline(pipeline_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return spec


class PipelineUpdate(BaseModel):
    name: str | None = None
    goal: str | None = None
    stages: list[Stage] | None = None


@app.patch("/pipelines/{pipeline_id}")
async def update_pipeline(pipeline_id: str, update: PipelineUpdate) -> PipelineSpec:
    """Update a pipeline's name, goal, or stage commands."""
    spec = await db.get_pipeline(pipeline_id)
    if not spec:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    if update.name is not None:
        spec.name = update.name
    if update.goal is not None:
        spec.goal = update.goal
    if update.stages is not None:
        spec.stages = update.stages
    updated = await db.update_pipeline(spec)
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update pipeline")
    logger.info("Updated pipeline %s", pipeline_id)
    return spec


@app.delete("/pipelines/{pipeline_id}")
async def delete_pipeline(pipeline_id: str) -> dict[str, str]:
    """Delete a pipeline and its results."""
    deleted = await db.delete_pipeline(pipeline_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pipeline not found")
    return {"status": "deleted"}


@app.get("/pipelines/{pipeline_id}/results")
async def get_results(pipeline_id: str) -> dict[str, StageResult]:
    """Get execution results for a pipeline."""
    result = await db.get_results(pipeline_id)
    if not result:
        raise HTTPException(status_code=404, detail="Results not found")
    return result


@app.websocket("/ws/{pipeline_id}")
async def websocket_endpoint(websocket: WebSocket, pipeline_id: str) -> None:
    """WebSocket endpoint for real-time pipeline status updates."""
    await manager.connect(websocket, pipeline_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, pipeline_id)


if __name__ == "__main__":
    uvicorn.run("src.api.main:app", host="0.0.0.0", port=8001, reload=True)
