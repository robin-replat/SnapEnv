"""Pipeline API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import Pipeline
from src.models.database import get_db
from src.schemas.api import PipelineResponse

router = APIRouter()


@router.get("/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(
    pipeline_id: str,
    db: AsyncSession = Depends(get_db),
) -> PipelineResponse:
    """Get detailed pipeline information including all stages and their results."""
    query = (
        select(Pipeline)
        .options(selectinload(Pipeline.stages))
        .where(Pipeline.id == pipeline_id)
    )

    result = await db.execute(query)
    pipeline = result.scalar_one_or_none()

    if not pipeline:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline {pipeline_id} not found",
        )

    return PipelineResponse.model_validate(pipeline)