"""Celery task definitions for the PR preview environment lifecycle.

SnapEnv owns one pipeline stage: DEPLOY. Pull request webhooks only register
state. A successful GitHub ``workflow_run`` event starts the deployment after
lint, tests, and the immutable image build have completed.

Async bridge:
    Celery tasks are synchronous; the service layer uses SQLAlchemy asyncpg.
    asyncio.run() creates a fresh event loop per task — correct for Celery
    workers where each task runs in its own thread.

External integrations:
    ArgoCD REST API — creates / deletes preview Applications in Kubernetes.
                      Set ARGOCD_SERVER + ARGOCD_TOKEN to enable deployments.
    GitHub REST API — posts the preview URL as a PR comment.
                      Set GITHUB_TOKEN to enable comments.

WebSocket note:
    Events are persisted to PostgreSQL and published to Redis. API replicas
    subscribe to Redis and fan them out to their own WebSocket clients.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.models.config import get_settings
from src.models.entities import (
    Environment,
    EnvironmentStatus,
    EventType,
    Pipeline,
    PipelineStage,
    PipelineStatus,
    PRStatus,
    PullRequest,
    StageStatus,
)
from src.services.environment_service import (
    create_environment,
    destroy_environment,
)
from src.services.environment_service import (
    update_status as update_env_status,
)
from src.services.event_service import create_event
from src.services.pipeline_service import (
    complete_pipeline,
    complete_stage,
    create_pipeline,
    start_pipeline,
    start_stage,
)
from src.workers.celery_app import celery_app

logger = structlog.get_logger()


@asynccontextmanager
async def _worker_db_session() -> AsyncGenerator[AsyncSession, None]:
    # NullPool: each task opens its own connection and closes it on exit.
    # Avoids "Future attached to a different loop" — asyncpg connections from
    # a previous asyncio.run() loop cannot be reused in the next one.
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
    await engine.dispose()


# ── Celery task entry points (sync wrappers) ──────────────────────────────────


@celery_app.task(name="handle_pr_opened")  # type: ignore[untyped-decorator]
def handle_pr_opened(payload: dict[str, Any]) -> None:
    asyncio.run(_handle_pr_opened(payload))


@celery_app.task(name="handle_pr_updated")  # type: ignore[untyped-decorator]
def handle_pr_updated(payload: dict[str, Any]) -> None:
    asyncio.run(_handle_pr_updated(payload))


@celery_app.task(name="handle_pr_closed")  # type: ignore[untyped-decorator]
def handle_pr_closed(payload: dict[str, Any]) -> None:
    asyncio.run(_handle_pr_closed(payload))


@celery_app.task(name="handle_workflow_completed")  # type: ignore[untyped-decorator]
def handle_workflow_completed(payload: dict[str, Any]) -> None:
    asyncio.run(_handle_workflow_completed(payload))


# ── External API helpers ──────────────────────────────────────────────────────


async def _provision_argocd_app(
    app_name: str,
    namespace: str,
    revision: str,
    image_tag: str,
    preview_host: str,
) -> None:
    """Create or update the ArgoCD Application for an immutable PR revision.

    ``upsert=true`` lets the same operation handle both the first deployment
    and subsequent successful CI runs. The Git revision and image tag refer to
    the same commit, avoiding branch movement between build and deployment.
    """
    settings = get_settings()
    if not settings.argocd_server or not settings.argocd_token:
        raise RuntimeError("ARGOCD_SERVER and ARGOCD_TOKEN are required to create previews")

    preview_image_repository = settings.resolved_preview_image_repository
    if not preview_image_repository:
        raise RuntimeError("PREVIEW_IMAGE_REPOSITORY or GITHUB_REPOSITORY is required to create previews")

    helm_parameters = [
        {"name": "image.repository", "value": preview_image_repository},
        {"name": "image.tag", "value": image_tag},
        {"name": "image.pullPolicy", "value": settings.preview_image_pull_policy},
        {"name": "ingress.host", "value": preview_host},
        {"name": "monitoring.enabled", "value": "false"},
        {
            "name": "postgresql.auth.username",
            "value": settings.resolved_preview_postgres_user,
        },
        {
            "name": "postgresql.auth.password",
            "value": settings.resolved_preview_postgres_password,
        },
        {
            "name": "postgresql.auth.database",
            "value": settings.resolved_preview_postgres_db,
        },
        {"name": "github.repository", "value": settings.github_repository},
        {"name": "preview.domain", "value": settings.preview_domain},
    ]
    if settings.ghcr_username and settings.ghcr_token:
        helm_parameters.extend(
            [
                {"name": "registryCredentials.enabled", "value": "true"},
                {"name": "registryCredentials.name", "value": settings.preview_image_pull_secret_name},
                {"name": "registryCredentials.username", "value": settings.ghcr_username},
                {"name": "registryCredentials.password", "value": settings.ghcr_token},
            ]
        )

    app_body = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": app_name},
        "spec": {
            "project": "default",
            "source": {
                "repoURL": f"https://github.com/{settings.github_repository}",
                "targetRevision": revision,
                "path": settings.helm_chart_path,
                "helm": {"parameters": helm_parameters},
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": namespace,
            },
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["CreateNamespace=true"],
            },
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.argocd_server}/api/v1/applications",
            params={"upsert": "true"},
            json=app_body,
            headers={"Authorization": f"Bearer {settings.argocd_token}"},
        )
        resp.raise_for_status()

    logger.info("argocd_app_applied", app=app_name, namespace=namespace, revision=revision[:7])


async def _delete_argocd_app(app_name: str) -> None:
    """Delete an ArgoCD Application and cascade-delete its namespace."""
    settings = get_settings()
    if not settings.argocd_server or not settings.argocd_token:
        raise RuntimeError("ARGOCD_SERVER and ARGOCD_TOKEN are required to delete previews")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(
            f"{settings.argocd_server}/api/v1/applications/{app_name}",
            params={"cascade": "true"},
            headers={"Authorization": f"Bearer {settings.argocd_token}"},
        )
        if resp.status_code == 404:
            logger.info("argocd_app_already_gone", app=app_name)
            return
        resp.raise_for_status()

    logger.info("argocd_app_deleted", app=app_name)


async def _post_github_comment(repo: str, pr_number: int, preview_url: str) -> None:
    """Post the preview URL as a comment on the GitHub PR."""
    settings = get_settings()
    if not settings.github_token:
        logger.info("github_comment_skipped", reason="GITHUB_TOKEN not set")
        return

    body = (
        f"🚀 **Preview environment ready**\n\n"
        f"| | |\n"
        f"|---|---|\n"
        f"| **URL** | [{preview_url}]({preview_url}) |\n"
        f"| **Namespace** | `pr-{pr_number}` |\n\n"
        f"_Automatically destroyed when this PR is closed or merged._"
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
            json={"body": body},
            headers={
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        resp.raise_for_status()

    logger.info("github_comment_posted", repo=repo, pr=pr_number)


# ── Async task implementations ────────────────────────────────────────────────


async def _handle_pr_opened(payload: dict[str, Any]) -> None:
    pr = payload["pull_request"]
    pr_number: int = pr["number"]
    repo: str = payload["repository"]["full_name"]
    branch: str = pr["head"]["ref"]
    commit_sha: str = pr["head"]["sha"]
    title: str = pr.get("title", f"PR #{pr_number}")
    author: str = pr["user"]["login"]
    base_branch: str = pr["base"]["ref"]
    pr_html_url: str = pr["html_url"]

    logger.info("handle_pr_opened", pr=pr_number, repo=repo, branch=branch, sha=commit_sha[:7])

    async with _worker_db_session() as db:
        try:
            existing = await db.execute(
                select(PullRequest).where(
                    PullRequest.github_pr_number == pr_number,
                    PullRequest.repository == repo,
                )
            )
            if existing.scalar_one_or_none() is not None:
                logger.warning("pr_already_exists", pr=pr_number, repo=repo)
                return

            # 1. Persist PullRequest
            pr_record = PullRequest(
                github_pr_number=pr_number,
                repository=repo,
                title=title,
                author=author,
                branch=branch,
                base_branch=base_branch,
                status=PRStatus.OPEN,
                preview_url=None,
                github_url=pr_html_url,
                latest_commit_sha=commit_sha,
            )
            db.add(pr_record)
            await db.flush()
            await create_event(
                db,
                EventType.PR_OPENED,
                f"PR #{pr_number} opened: {title}",
                pr_record.id,
            )
            await db.commit()

        except Exception:
            await db.rollback()
            raise


async def _handle_pr_updated(payload: dict[str, Any]) -> None:
    """Record the newest PR revision without deploying it before CI succeeds."""
    pr = payload["pull_request"]
    pr_number: int = pr["number"]
    repo: str = payload["repository"]["full_name"]
    commit_sha: str = pr["head"]["sha"]

    logger.info("handle_pr_updated", pr=pr_number, repo=repo, sha=commit_sha[:7])

    async with _worker_db_session() as db:
        try:
            pr_result = await db.execute(
                select(PullRequest).where(
                    PullRequest.github_pr_number == pr_number,
                    PullRequest.repository == repo,
                )
            )
            pr_record = pr_result.scalar_one_or_none()
            if pr_record is None:
                logger.warning("pr_not_found_for_update", pr=pr_number, repo=repo)
                return

            pr_record.latest_commit_sha = commit_sha
            pr_record.branch = pr["head"]["ref"]
            pr_record.base_branch = pr["base"]["ref"]
            pr_record.title = pr.get("title", pr_record.title)
            pr_record.author = pr.get("user", {}).get("login", pr_record.author)
            pr_record.status = PRStatus.OPEN
            await db.flush()
            await create_event(
                db,
                EventType.PR_UPDATED,
                f"PR #{pr_number} updated — commit {commit_sha[:7]}",
                pr_record.id,
            )

            running_result = await db.execute(
                select(Pipeline).where(
                    Pipeline.pull_request_id == pr_record.id,
                    Pipeline.status == PipelineStatus.RUNNING,
                )
            )
            for running in running_result.scalars().all():
                running.status = PipelineStatus.CANCELLED
                running.finished_at = datetime.now(UTC)
            await db.commit()

        except Exception:
            await db.rollback()
            raise


async def _handle_workflow_completed(payload: dict[str, Any]) -> None:
    """Deploy every PR attached to a successful, filtered GitHub workflow run."""
    workflow_run = payload["workflow_run"]
    repo: str = payload["repository"]["full_name"]
    commit_sha: str = workflow_run["head_sha"]

    for pr_reference in workflow_run.get("pull_requests", []):
        await _deploy_pr_from_ci(repo, int(pr_reference["number"]), commit_sha)


async def _deploy_pr_from_ci(repo: str, pr_number: int, commit_sha: str) -> None:
    """Create or update a preview for the latest successfully built PR commit."""
    settings = get_settings()
    namespace = f"pr-{pr_number}"
    image_tag = f"pr-{pr_number}-{commit_sha[:7]}"
    preview_host = f"pr-{pr_number}.{settings.preview_domain}"
    preview_url = f"http://{preview_host}"
    argocd_app_name = f"preview-pr-{pr_number}"

    logger.info("deploy_ci_revision", pr=pr_number, repo=repo, sha=commit_sha[:7])

    async with _worker_db_session() as db:
        try:
            pr_result = await db.execute(
                select(PullRequest).where(
                    PullRequest.github_pr_number == pr_number,
                    PullRequest.repository == repo,
                )
            )
            pr_record = pr_result.scalar_one_or_none()
            if pr_record is None:
                logger.warning("pr_not_found_for_workflow", pr=pr_number, repo=repo)
                return
            if pr_record.status != PRStatus.OPEN:
                logger.info("workflow_ignored_pr_not_open", pr=pr_number, status=pr_record.status.value)
                return
            if pr_record.latest_commit_sha != commit_sha:
                logger.info(
                    "workflow_ignored_stale_commit",
                    pr=pr_number,
                    expected=pr_record.latest_commit_sha[:7],
                    received=commit_sha[:7],
                )
                return

            env_result = await db.execute(
                select(Environment).where(Environment.pull_request_id == pr_record.id)
            )
            environment = env_result.scalar_one_or_none()

            duplicate_result = await db.execute(
                select(Pipeline)
                .where(
                    Pipeline.pull_request_id == pr_record.id,
                    Pipeline.commit_sha == commit_sha,
                    Pipeline.status.in_(
                        [PipelineStatus.PENDING, PipelineStatus.RUNNING, PipelineStatus.SUCCESS]
                    ),
                )
                .order_by(Pipeline.created_at.desc())
                .limit(1)
            )
            duplicate = duplicate_result.scalar_one_or_none()
            if duplicate and (
                duplicate.status != PipelineStatus.SUCCESS
                or (environment is not None and environment.status == EnvironmentStatus.RUNNING)
            ):
                logger.info(
                    "workflow_already_processed",
                    pr=pr_number,
                    pipeline_id=duplicate.id,
                    status=duplicate.status.value,
                )
                return

            pipeline = await create_pipeline(db, pr_record.id, commit_sha)
            await start_pipeline(db, pipeline.id)
            await create_event(
                db,
                EventType.PIPELINE_STARTED,
                f"Deploy pipeline started for commit {commit_sha[:7]}",
                pr_record.id,
                pipeline.id,
            )

            deploy_result = await db.execute(
                select(PipelineStage).where(PipelineStage.pipeline_id == pipeline.id)
            )
            deploy_stage = deploy_result.scalar_one()
            await start_stage(db, deploy_stage.id)
            await create_event(
                db,
                EventType.STAGE_STARTED,
                "Applying the successful CI revision through ArgoCD",
                pr_record.id,
                pipeline.id,
            )

            announce_preview = environment is None or environment.status == EnvironmentStatus.DESTROYED
            if environment is None:
                environment = await create_environment(
                    db,
                    pr_record.id,
                    namespace,
                    preview_url,
                    argocd_app_name,
                )
            else:
                environment.namespace = namespace
                environment.url = preview_url
                environment.argocd_app_name = argocd_app_name
                environment.destroyed_at = None
                await update_env_status(db, environment.id, EnvironmentStatus.PROVISIONING)

            await create_event(
                db,
                EventType.ENV_PROVISIONING,
                f"Applying commit {commit_sha[:7]} to namespace {namespace}",
                pr_record.id,
                pipeline.id,
            )
            await db.commit()

            pipeline_status: PipelineStatus
            try:
                await _provision_argocd_app(
                    argocd_app_name,
                    namespace,
                    commit_sha,
                    image_tag,
                    preview_host,
                )
                await update_env_status(db, environment.id, EnvironmentStatus.RUNNING)
                pr_record.preview_url = preview_url
                await complete_stage(
                    db,
                    deploy_stage.id,
                    StageStatus.SUCCESS,
                    details={
                        "namespace": namespace,
                        "argocd_app": argocd_app_name,
                        "url": preview_url,
                        "image_tag": image_tag,
                        "commit_sha": commit_sha,
                    },
                )
                await create_event(
                    db,
                    EventType.ENV_READY,
                    f"Preview applied for commit {commit_sha[:7]}: {preview_url}",
                    pr_record.id,
                    pipeline.id,
                )
                pipeline_status = PipelineStatus.SUCCESS
            except Exception as exc:
                logger.error("deploy_failed", error=str(exc), pr=pr_number, sha=commit_sha[:7])
                await update_env_status(db, environment.id, EnvironmentStatus.FAILED)
                await complete_stage(
                    db,
                    deploy_stage.id,
                    StageStatus.FAILED,
                    details={"error": str(exc), "commit_sha": commit_sha},
                )
                await create_event(
                    db,
                    EventType.ENV_FAILED,
                    f"Deployment failed for commit {commit_sha[:7]}: {exc}",
                    pr_record.id,
                    pipeline.id,
                )
                pipeline_status = PipelineStatus.FAILED

            await complete_pipeline(db, pipeline.id, pipeline_status)
            await db.commit()

            if pipeline_status == PipelineStatus.SUCCESS and announce_preview:
                try:
                    await _post_github_comment(repo, pr_number, preview_url)
                except Exception as exc:
                    logger.warning("github_comment_failed", error=str(exc), pr=pr_number)

        except Exception:
            await db.rollback()
            raise


async def _handle_pr_closed(payload: dict[str, Any]) -> None:
    pr = payload["pull_request"]
    pr_number: int = pr["number"]
    repo: str = payload["repository"]["full_name"]
    merged: bool = pr.get("merged", False)

    logger.info("handle_pr_closed", pr=pr_number, repo=repo, merged=merged)

    async with _worker_db_session() as db:
        try:
            pr_result = await db.execute(
                select(PullRequest).where(
                    PullRequest.github_pr_number == pr_number,
                    PullRequest.repository == repo,
                )
            )
            pr_record = pr_result.scalar_one_or_none()
            if pr_record is None:
                logger.warning("pr_not_found_for_close", pr=pr_number, repo=repo)
                return

            # Tear down the environment if one exists and isn't already gone
            env_result = await db.execute(
                select(Environment).where(Environment.pull_request_id == pr_record.id)
            )
            env = env_result.scalar_one_or_none()

            if env and env.status not in {EnvironmentStatus.DESTROYED, EnvironmentStatus.DESTROYING}:
                await update_env_status(db, env.id, EnvironmentStatus.DESTROYING)
                await create_event(
                    db,
                    EventType.ENV_DESTROYING,
                    f"Destroying preview environment for PR #{pr_number}",
                    pr_record.id,
                )
                await db.commit()

                try:
                    await _delete_argocd_app(env.argocd_app_name)
                    await destroy_environment(db, env.id)
                    await create_event(
                        db,
                        EventType.ENV_DESTROYED,
                        f"Preview environment destroyed for PR #{pr_number}",
                        pr_record.id,
                    )
                except Exception as exc:
                    logger.error("env_destroy_failed", error=str(exc), pr=pr_number)
                    await update_env_status(db, env.id, EnvironmentStatus.FAILED)
                    await create_event(
                        db,
                        EventType.ENV_FAILED,
                        f"Failed to destroy environment: {exc}",
                        pr_record.id,
                    )

                await db.commit()

            # Mark the PR as closed or merged
            pr_record.status = PRStatus.MERGED if merged else PRStatus.CLOSED
            await create_event(
                db,
                EventType.PR_MERGED if merged else EventType.PR_CLOSED,
                f"PR #{pr_number} {'merged' if merged else 'closed'}",
                pr_record.id,
            )
            await db.commit()

        except Exception:
            await db.rollback()
            raise
