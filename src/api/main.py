"""FastAPI application entrypoint.

This is the main file that creates and configures the FastAPI app.
Run with: uvicorn src.api.main:app --reload
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

from src import __description__, __version__
from src.api.routes import dashboard, events, pipelines, pull_requests
from src.api.routes.websocket import router as websocket_router
from src.models.config import get_settings
from src.models.database import init_db

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle.

    Code before `yield` runs on startup.
    Code after `yield` runs on shutdown.
    Used for initializing/closing connections, warming caches, etc.
    """
    settings = get_settings()
    init_db()
    app.title = settings.app_name
    logger.info("app_starting", app_name=settings.app_name, debug=settings.debug)
    yield
    logger.info("app_shutting_down")


app = FastAPI(
    title="SnapEnv",
    version=__version__,
    description=__description__,
    lifespan=lifespan,
)

# WARNING: CORS: allow the frontend (dashboard) to call the API from a different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instrument the app with Prometheus metrics.
# This adds a /metrics endpoint that Prometheus will scrape.
Instrumentator().instrument(app).expose(app)

# Register route modules.
# Each router handles a group of related endpoints.
# The prefix is prepended to all routes in the router.
# Tags group endpoints in the Swagger documentation.
app.include_router(pull_requests.router, prefix="/api/pull-requests", tags=["pull-requests"])
app.include_router(pipelines.router, prefix="/api/pipelines", tags=["pipelines"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
app.include_router(websocket_router)


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
