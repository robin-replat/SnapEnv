"""SQLAlchemy models for the Preview Platform.

Each class below corresponds to a table in PostgreSQL.

Data architecture:
─────────────────────────
PullRequest (the GitHub PR we track)
  ├── 1:N Pipeline (each push on the PR triggers a pipeline run)
  │     └── 1:N PipelineStage (stages: lint, test, sonar, build, deploy)
  ├── 1:1 Environment (the ephemeral preview environment)
  └── 1:N Event (event log for the real-time dashboard)
"""

import enum
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ──────────────────────────────────────────────────────────────
# Base
# ──────────────────────────────────────────────────────────────


class Base(DeclarativeBase):
    """Base class that all models inherit from.

    All models inheriting from it are automatically
    registered in Base.metadata. This is what Alembic uses
    to detect tables to create/modify.
    """

    # It tells SQLAlchemy: when you see the Python type `dict`, use the PostgreSQL type JSONB
    type_annotation_map = {
        dict[str, Any]: JSONB,
    }


# ──────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────


class PRStatus(str, enum.Enum):
    """Possible states of a Pull Request."""

    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"


class PipelineStatus(str, enum.Enum):
    """Possible states of a pipeline run."""

    PENDING = "pending"  # Waiting to start
    RUNNING = "running"  # Currently running
    SUCCESS = "success"  # All stages succeeded
    FAILED = "failed"  # At least one stage failed
    CANCELLED = "cancelled"  # Cancelled (PR closed during run)


class StageType(str, enum.Enum):
    """Types of stages in a pipeline.

    Each pipeline goes through these stages in order.
    """

    LINT = "lint"  # Code style check
    TEST = "test"  # Run tests
    SONARQUBE = "sonarqube"  # Quality analysis
    BUILD_IMAGE = "build_image"  # Build Docker image
    DEPLOY = "deploy"  # Deploy to K8s via ArgoCD


