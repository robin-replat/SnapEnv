"""Celery application configuration.

Celery is an async task queue. It works like this:
1. The FastAPI app receives a webhook and ENQUEUES a task (fast, non-blocking)
2. Redis holds the task message in a queue
3. A separate Celery worker process PICKS UP the task and executes it
4. The worker can take minutes (deploying to K8s) without blocking the API
"""

from celery import Celery

from src.models.config import get_settings

settings = get_settings()

# Create the Celery app.
# broker: where tasks are queued (Redis)
# backend: where task results are stored (Redis)
celery_app = Celery(
    "snapenv",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    # Serialization: how task arguments are encoded.
    # JSON is human-readable and safe (no arbitrary code execution).
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Task behavior
    task_track_started=True,        # Track when a task starts (not just queued/done)
    task_acks_late=True,            # Acknowledge task AFTER execution (safer for retries)
    worker_prefetch_multiplier=1,   # Take one task at a time (our tasks are long-running)

    # Auto-discover tasks from this module
    task_routes={
        "src.workers.tasks.*": {"queue": "default"},
    },
)

# Auto-discover task modules
celery_app.autodiscover_tasks(["src.workers"])
