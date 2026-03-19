import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AgentType(str, Enum):
    BUILD = "build"
    TEST = "test"
    SECURITY = "security"
    DEPLOY = "deploy"
    VERIFY = "verify"


class Stage(BaseModel):
    id: str
    agent: AgentType
    command: str
    depends_on: list[str] = []
    timeout_seconds: int = 300
    retry_count: int = 0
    critical: bool = True
    env_vars: dict[str, str] = {}


class RepoAnalysis(BaseModel):
    language: str
    framework: Optional[str] = None
    package_manager: str
    has_dockerfile: bool = False
    has_requirements_txt: bool = False
    has_yarn_lock: bool = False
    has_package_lock: bool = False
    has_tests: bool = False
    test_runner: Optional[str] = None
    is_monorepo: bool = False
    deploy_target: Optional[str] = None  # e.g., "aws", "gcp", "azure", "docker", "heroku", "kubernetes", "staging", "production"
    available_scripts: list[str] = []  # npm/yarn scripts found in package.json
    has_test_extras: bool = False  # Python: has [dev], [test], or [testing] extras


class PipelineSpec(BaseModel):
    pipeline_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    repo_url: str = ""
    goal: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    analysis: RepoAnalysis
    stages: list[Stage]
    work_dir: str = ""
    use_docker: bool = False
