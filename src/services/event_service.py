"""Event service — write events to the database and broadcast to WebSocket clients.

Pub/sub architecture
────────────────────
Worker process              API process(es)
─────────────────           ───────────────────────────────────────
create_event()              start_redis_listener() ← background task
  → write to PostgreSQL       subscribes to "snapenv:events" channel
  → publish to Redis          for each message → broadcast_event()
                                → sends to all connected WebSocket clients

Using Redis as the message bus means:
  - The worker never touches WebSocket clients directly (different process).
  - Multiple API replicas all receive and fan-out every event.
  - No code change needed when scaling horizontally.
"""

import asyncio
import json
from typing import Any

import redis.asyncio as aioredis
import structlog
from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.config import get_settings
from src.models.entities import Event, EventType

logger = structlog.get_logger()

REDIS_CHANNEL = "snapenv:events"

# In-memory registry of WebSocket clients connected to THIS API replica.
connected_clients: set[WebSocket] = set()


async def broadcast_event(event_data: dict[str, Any]) -> None:
    """Push an event payload to all WebSocket clients on this API replica."""
    if not connected_clients:
        return

    message = json.dumps(event_data, default=str)
    disconnected: set[WebSocket] = set()

    for ws in connected_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)

    connected_clients.difference_update(disconnected)


async def _publish_to_redis(event_data: dict[str, Any]) -> None:
    """Publish an event to the Redis pub/sub channel.

    Called by create_event() so that all API replicas (via their listener
    tasks) receive the event and fan it out to their WebSocket clients.
    Failures are logged but never propagate — a broadcast failure must not
    roll back the database transaction.
    """
    try:
        client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        await client.publish(REDIS_CHANNEL, json.dumps(event_data, default=str))
        await client.aclose()
    except Exception as exc:
        logger.warning("redis_publish_failed", error=str(exc))


async def start_redis_listener() -> None:
    """Subscribe to Redis and fan-out events to connected WebSocket clients.

    Designed to run as a long-lived background asyncio task inside the API
    lifespan. Automatically reconnects if Redis drops.
    """
    redis_url = get_settings().redis_url

    while True:
        try:
            client = aioredis.from_url(redis_url, decode_responses=True)
            async with client.pubsub() as pubsub:
                await pubsub.subscribe(REDIS_CHANNEL)
                logger.info("redis_listener_ready", channel=REDIS_CHANNEL)

                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        await broadcast_event(json.loads(message["data"]))
                    except Exception as exc:
                        logger.warning("redis_message_error", error=str(exc))

        except asyncio.CancelledError:
            logger.info("redis_listener_stopped")
            return
        except Exception as exc:
            logger.warning("redis_listener_reconnecting", error=str(exc))
            await asyncio.sleep(5)


async def create_event(
    db: AsyncSession,
    event_type: EventType,
    message: str,
    pull_request_id: str | None = None,
    pipeline_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Event:
    """Persist an event and publish it to the Redis channel for dashboard broadcast."""
    event = Event(
        event_type=event_type,
        message=message,
        pull_request_id=pull_request_id,
        pipeline_id=pipeline_id,
        event_metadata=metadata,
    )
    db.add(event)
    await db.flush()

    await _publish_to_redis(
        {
            "id": event.id,
            "event_type": event.event_type.value,
            "message": event.message,
            "pull_request_id": event.pull_request_id,
            "pipeline_id": event.pipeline_id,
            "metadata": event.event_metadata,
            "created_at": str(event.created_at),
        }
    )

    logger.info("event_created", event_type=event_type.value, pr_id=pull_request_id)
    return event
