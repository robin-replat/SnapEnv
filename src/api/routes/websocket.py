"""WebSocket endpoint for real-time event streaming.

The dashboard connects to this WebSocket to receive live updates
as PR events, deployments, and environment changes happen.

Architecture:
  Webhook arrives → DB write + Event created → broadcast to WebSocket clients
  Celery task completes → DB write + Event created → broadcast to WebSocket clients

We use an in-memory ConnectionManager that holds active WebSocket connections.
When an event is created anywhere in the system, it calls broadcast()
to push the event to all connected dashboard clients.

For a single-server setup this works perfectly. For multi-server production,
you'd replace this with Redis Pub/Sub (the infrastructure is already there
since we use Redis for Celery).
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()


class ConnectionManager:
    """Manages active WebSocket connections.

    Holds a list of connected clients and provides methods
    to broadcast messages to all of them simultaneously.
    """

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(
            "WebSocket connected. Active connections: %d",
            len(self.active_connections),
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected client."""
        self.active_connections.remove(websocket)
        logger.info(
            "WebSocket disconnected. Active connections: %d",
            len(self.active_connections),
        )

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send a message to all connected clients.

        If a client has disconnected unexpectedly, we catch the error
        and remove it from the list (cleanup on next broadcast).
        """
        disconnected: list[WebSocket] = []

        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        # Clean up dead connections
        for conn in disconnected:
            self.active_connections.remove(conn)


# Singleton instance — imported by other modules to broadcast events
manager = ConnectionManager()


@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    """WebSocket endpoint for the real-time event feed.

    The dashboard connects here and receives JSON messages like:
    {
        "event_type": "PR_OPENED",
        "message": "PR #42 opened: feat: add widget",
        "pr_number": 42,
        "timestamp": "2026-03-01T14:30:00Z"
    }

    The connection stays open until the client disconnects.
    """
    await manager.connect(websocket)
    try:
        # Keep the connection alive.
        # We don't expect messages FROM the client, but we need to
        # keep receiving to detect disconnection.
        while True:
            # Wait for any message (ping/pong or close frame)
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def broadcast_event(
    event_type: str,
    message: str,
    pr_number: int | None = None,
    preview_url: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Broadcast an event to all connected dashboard clients.

    Call this from anywhere in the codebase when something interesting happens.
    The WebSocket manager sends it to all connected browsers.
    """
    payload: dict[str, Any] = {
        "event_type": event_type,
        "message": message,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    if pr_number is not None:
        payload["pr_number"] = pr_number
    if preview_url is not None:
        payload["preview_url"] = preview_url
    if extra:
        payload.update(extra)

    await manager.broadcast(payload)
