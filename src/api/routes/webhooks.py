"""GitHub webhook endpoint.

GitHub calls POST /api/webhooks/github on every PR event.
The handler validates the signature, identifies the event type,
and enqueues a Celery task — then returns 200 immediately.
GitHub requires a response within 10 seconds; the actual work
(pipeline, environment provisioning) happens asynchronously in the worker.

Signature validation:
  GitHub computes HMAC-SHA256 over the raw request body using the
  webhook secret configured in GitHub repo Settings → Webhooks.
  We recompute it and compare with hmac.compare_digest (timing-safe).
  Requests with an invalid or missing signature are rejected with 401.
"""

import hashlib
import hmac
import json

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status

from src.models.config import get_settings
from src.workers.tasks import handle_pr_closed, handle_pr_opened, handle_pr_updated

logger = structlog.get_logger()
router = APIRouter()


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    """Raise 401 if the GitHub HMAC-SHA256 signature does not match.

    If GITHUB_WEBHOOK_SECRET is not configured (local dev), validation is skipped.
    In production the secret must always be set.
    """
    secret = get_settings().github_webhook_secret
    if not secret:
        return

    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Hub-Signature-256 header",
        )

    expected = (
        "sha256="
        + hmac.new(
            secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    )

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )


@router.post("/github", status_code=status.HTTP_200_OK)
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(None),
    x_hub_signature_256: str | None = Header(None),
    x_github_delivery: str | None = Header(None),
) -> dict[str, str]:
    """Receive a GitHub webhook and dispatch the matching Celery task.

    GitHub sends this on every PR action (opened, synchronize, closed…).
    We always return 200 — errors are logged, not re-raised, to prevent
    GitHub from retrying events we've already partially processed.
    """
    raw_body = await request.body()
    _verify_signature(raw_body, x_hub_signature_256)

    payload = json.loads(raw_body)
    action = payload.get("action", "")
    pr = payload.get("pull_request", {})
    pr_number = pr.get("number")
    repo = payload.get("repository", {}).get("full_name", "unknown")

    logger.info(
        "webhook_received",
        event=x_github_event,
        action=action,
        pr=pr_number,
        repo=repo,
        delivery=x_github_delivery,
    )

    if x_github_event != "pull_request":
        # We only care about PR events — acknowledge everything else silently.
        return {"status": "ignored", "event": x_github_event}

    if action == "opened":
        handle_pr_opened.delay(payload)
        logger.info("task_enqueued", task="handle_pr_opened", pr=pr_number)

    elif action in ("synchronize", "reopened"):
        handle_pr_updated.delay(payload)
        logger.info("task_enqueued", task="handle_pr_updated", pr=pr_number)

    elif action == "closed":
        handle_pr_closed.delay(payload)
        logger.info("task_enqueued", task="handle_pr_closed", pr=pr_number)

    return {"status": "ok", "action": action, "pr": str(pr_number)}
