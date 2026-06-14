"""Event stream endpoints (REST + WebSocket).

The REST endpoint returns historical events.
The WebSocket endpoint streams events in real time to the dashboard.

broadcast_event and the connected_clients registry live in
src/services/event_service so the Celery worker can call them
without importing from the API route layer.
"""

import asyncio

import structlog
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Event
from src.models.database import get_db
from src.schemas.api import EventResponse
from src.services.event_service import connected_clients

logger = structlog.get_logger()
router = APIRouter()


@router.get("", response_model=list[EventResponse])
async def list_events(
    limit: int = Query(50, ge=1, le=200),
    pull_request_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[EventResponse]:
    """Get recent events, optionally filtered by pull request."""
    query = select(Event).order_by(Event.created_at.desc()).limit(limit)

    if pull_request_id:
        query = query.where(Event.pull_request_id == pull_request_id)

    result = await db.execute(query)
    events = result.scalars().all()

    return [EventResponse.model_validate(e) for e in events]


@router.websocket("/ws")
async def event_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time event streaming.

    The dashboard connects here and receives live updates
    as events are created (PR opened, pipeline stage completed, etc.).
    """
    await websocket.accept()
    connected_clients.add(websocket)
    logger.info("websocket_connected", total_clients=len(connected_clients))

    try:
        while True:
            await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
    except (TimeoutError, WebSocketDisconnect):
        pass
    finally:
        connected_clients.discard(websocket)
        logger.info("websocket_disconnected", total_clients=len(connected_clients))
