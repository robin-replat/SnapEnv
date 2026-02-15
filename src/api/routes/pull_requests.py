"""Pull Request API endpoints.

These endpoints power the dashboard's PR list and detail views.
Each endpoint receives a database session via FastAPI's dependency injection
(the `db: AsyncSession = Depends(get_db)` parameter).
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import PRStatus, PullRequest
from src.models.database import get_db
from src.schemas.api import PullRequestListItem, PullRequestResponse

router = APIRouter()


@router.get("", response_model=list[PullRequestListItem])
async def list_pull_requests(
    status_filter: PRStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> list[PullRequestListItem]:
    """List all tracked pull requests with their latest pipeline status.

    Query parameters:
    - status: filter by PR status (open, merged, closed)
    - limit: max number of results (default 50, max 100)
    - offset: pagination offset
    """
    # selectinload tells SQLAlchemy to fetch related objects in a single query
    # instead of making a separate query for each PR's environment/pipelines
    query = (
        select(PullRequest)
        .options(
            selectinload(PullRequest.environment),
            selectinload(PullRequest.pipelines),
        )
        .order_by(PullRequest.updated_at.desc())
        .offset(offset)
        .limit(limit)
    )

    if status_filter:
        query = query.where(PullRequest.status == status_filter)

    result = await db.execute(query)
    prs = result.scalars().all()

    # Build response items with the latest pipeline extracted
    items = []
    for pr in prs:
        latest_pipeline = pr.pipelines[0] if pr.pipelines else None
        items.append(
            PullRequestListItem(
                id=pr.id,
                github_pr_number=pr.github_pr_number,
                repository=pr.repository,
                title=pr.title,
                author=pr.author,
                branch=pr.branch,
                status=pr.status,
                preview_url=pr.preview_url,
                latest_commit_sha=pr.latest_commit_sha,
                created_at=pr.created_at,
                updated_at=pr.updated_at,
                environment=pr.environment,
                latest_pipeline=latest_pipeline,
            )
        )

    return items


@router.get("/{pr_id}", response_model=PullRequestResponse)
async def get_pull_request(
    pr_id: str,
    db: AsyncSession = Depends(get_db),
) -> PullRequestResponse:
    """Get detailed information about a specific pull request.

    Includes the full pipeline history and environment status.
    """
    query = (
        select(PullRequest)
        .options(
            selectinload(PullRequest.environment),
            selectinload(PullRequest.pipelines),
        )
        .where(PullRequest.id == pr_id)
    )

    result = await db.execute(query)
    pr = result.scalar_one_or_none()

    if not pr:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pull request {pr_id} not found",
        )

    return PullRequestResponse.model_validate(pr)
