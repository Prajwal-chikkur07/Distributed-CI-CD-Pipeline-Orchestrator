import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class PipelineRow(Base):
    __tablename__ = "pipelines"

    pipeline_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False, default="")
    repo_url = Column(String, nullable=False, default="")
    goal = Column(String, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    work_dir = Column(String, default="")
    # Store full PipelineSpec as JSON (analysis + stages)
    spec_json = Column(Text, nullable=False)

    results = relationship("StageResultRow", back_populates="pipeline", cascade="all, delete-orphan")


class StageResultRow(Base):
    __tablename__ = "stage_results"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id = Column(String, ForeignKey("pipelines.pipeline_id"), nullable=False)
    stage_id = Column(String, nullable=False)
    status = Column(String, nullable=False)
    exit_code = Column(String, default="-1")
    stdout = Column(Text, default="")
    stderr = Column(Text, default="")
    duration_seconds = Column(String, default="0.0")
    result_json = Column(Text, nullable=False)

    pipeline = relationship("PipelineRow", back_populates="results")
