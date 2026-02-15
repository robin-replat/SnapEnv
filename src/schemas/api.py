"""Pydantic schemas for API request/response serialization.

These schemas define the shape of data going in and out of the API.
FastAPI uses them to:
- Validate incoming request data automatically
- Serialize outgoing response data (Python objects → JSON)
- Generate the OpenAPI/Swagger documentation

Naming convention:
- *Response: returned by GET endpoints
- *Create: accepted by POST endpoints (future use)
- *Summary: lightweight version for list endpoints
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.models.entities import (
    EnvironmentStatus,
    EventType,
    PipelineStatus,
    PRStatus,
    StageStatus,
    StageType,
)


# ──────────────────────────────────────────────
# Pipeline Stage
# ──────────────────────────────────────────────


class PipelineStageResponse(BaseModel):
    """Full pipeline stage details, including results."""

    # from_attributes=True tells Pydantic to read data from SQLAlchemy model
    # attributes (e.g., stage.stage_type) rather than expecting a dict.
    # This is what makes PipelineStageResponse.model_validate(stage) work.
    model_config = ConfigDict(from_attributes=True)

    id: str
    stage_type: StageType
    status: StageStatus
    order: int
    details: dict | None = None
    duration_seconds: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


# ──────────────────────────────────────────────
# Pipeline
# ──────────────────────────────────────────────


class PipelineResponse(BaseModel):
    """Full pipeline details with all stages."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    commit_sha: str
    status: PipelineStatus
    duration_seconds: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    stages: list[PipelineStageResponse] = []


class PipelineSummary(BaseModel):
    """Lightweight pipeline info for list views (no stages)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    commit_sha: str
    status: PipelineStatus
    duration_seconds: int | None = None
    created_at: datetime


# ──────────────────────────────────────────────
# Environment
# ──────────────────────────────────────────────


class EnvironmentResponse(BaseModel):
    """Full environment details."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    namespace: str
    url: str
    status: EnvironmentStatus
    argocd_app_name: str
    cpu_request: str
    memory_request: str
    cpu_limit: str
    memory_limit: str
    created_at: datetime
    destroyed_at: datetime | None = None


# ──────────────────────────────────────────────
# Pull Request
# ──────────────────────────────────────────────


class PullRequestResponse(BaseModel):
    """Full PR details with environment and pipeline history."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    github_pr_number: int
    repository: str
    title: str
    author: str
    branch: str
    base_branch: str
    status: PRStatus
    preview_url: str | None = None
    github_url: str
    latest_commit_sha: str
    created_at: datetime
    updated_at: datetime
    environment: EnvironmentResponse | None = None
    pipelines: list[PipelineSummary] = []


class PullRequestListItem(BaseModel):
    """PR summary for list endpoints (latest pipeline only)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    github_pr_number: int
    repository: str
    title: str
    author: str
    branch: str
    status: PRStatus
    preview_url: str | None = None
    latest_commit_sha: str
    created_at: datetime
    updated_at: datetime
    environment: EnvironmentResponse | None = None
    latest_pipeline: PipelineSummary | None = None


# ──────────────────────────────────────────────
# Event
# ──────────────────────────────────────────────


class EventResponse(BaseModel):
    """Event data for the real-time feed."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: EventType
    message: str
    metadata: dict | None = None
    pull_request_id: str | None = None
    pipeline_id: str | None = None
    created_at: datetime


# ──────────────────────────────────────────────
# Dashboard Stats
# ──────────────────────────────────────────────


class PlatformStats(BaseModel):
    """Aggregate metrics for the dashboard header."""

    active_environments: int
    total_pull_requests: int
    open_pull_requests: int
    pipelines_today: int
    success_rate_percent: float
    avg_deploy_time_seconds: float | None = None