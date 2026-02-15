"""Dashboard aggregate stats endpoint.

Returns high-level metrics for the dashboard header:
active environments count, success rate, average deploy time, etc.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    Environment,
    EnvironmentStatus,
    Pipeline,
    PipelineStatus,
    PRStatus,
    PullRequest,
)
from src.models.database import get_db
from src.schemas.api import PlatformStats

router = APIRouter()


@router.get("/stats", response_model=PlatformStats)
async def get_platform_stats(
    db: AsyncSession = Depends(get_db),
) -> PlatformStats:
    """Aggregate platform statistics for the dashboard header."""

    # Active environments (status = RUNNING)
    active_envs = (
        await db.scalar(
            select(func.count(Environment.id)).where(Environment.status == EnvironmentStatus.RUNNING)
        )
        or 0
    )

    # PR counts
    total_prs = await db.scalar(select(func.count(PullRequest.id)))
    open_prs = (
        await db.scalar(select(func.count(PullRequest.id)).where(PullRequest.status == PRStatus.OPEN)) or 0
    )

    # Pipelines created today
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    pipelines_today = (
        await db.scalar(select(func.count(Pipeline.id)).where(Pipeline.created_at >= today_start)) or 0
    )

    # Success rate over the last 30 days
    thirty_days_ago = datetime.now(UTC) - timedelta(days=30)
    total_finished = (
        await db.scalar(
            select(func.count(Pipeline.id)).where(
                Pipeline.created_at >= thirty_days_ago,
                Pipeline.status.in_([PipelineStatus.SUCCESS, PipelineStatus.FAILED]),
            )
        )
        or 0
    )
    total_success = (
        await db.scalar(
            select(func.count(Pipeline.id)).where(
                Pipeline.created_at >= thirty_days_ago,
                Pipeline.status == PipelineStatus.SUCCESS,
            )
        )
        or 0
    )
    success_rate = (total_success / total_finished * 100) if total_finished > 0 else 0.0

    # Average deploy time (successful pipelines only)
    avg_duration = await db.scalar(
        select(func.avg(Pipeline.duration_seconds)).where(
            Pipeline.status == PipelineStatus.SUCCESS,
            Pipeline.duration_seconds.isnot(None),
        )
    )

    return PlatformStats(
        active_environments=active_envs,
        total_pull_requests=total_prs,
        open_pull_requests=open_prs,
        pipelines_today=pipelines_today,
        success_rate_percent=round(success_rate, 1),
        avg_deploy_time_seconds=round(avg_duration, 1) if avg_duration else None,
    )
