"""Celery application instance.

The worker pod boots from this module:
  celery -A src.workers.celery_app worker --loglevel=info --concurrency=2 -Q default

Flow:
  1. GitHub sends a webhook to the API (PR opened / updated / closed)
  2. The API enqueues a Celery task and returns 200 immediately
  3. This worker picks up the task from Redis and runs the pipeline stages
     (lint, test, build image, deploy via ArgoCD) asynchronously
  4. After each stage, the worker writes an Event to the database and the
     API broadcasts it to connected dashboard WebSocket clients
"""

from celery import Celery

from src.models.config import get_settings


def _make_celery() -> Celery:
    settings = get_settings()

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
