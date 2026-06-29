"""FastAPI application entrypoint.

This is the main file that creates and configures the FastAPI app.
Run with: uvicorn src.api.main:app --reload
"""

import asyncio
from collections.abc import AsyncGenerator, MutableMapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import prometheus_fastapi_instrumentator.routing as instrumentator_routing
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.routing import Route

from src import __description__, __version__
from src.api.routes import dashboard, events, pipelines, pull_requests, webhooks
from src.models.config import get_settings
from src.models.database import init_db
from src.services.event_service import start_redis_listener

logger = structlog.get_logger()


def _patch_instrumentator_route_matching() -> None:
    """Ignore Starlette private router sentinels that instrumentator cannot parse."""
    original_get_route_name = instrumentator_routing._get_route_name

    def patched_get_route_name(
        scope: MutableMapping[str, Any],
        routes: list[Route],
        route_name: str | None = None,
    ) -> str | None:
        routable_items = [route for route in routes if hasattr(route, "path")]
        return original_get_route_name(scope, routable_items, route_name)

    instrumentator_routing._get_route_name = patched_get_route_name


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown.

    On startup, initialize application settings, prepare the database, and start
    the Redis Pub/Sub listener used to fan out worker events to WebSocket clients.

    On shutdown, cancel the Redis listener gracefully so the application exits
    without leaving background tasks running.
    """
    settings = get_settings()
    init_db()
    app.title = settings.app_name
    logger.info("app_starting", app_name=settings.app_name, debug=settings.debug)

    # Bridge: worker publishes to Redis → listener fans out to WebSocket clients.
    listener = asyncio.create_task(start_redis_listener())
    yield

    listener.cancel()
    await asyncio.gather(listener, return_exceptions=True)
    logger.info("app_shutting_down")


app = FastAPI(
    title="SnapEnv",
    version=__version__,
    description=__description__,
    lifespan=lifespan,
)

# WARNING: CORS: allow the frontend to call the API from a different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_patch_instrumentator_route_matching()
Instrumentator().instrument(app).expose(app)

# Register route modules.
# Each router handles a group of related endpoints.
# The prefix is prepended to all routes in the router.
# Tags group endpoints in the Swagger documentation.
app.include_router(pull_requests.router, prefix="/api/pull-requests", tags=["pull-requests"])
app.include_router(pipelines.router, prefix="/api/pipelines", tags=["pipelines"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
app.include_router(webhooks.router, prefix="/api/webhooks", tags=["webhooks"])


# Mount static files directory:
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Health check endpoint.

    Used by:
    - Docker HEALTHCHECK to know if the container is alive
    - Kubernetes readiness/liveness probes
    - Load balancers to route traffic only to healthy instances
    """
    return {"status": "healthy"}


# Route that serves the dashboard at the root:
@app.get("/", include_in_schema=False)
async def serve_dashboard() -> FileResponse:
    return FileResponse(str(static_dir / "dashboard.html"))
