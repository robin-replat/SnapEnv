"""Pipeline service — create and update pipeline runs and their stages.

Called by Celery worker tasks as each stage of a pipeline executes.
The read endpoints (GET /pipelines/{id}) query the DB directly in their
route handlers — no need to go through a service for simple SELECTs.
"""

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.entities import Pipeline, PipelineStage, PipelineStatus, StageStatus, StageType

logger = structlog.get_logger()


async def create_pipeline(
    db: AsyncSession,
    pull_request_id: str,
    commit_sha: str,
    stage_types: list[StageType] | None = None,
) -> Pipeline:
    """Create a new pipeline run for a given PR commit.

    SnapEnv owns only the DEPLOY stage. LINT / TEST / SONARQUBE / BUILD_IMAGE
    are handled by the GitHub Actions workflow in .github/workflows/ci.yml.
    Pass a custom stage_types list to override (e.g. when integrating CI
    status reporting from GitHub Actions in the future).
    """
    if stage_types is None:
        stage_types = [StageType.DEPLOY]

    pipeline = Pipeline(
        pull_request_id=pull_request_id,
        commit_sha=commit_sha,
        status=PipelineStatus.PENDING,
    )
    db.add(pipeline)
    await db.flush()

    stages = [
        PipelineStage(pipeline_id=pipeline.id, stage_type=stage_type, order=order)
        for order, stage_type in enumerate(stage_types, start=1)
    ]
    db.add_all(stages)
    await db.flush()

    logger.info("pipeline_created", pipeline_id=pipeline.id, pr_id=pull_request_id)
    return pipeline


async def start_pipeline(db: AsyncSession, pipeline_id: str) -> Pipeline:
    """Mark a pipeline as running and record its start time."""
    pipeline = await db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ValueError(f"Pipeline {pipeline_id} not found")

    pipeline.status = PipelineStatus.RUNNING
    pipeline.started_at = datetime.now(UTC)
    await db.flush()
    return pipeline


async def start_stage(db: AsyncSession, stage_id: str) -> PipelineStage:
    """Mark a pipeline stage as running."""
    stage = await db.get(PipelineStage, stage_id)
    if stage is None:
        raise ValueError(f"Stage {stage_id} not found")

    stage.status = StageStatus.RUNNING
    stage.started_at = datetime.now(UTC)
    await db.flush()
    return stage


async def complete_stage(
    db: AsyncSession,
    stage_id: str,
    status: StageStatus,
    details: dict[str, Any] | None = None,
) -> PipelineStage:
    """Record the result of a completed (or failed) pipeline stage."""
    stage = await db.get(PipelineStage, stage_id)
    if stage is None:
        raise ValueError(f"Stage {stage_id} not found")

    now = datetime.now(UTC)
    stage.status = status
    stage.details = details
    stage.finished_at = now
    if stage.started_at:
        stage.duration_seconds = int((now - stage.started_at).total_seconds())

    await db.flush()
    logger.info("stage_completed", stage_id=stage_id, status=status.value)
    return stage


async def complete_pipeline(
    db: AsyncSession,
    pipeline_id: str,
    status: PipelineStatus,
) -> Pipeline:
    """Mark a pipeline as finished (success or failed) and compute its duration."""
    pipeline = await db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise ValueError(f"Pipeline {pipeline_id} not found")

    now = datetime.now(UTC)
    pipeline.status = status
    pipeline.finished_at = now
    if pipeline.started_at:
        pipeline.duration_seconds = int((now - pipeline.started_at).total_seconds())

    await db.flush()
    logger.info("pipeline_completed", pipeline_id=pipeline_id, status=status.value)
    return pipeline
