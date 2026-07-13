"""Environment service — manage the lifecycle of preview environments.

A preview environment is a dedicated Kubernetes namespace + ArgoCD application
created for each open PR and destroyed when the PR is closed or merged.

These functions are called exclusively by Celery worker tasks, never directly
by API route handlers (the routes only READ environment state via the DB).
"""

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.entities import Environment, EnvironmentStatus

logger = structlog.get_logger()


async def create_environment(
    db: AsyncSession,
    pull_request_id: str,
    namespace: str,
    url: str,
    argocd_app_name: str,
) -> Environment:
    """Create the Environment record when provisioning begins.

    The environment starts in PROVISIONING status. The worker task updates
    it to RUNNING once ArgoCD reports the app is healthy.
    """
    environment = Environment(
        pull_request_id=pull_request_id,
        namespace=namespace,
        url=url,
        argocd_app_name=argocd_app_name,
        status=EnvironmentStatus.PROVISIONING,
    )
    db.add(environment)
    await db.flush()

    logger.info("environment_created", env_id=environment.id, namespace=namespace)
    return environment


async def update_status(
    db: AsyncSession,
    environment_id: str,
    status: EnvironmentStatus,
) -> Environment:
    """Update the status of an existing environment."""
    environment = await db.get(Environment, environment_id)
    if environment is None:
        raise ValueError(f"Environment {environment_id} not found")

    environment.status = status
    await db.flush()

    logger.info("environment_status_updated", env_id=environment_id, status=status.value)
    return environment


async def destroy_environment(
    db: AsyncSession,
    environment_id: str,
) -> Environment:
    """Mark an environment as destroyed and record the destruction time.

    The actual Kubernetes/ArgoCD teardown is done by the Celery task
    before calling this function.
    """

    environment = await db.get(Environment, environment_id)
    if environment is None:
        raise ValueError(f"Environment {environment_id} not found")

    environment.status = EnvironmentStatus.DESTROYED
    environment.destroyed_at = datetime.now(UTC)
    await db.flush()

    logger.info("environment_destroyed", env_id=environment_id, namespace=environment.namespace)
    return environment
