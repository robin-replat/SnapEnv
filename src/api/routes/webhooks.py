"""GitHub webhook receiver.

This endpoint receives events from GitHub when PR actions occur.
GitHub sends a POST request with a JSON payload and a HMAC-SHA256 signature.

Security: we verify the signature using our webhook secret to ensure
the request genuinely comes from GitHub and hasn't been tampered with.
Without this, anyone could send fake webhook payloads to our API.

Supported events:
- pull_request.opened → create preview environment
- pull_request.synchronize → update preview environment (new commit pushed)
- pull_request.closed → destroy preview environment
- pull_request.reopened → recreate preview environment
"""

import hashlib
import hmac
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from src.models.config import get_settings
from src.models.database import get_session_factory
from src.models.entities import (
    Event,
    EventType,
    Pipeline,
    PipelineStatus,
    PRStatus,
    PullRequest,
)
from src.workers.tasks import process_pr_event

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(tags=["webhooks"])


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify the HMAC-SHA256 signature from GitHub.

    GitHub sends a header X-Hub-Signature-256 with each webhook.
    We compute our own HMAC using the shared secret and compare.
    If they don't match, the request is rejected.

    Uses hmac.compare_digest for constant-time comparison
    to prevent timing attacks.
    """
    if not secret:
        # No secret configured — skip verification (development only)
        logger.warning("Webhook secret not configured, skipping signature verification")
        return True

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(""),
    x_github_event: str = Header(""),
) -> dict[str, Any]:
    """Receive and process GitHub webhook events.

    This endpoint:
    1. Verifies the HMAC signature (security)
    2. Extracts the PR information from the payload
    3. Creates or updates the PullRequest in the database
    4. Enqueues a Celery task for async processing
    5. Returns immediately (GitHub expects a response within 10 seconds)
    """
    # Read the raw body for signature verification
    body = await request.body()

    # Verify signature
    if not verify_github_signature(body, x_hub_signature_256, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    # Parse the JSON payload
    payload = await request.json()

    # We only handle pull_request events
    if x_github_event != "pull_request":
        return {"status": "ignored", "event": x_github_event}

    action = payload.get("action", "")
    pr_data = payload.get("pull_request", {})

    if not pr_data:
        return {"status": "ignored", "reason": "no pull_request data"}

    # Only process relevant actions
    relevant_actions = {"opened", "synchronize", "closed", "reopened"}
    if action not in relevant_actions:
        return {"status": "ignored", "action": action}

    # Extract PR information from the GitHub payload
    pr_number = pr_data["number"]
    repository = payload["repository"]["full_name"]

    logger.info(
        "Received PR event: repo=%s pr=#%d action=%s",
        repository,
        pr_number,
        action,
    )

    # Create or update the PR in our database
    session_factory = get_session_factory()
    async with session_factory() as db:
        # Check if this PR already exists
        pr = (
            await db.execute(
                select(PullRequest).where(
                    PullRequest.github_pr_number == pr_number,
                    PullRequest.repository == repository,
                )
            )
        ).scalar_one_or_none()

        if pr:
            # Update existing PR
            pr.latest_commit_sha = pr_data["head"]["sha"]
            pr.title = pr_data["title"]
            if action == "closed":
                pr.status = PRStatus.MERGED if pr_data.get("merged") else PRStatus.CLOSED
            elif action == "reopened":
                pr.status = PRStatus.OPEN
        else:
            # Create new PR
            pr = PullRequest(
                github_pr_number=pr_number,
                repository=repository,
                title=pr_data["title"],
                author=pr_data["user"]["login"],
                branch=pr_data["head"]["ref"],
                base_branch=pr_data["base"]["ref"],
                status=PRStatus.OPEN,
                github_url=pr_data["html_url"],
                latest_commit_sha=pr_data["head"]["sha"],
            )
            db.add(pr)

        # Create a pipeline record for this event
        if action in ("opened", "synchronize", "reopened"):
            pipeline = Pipeline(
                pull_request_id=pr.id if pr.id else None,
                commit_sha=pr_data["head"]["sha"],
                status=PipelineStatus.PENDING,
            )
            db.add(pipeline)

        # Log the event
        event_type_map = {
            "opened": EventType.PR_OPENED,
            "synchronize": EventType.PR_UPDATED,
            "closed": EventType.PR_CLOSED,
            "reopened": EventType.PR_REOPENED,
        }
        db.add(Event(
            event_type=event_type_map.get(action, EventType.PR_UPDATED),
            message=f"PR #{pr_number} {action}: {pr_data['title']}",
            pull_request_id=pr.id if pr.id else None,
        ))

        await db.commit()
        await db.refresh(pr)

        # Enqueue the async task for processing
        # This returns immediately — the worker handles the heavy lifting
        process_pr_event.delay(str(pr.id), action)

    return {
        "status": "accepted",
        "pr_number": pr_number,
        "action": action,
    }