class StageStatus(str, enum.Enum):
    """Possible states of a pipeline stage."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"  # Stage skipped (e.g., deploy skipped if tests fail)


class EnvironmentStatus(str, enum.Enum):
    """Possible states of a preview environment."""

    PROVISIONING = "provisioning"  # Being created
    RUNNING = "running"  # Accessible and functional
    DEGRADED = "degraded"  # Partially functional
    DESTROYING = "destroying"  # Being destroyed
    DESTROYED = "destroyed"  # Destroyed
    FAILED = "failed"  # Creation failed


class EventType(str, enum.Enum):
    """Event types for the real-time dashboard stream."""

    PR_OPENED = "pr_opened"
    PR_UPDATED = "pr_updated"
    PR_CLOSED = "pr_closed"
    PR_MERGED = "pr_merged"
    PR_REOPENED = "pr_reopened"
    PIPELINE_STARTED = "pipeline_started"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    DEPLOY_STARTED = "deployed_started"
    ENV_PROVISIONING = "env_provisioning"
    ENV_READY = "env_ready"
    ENV_DESTROYING = "env_destroying"
    ENV_DESTROYED = "env_destroyed"
    ENV_FAILED = "env_failed"


# ──────────────────────────────────────────────────────────────
# Models (Tables)
# ──────────────────────────────────────────────────────────────


class PullRequest(Base):
    """Represents a GitHub Pull Request tracked by the platform.

    This is the central entity: everything (pipelines, environments, events)
    is linked to a PullRequest.

    Columns explained:
    - mapped_column() defines a column with its SQL properties
    - Mapped[str] defines the Python type (for mypy and IDE)
    - Together they give a type-safe column on both sides
    """

    __tablename__ = "pull_requests"  # Table name in PostgreSQL

    # ── Primary key ──
    # We use UUIDs instead of auto-increment (1, 2, 3...)
    # Advantages: no collision when merging databases, no info
    # about total records, works in distributed setups.
    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid4()),  # Automatically generates a UUID
    )

    # ── PR data ──
    # index=True to create a SQL index for fast searches by PR number
    github_pr_number: Mapped[int] = mapped_column(Integer, index=True)

    repository: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    author: Mapped[str] = mapped_column(String(100))
    branch: Mapped[str] = mapped_column(String(255))
    base_branch: Mapped[str] = mapped_column(String(255), default="main")

    # Enum(PRStatus) creates a PostgreSQL ENUM type with values "open", "merged", "closed"
    status: Mapped[PRStatus] = mapped_column(Enum(PRStatus), default=PRStatus.OPEN, index=True)

    # str | None = this column can be NULL (no URL before deployment)
    preview_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    github_url: Mapped[str] = mapped_column(String(500))
    # A Git SHA is always 40 hexadecimal characters
    latest_commit_sha: Mapped[str] = mapped_column(String(40))

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),  # PostgreSQL generates the date (not Python)
    )
    # server_default=func.now() → default value is computed by PostgreSQL

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),  # Automatically updated on each UPDATE
    )

    # ── Relationships ──

    pipelines: Mapped[list["Pipeline"]] = relationship(
        back_populates="pull_request",
        cascade="all, delete-orphan",  # If PR is deleted, its pipelines are deleted
        order_by="Pipeline.created_at.desc()",  # Sorted by descending creation date
    )

    environment: Mapped["Environment | None"] = relationship(
        back_populates="pull_request",
        cascade="all, delete-orphan",
        uselist=False,  # 1:1 relationship (one PR → one environment)
    )

    events: Mapped[list["Event"]] = relationship(
        back_populates="pull_request",
        cascade="all, delete-orphan",
        order_by="Event.created_at.desc()",
    )

    def __repr__(self) -> str:
        """Readable representation for debugging."""
        return f"<PullRequest #{self.github_pr_number} [{self.status.value}]>"


class Pipeline(Base):
    """A CI/CD pipeline run triggered by a commit on a PR.

    Each push on a PR creates a new Pipeline.
    A Pipeline contains multiple PipelineStage (lint, test, etc.).
    """

    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))

    # ── Foreign key ──
    # Links this pipeline to a PullRequest.
    # ForeignKey("pull_requests.id") references the "id" column of the "pull_requests" table
    # ondelete="CASCADE": if the PR is deleted, its pipelines are also deleted
    pull_request_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        index=True,
    )

    commit_sha: Mapped[str] = mapped_column(String(40))
    status: Mapped[PipelineStatus] = mapped_column(
        Enum(PipelineStatus), default=PipelineStatus.PENDING, index=True
    )

    # GitHub Actions workflow run ID (for polling)
    github_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # ── Relationships ──
    # back_populates="pipelines" means: on the PullRequest side,
    # this relationship is called "pipelines"
    pull_request: Mapped["PullRequest"] = relationship(back_populates="pipelines")

    stages: Mapped[list["PipelineStage"]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineStage.order",  # Ordered by execution sequence
    )

    events: Mapped[list["Event"]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<Pipeline {self.id[:8]} [{self.status.value}]>"


class PipelineStage(Base):
    """An individual step in a pipeline run.

    Examples of steps: lint, test, sonarqube, build_image, deploy.
    Each step has its own status and can store specific results
    in the `details` field (JSONB).
    """

    __tablename__ = "pipeline_stages"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    pipeline_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("pipelines.id", ondelete="CASCADE"),
        index=True,
    )

    stage_type: Mapped[StageType] = mapped_column(Enum(StageType))
    status: Mapped[StageStatus] = mapped_column(Enum(StageStatus), default=StageStatus.PENDING)
    # order = position in the pipeline (1=lint, 2=test, 3=sonar, etc.)
    order: Mapped[int] = mapped_column(Integer)

    # ── JSONB ──
    # JSONB is a PostgreSQL type that stores indexable JSON.
    # Each step type stores different results:
    #   lint:       {"errors": 0, "warnings": 2, "tool": "ruff"}
    #   test:       {"passed": 42, "failed": 0, "coverage": 87.3}
    #   sonarqube:  {"bugs": 0, "vulnerabilities": 1, "gate": "passed"}
    #   build:      {"image_tag": "pr-42-abc1234", "size_mb": 145}
    #   deploy:     {"namespace": "pr-42", "argocd_app": "preview-pr-42"}
    # It's more flexible than a column per result (we wouldn't have the
    # same columns for lint and deploy).
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationship ──
    pipeline: Mapped["Pipeline"] = relationship(back_populates="stages")

    def __repr__(self) -> str:
        return f"<PipelineStage {self.stage_type.value} [{self.status.value}]>"


class Environment(Base):
    """A temporary preview environment associated with a PR.

    When a PR is opened, a dedicated Kubernetes namespace is created
    with the deployed application, accessible via a unique URL
    (e.g., pr-42.preview.yourdomain.dev).
    """

    __tablename__ = "environments"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    pull_request_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        unique=True,  # Only one environment per PR (1:1 relation)
        index=True,
    )

    # 63 characters = Kubernetes namespace name limit
    namespace: Mapped[str] = mapped_column(String(63), unique=True)

    url: Mapped[str] = mapped_column(String(500))
    status: Mapped[EnvironmentStatus] = mapped_column(
        Enum(EnvironmentStatus),
        default=EnvironmentStatus.PROVISIONING,
        index=True,
    )
    argocd_app_name: Mapped[str] = mapped_column(String(255))

    # ── K8s resource tracking ──
    # Stores CPU/memory limits requested for this environment.
    cpu_request: Mapped[str | None] = mapped_column(String(20), nullable=True)
    memory_request: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cpu_limit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    memory_limit: Mapped[str | None] = mapped_column(String(20), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    destroyed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relationship ──
    pull_request: Mapped["PullRequest"] = relationship(back_populates="environment")

    def __repr__(self) -> str:
        return f"<Environment {self.namespace} [{self.status.value}]>"


class Event(Base):
    """Timestamped event for the dashboard real-time feed.

    Each significant action creates an Event:
    - PR opened, updated, closed
    - Pipeline started, step succeeded/failed
    - Environment created, ready, destroyed

    These events feed the dashboard WebSocket
    so that visitors see changes in real time.
    """

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), index=True)
    # Text = SQL TEXT (no length limit, unlike VARCHAR)
    message: Mapped[str] = mapped_column(Text)

    # Additional metadata (flexible, like details in PipelineStage)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # ── Optional foreign keys ──
    # An event can be linked to a PR, a pipeline, or both.
    # nullable=True because some events are global.
    pull_request_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("pull_requests.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    pipeline_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("pipelines.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,  # Indexed because often sorted by date
    )

    # ── Relationships ──
    pull_request: Mapped["PullRequest | None"] = relationship(back_populates="events")
    pipeline: Mapped["Pipeline | None"] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"<Event {self.event_type.value} @ {self.created_at}>"
