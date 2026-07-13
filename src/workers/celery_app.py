"""Celery application instance.

The worker pod boots from this module:
  celery -A src.workers.celery_app worker --loglevel=info --concurrency=2 -Q default

Flow:
  1. GitHub sends a webhook to the API (PR opened / updated / closed)
  2. The API enqueues a Celery task and returns 200 immediately
  3. This worker picks up the task from Redis and runs the DEPLOY stage:
     calls ArgoCD to create a preview namespace, posts the URL to GitHub
  4. The worker writes Events to the database; the API broadcasts them
     to connected dashboard WebSocket clients
"""

import structlog
from celery import Celery
from celery.signals import worker_ready
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class CelerySettings(BaseSettings):
    """Minimal import-time settings for Celery wiring.

    Importing FastAPI routes imports task objects so webhook handlers can enqueue
    work. That path must not validate the full application settings, because test
    collection may override DB dependencies and never touch PostgreSQL.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    redis_host: str = "localhost"
    redis_port: int = 6379

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"


@worker_ready.connect  # type: ignore[untyped-decorator]
def _on_worker_ready(**_kwargs: object) -> None:
    logger.info("worker_ready")


def _make_celery() -> Celery:
    settings = CelerySettings()

    app = Celery(
        "snapenv",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=["src.workers.tasks"],
    )

    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_default_queue="default",
        # Acknowledge the task only after it finishes, not when it starts.
        # If the worker crashes mid-task, Redis re-queues it automatically.
        task_acks_late=True,
        worker_prefetch_multiplier=1,
    )

    return app


celery_app = _make_celery()
