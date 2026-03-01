"""Celery tasks for managing preview environments.

Each task represents a unit of work that runs asynchronously in the worker.
Tasks are enqueued by the FastAPI webhook handler and executed here.

Task flow:
  PR opened/synchronized → process_pr_event
                              ├→ deploy_preview_environment
                              └→ poll_pipeline_status

  PR closed/merged → destroy_preview_environment
"""

import logging
from datetime import UTC, datetime
from typing import Any

from celery.exceptions import Retry
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.config import get_settings
from src.models.entities import (
    Environment,
    EnvironmentStatus,
    Event,
    EventType,
    PullRequest,
)
from src.services.argocd import ArgocdService
from src.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_sync_engine: Engine | None = None
_sync_session_factory: sessionmaker[Session] | None = None


def get_sync_session_factory() -> sessionmaker[Session]:
    """Return a singleton sync session factory for Celery workers.

    Celery workers are long-lived processes. Reusing one engine/factory per
    process avoids repeatedly creating connection pools on every task call.
    """
    global _sync_engine, _sync_session_factory  # noqa: PLW0603

    if _sync_session_factory is None:
        settings = get_settings()
        _sync_engine = create_engine(
            settings.database_url_sync,
            pool_pre_ping=True,
        )
        _sync_session_factory = sessionmaker(
            bind=_sync_engine,
            expire_on_commit=False,
        )

    return _sync_session_factory


def get_sync_session() -> Session:
    """Create a synchronous database session for Celery tasks.

    Celery tasks run in a synchronous context (not async), so we need
    a sync SQLAlchemy session instead of the async one used by FastAPI.
    """
    session_factory = get_sync_session_factory()
    return session_factory()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=30)  # type: ignore[untyped-decorator]
def process_pr_event(self: Any, pr_id: str, action: str) -> dict[str, str]:
    """Process a GitHub PR event.

    This is the entry point task, triggered by the webhook handler.
    It dispatches to the appropriate sub-task based on the action.

    Args:
        pr_id: UUID of the PullRequest record in our database
        action: GitHub webhook action (opened, synchronize, closed, etc.)
    """
    logger.info("Processing PR event: pr_id=%s action=%s", pr_id, action)

    try:
        if action in ("opened", "synchronize", "reopened"):
            deploy_preview_environment.delay(pr_id)
        elif action == "closed":
            destroy_preview_environment.delay(pr_id)
        else:
            logger.info("Ignoring unhandled action: %s", action)

        return {"status": "dispatched", "pr_id": pr_id, "action": action}

    except Exception as exc:
        logger.error("Failed to process PR event: %s", exc)
        raise self.retry(exc=exc) from exc


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)  # type: ignore[untyped-decorator]
def deploy_preview_environment(self: Any, pr_id: str) -> dict[str, str]:
    """Deploy a preview environment for a pull request.

    Steps:
    1. Fetch the PR from the database
    2. Create or update the ArgoCD Application
    3. Create or update the Environment record
    4. Log an event for the dashboard
    """
    logger.info("Deploying preview environment for PR: %s", pr_id)
    settings = get_settings()
    session = get_sync_session()

    try:
        # Fetch the PR
        pr = session.execute(select(PullRequest).where(PullRequest.id == pr_id)).scalar_one_or_none()

        if not pr:
            logger.error("PullRequest not found: %s", pr_id)
            return {"status": "error", "message": "PR not found"}

        pr_number = pr.github_pr_number
        app_name = f"snapenv-pr-{pr_number}"
        namespace = f"pr-{pr_number}"
        preview_url = f"https://snapenv-pr-{pr_number}.{settings.preview_domain}"

        # Create the ArgoCD Application
        argocd = ArgocdService()
        argocd.create_or_update_application(
            app_name=app_name,
            namespace=namespace,
            repo_url=f"https://github.com/{settings.github_repository}.git",
            chart_path=settings.helm_chart_path,
            image_tag=pr.latest_commit_sha[:7] if pr.latest_commit_sha else "latest",
            pr_number=pr_number,
        )

        # Create or update the Environment record
        env = session.execute(
            select(Environment).where(Environment.pull_request_id == pr.id)
        ).scalar_one_or_none()

        if env:
            env.status = EnvironmentStatus.PROVISIONING
            env.url = preview_url
        else:
            env = Environment(
                pull_request_id=pr.id,
                namespace=namespace,
                url=preview_url,
                status=EnvironmentStatus.PROVISIONING,
                argocd_app_name=app_name,
            )
            session.add(env)

        # Update PR with preview URL
        pr.preview_url = preview_url

        # Log the event
        session.add(
            Event(
                event_type=EventType.DEPLOY_STARTED,
                message=f"Deploying preview environment for PR #{pr_number}",
                pull_request_id=pr.id,
            )
        )

        session.commit()

        # Start polling ArgoCD for deployment status
        poll_deployment_status.delay(pr_id, app_name)

        logger.info("Preview environment deployment initiated: %s", app_name)
        return {"status": "deploying", "app_name": app_name, "url": preview_url}

    except Exception as exc:
        session.rollback()
        logger.error("Failed to deploy preview environment: %s", exc)
        raise self.retry(exc=exc) from exc
    finally:
        session.close()


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)  # type: ignore[untyped-decorator]
def destroy_preview_environment(self: Any, pr_id: str) -> dict[str, str]:
    """Destroy a preview environment when a PR is closed or merged.

    Steps:
    1. Fetch the PR and its Environment
    2. Delete the ArgoCD Application (which destroys all K8s resources)
    3. Update the Environment record (status=destroyed)
    4. Log an event
    """
    logger.info("Destroying preview environment for PR: %s", pr_id)
    session = get_sync_session()

    try:
        pr = session.execute(select(PullRequest).where(PullRequest.id == pr_id)).scalar_one_or_none()

        if not pr:
            logger.error("PullRequest not found: %s", pr_id)
            return {"status": "error", "message": "PR not found"}

        env = session.execute(
            select(Environment).where(Environment.pull_request_id == pr.id)
        ).scalar_one_or_none()

        if not env:
            logger.warning("No environment found for PR: %s", pr_id)
            return {"status": "skipped", "message": "No environment to destroy"}

        # Delete the ArgoCD Application
        argocd = ArgocdService()
        argocd.delete_application(env.argocd_app_name)

        # Update records
        env.status = EnvironmentStatus.DESTROYED
        env.destroyed_at = datetime.now(UTC)
        pr.preview_url = None

        session.add(
            Event(
                event_type=EventType.ENV_DESTROYED,
                message=f"Preview environment destroyed for PR #{pr.github_pr_number}",
                pull_request_id=pr.id,
            )
        )

        session.commit()

        logger.info("Preview environment destroyed: %s", env.argocd_app_name)
        return {"status": "destroyed", "app_name": env.argocd_app_name}

    except Exception as exc:
        session.rollback()
        logger.error("Failed to destroy preview environment: %s", exc)
        raise self.retry(exc=exc) from exc
    finally:
        session.close()


@celery_app.task(bind=True, max_retries=20, default_retry_delay=15)  # type: ignore[untyped-decorator]
def poll_deployment_status(self: Any, pr_id: str, app_name: str) -> dict[str, str]:
    """Poll ArgoCD until the preview environment is healthy or failed.

    This task retries every 15 seconds, up to 20 times (5 minutes total).
    Once the deployment is healthy, it updates the Environment status to RUNNING.
    """
    logger.info("Polling deployment status: %s", app_name)
    session = get_sync_session()

    try:
        argocd = ArgocdService()
        status = argocd.get_application_status(app_name)

        if status == "Healthy":
            # Deployment successful
            env = session.execute(
                select(Environment).where(Environment.argocd_app_name == app_name)
            ).scalar_one_or_none()

            if env:
                env.status = EnvironmentStatus.RUNNING
                session.add(
                    Event(
                        event_type=EventType.ENV_READY,
                        message=f"Preview environment ready: {env.url}",
                        pull_request_id=env.pull_request_id,
                    )
                )
                session.commit()

            logger.info("Deployment healthy: %s", app_name)
            return {"status": "healthy", "app_name": app_name}

        if status in ("Degraded", "Unknown"):
            # Deployment failed
            env = session.execute(
                select(Environment).where(Environment.argocd_app_name == app_name)
            ).scalar_one_or_none()

            if env:
                env.status = EnvironmentStatus.FAILED
                session.add(
                    Event(
                        event_type=EventType.ENV_FAILED,
                        message=f"Deployment failed for {app_name}: {status}",
                        pull_request_id=env.pull_request_id,
                    )
                )
                session.commit()

            logger.error("Deployment failed: %s status=%s", app_name, status)
            return {"status": "failed", "app_name": app_name}

        # Still deploying — retry
        logger.info("Deployment still in progress: %s status=%s", app_name, status)
        raise self.retry()

    except self.MaxRetriesExceededError:
        logger.error("Deployment timed out: %s", app_name)
        return {"status": "timeout", "app_name": app_name}
    except Exception as exc:
        if isinstance(exc, Retry):
            raise
        session.rollback()
        logger.error("Error polling deployment: %s", exc)
        raise self.retry(exc=exc) from exc
    finally:
        session.close()
