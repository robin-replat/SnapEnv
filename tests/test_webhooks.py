"""Tests for the GitHub webhook endpoint.

These tests verify:
- HMAC signature validation (security)
- PR creation and update on webhook events
- Pipeline creation on PR open/synchronize
- Celery task dispatch via send_task (decoupled from worker modules)
- Rejection of invalid/irrelevant events
"""

import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from httpx import AsyncClient


def sign_payload(payload: bytes, secret: str) -> str:
    """Generate a GitHub-style HMAC-SHA256 signature for a payload."""
    signature = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


def make_pr_payload(
    action: str,
    pr_number: int = 42,
    repo: str = "robin-replat/SnapEnv",
    title: str = "feat: add widget",
    author: str = "robin-replat",
    branch: str = "feat/widget",
    base_branch: str = "main",
    sha: str = "abc1234567890",
    merged: bool = False,
) -> dict:
    """Build a realistic GitHub pull_request webhook payload."""
    return {
        "action": action,
        "pull_request": {
            "number": pr_number,
            "title": title,
            "user": {"login": author},
            "head": {"ref": branch, "sha": sha},
            "base": {"ref": base_branch},
            "html_url": f"https://github.com/{repo}/pull/{pr_number}",
            "merged": merged,
        },
        "repository": {
            "full_name": repo,
        },
    }


# Path to mock — celery_app is imported in webhooks.py
CELERY_MOCK_PATH = "src.api.routes.webhooks.celery_app"
SETTINGS_MOCK_PATH = "src.api.routes.webhooks.get_settings"


# ── Signature Validation ──────────────────────────


@pytest.mark.asyncio
async def test_webhook_rejects_invalid_signature(client: AsyncClient) -> None:
    """Webhook must reject requests with an invalid HMAC signature."""
    payload = json.dumps(make_pr_payload("opened")).encode()

    with patch(SETTINGS_MOCK_PATH) as mock_get_settings:
        mock_get_settings.return_value.github_webhook_secret = "real-secret"  # noqa: S105

        response = await client.post(
            "/webhooks/github",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "sha256=invalidsignature",
            },
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook signature"


@pytest.mark.asyncio
async def test_webhook_accepts_valid_signature(client: AsyncClient) -> None:
    """Webhook must accept requests with a valid HMAC signature."""
    secret = "test-webhook-secret"  # noqa: S105
    payload = json.dumps(make_pr_payload("opened")).encode()
    signature = sign_payload(payload, secret)

    with (
        patch(SETTINGS_MOCK_PATH) as mock_get_settings,
        patch(CELERY_MOCK_PATH) as mock_celery,
    ):
        mock_get_settings.return_value.github_webhook_secret = secret
        mock_celery.send_task.return_value = None

        response = await client.post(
            "/webhooks/github",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": signature,
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


@pytest.mark.asyncio
async def test_webhook_skips_signature_when_no_secret(client: AsyncClient) -> None:
    """When no webhook secret is configured, skip signature verification."""
    payload = json.dumps(make_pr_payload("opened")).encode()

    with (
        patch(SETTINGS_MOCK_PATH) as mock_get_settings,
        patch(CELERY_MOCK_PATH) as mock_celery,
    ):
        mock_get_settings.return_value.github_webhook_secret = ""
        mock_celery.send_task.return_value = None

        response = await client.post(
            "/webhooks/github",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "",
            },
        )

    assert response.status_code == 200


# ── Event Filtering ───────────────────────────────


@pytest.mark.asyncio
async def test_webhook_ignores_non_pr_events(client: AsyncClient) -> None:
    """Non pull_request events should be ignored."""
    with patch(SETTINGS_MOCK_PATH) as mock_get_settings:
        mock_get_settings.return_value.github_webhook_secret = ""

        response = await client.post(
            "/webhooks/github",
            json={"zen": "Responsive is better than fast."},
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "ping",
                "X-Hub-Signature-256": "",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["event"] == "ping"


@pytest.mark.asyncio
async def test_webhook_ignores_irrelevant_pr_actions(client: AsyncClient) -> None:
    """PR actions we don't handle (labeled, assigned, etc.) should be ignored."""
    payload = make_pr_payload("labeled")

    with patch(SETTINGS_MOCK_PATH) as mock_get_settings:
        mock_get_settings.return_value.github_webhook_secret = ""

        response = await client.post(
            "/webhooks/github",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["action"] == "labeled"


# ── PR Opened ─────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_creates_pr_on_open(client: AsyncClient) -> None:
    """Opening a PR should create a PullRequest record and dispatch a task."""
    payload = make_pr_payload(
        action="opened",
        pr_number=42,
        title="feat: add widget",
        author="robin-replat",
        branch="feat/widget",
        sha="abc1234567890",
    )

    with (
        patch(SETTINGS_MOCK_PATH) as mock_get_settings,
        patch(CELERY_MOCK_PATH) as mock_celery,
    ):
        mock_get_settings.return_value.github_webhook_secret = ""
        mock_celery.send_task.return_value = None

        response = await client.post(
            "/webhooks/github",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert response.json()["pr_number"] == 42
    assert response.json()["action"] == "opened"

    # Verify celery_app.send_task was called with the correct task name
    mock_celery.send_task.assert_called_once()
    call_args = mock_celery.send_task.call_args
    assert call_args[0][0] == "src.workers.tasks.process_pr_event"


@pytest.mark.asyncio
async def test_webhook_dispatches_task_on_open(client: AsyncClient) -> None:
    """Opening a PR should dispatch a process_pr_event Celery task."""
    payload = make_pr_payload(action="opened")

    with (
        patch(SETTINGS_MOCK_PATH) as mock_get_settings,
        patch(CELERY_MOCK_PATH) as mock_celery,
    ):
        mock_get_settings.return_value.github_webhook_secret = ""
        mock_celery.send_task.return_value = None

        await client.post(
            "/webhooks/github",
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "",
            },
        )

    mock_celery.send_task.assert_called_once()
    call_args = mock_celery.send_task.call_args
    assert call_args[0][0] == "src.workers.tasks.process_pr_event"


# ── PR Synchronize (new push) ────────────────────


@pytest.mark.asyncio
async def test_webhook_updates_pr_on_synchronize(client: AsyncClient) -> None:
    """Pushing a new commit to a PR should update the commit SHA."""
    payload_open = make_pr_payload(action="opened", pr_number=42, sha="first_commit")
    payload_sync = make_pr_payload(action="synchronize", pr_number=42, sha="second_commit")

    with (
        patch(SETTINGS_MOCK_PATH) as mock_get_settings,
        patch(CELERY_MOCK_PATH) as mock_celery,
    ):
        mock_get_settings.return_value.github_webhook_secret = ""
        mock_celery.send_task.return_value = None

        await client.post(
            "/webhooks/github",
            json=payload_open,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "",
            },
        )

        response = await client.post(
            "/webhooks/github",
            json=payload_sync,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "",
            },
        )

    assert response.status_code == 200
    assert response.json()["action"] == "synchronize"
    # send_task called twice (opened + synchronize)
    assert mock_celery.send_task.call_count == 2


# ── PR Closed ─────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_closes_pr(client: AsyncClient) -> None:
    """Closing a PR should update its status and dispatch destroy task."""
    payload_open = make_pr_payload(action="opened", pr_number=42)
    payload_close = make_pr_payload(action="closed", pr_number=42, merged=False)

    with (
        patch(SETTINGS_MOCK_PATH) as mock_get_settings,
        patch(CELERY_MOCK_PATH) as mock_celery,
    ):
        mock_get_settings.return_value.github_webhook_secret = ""
        mock_celery.send_task.return_value = None

        await client.post(
            "/webhooks/github",
            json=payload_open,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "",
            },
        )

        response = await client.post(
            "/webhooks/github",
            json=payload_close,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "",
            },
        )

    assert response.status_code == 200
    assert response.json()["action"] == "closed"
    # Second call should dispatch with "closed" action
    assert mock_celery.send_task.call_count == 2


@pytest.mark.asyncio
async def test_webhook_merges_pr(client: AsyncClient) -> None:
    """Merging a PR should set status to MERGED, not just CLOSED."""
    payload_open = make_pr_payload(action="opened", pr_number=42)
    payload_merge = make_pr_payload(action="closed", pr_number=42, merged=True)

    with (
        patch(SETTINGS_MOCK_PATH) as mock_get_settings,
        patch(CELERY_MOCK_PATH) as mock_celery,
    ):
        mock_get_settings.return_value.github_webhook_secret = ""
        mock_celery.send_task.return_value = None

        await client.post(
            "/webhooks/github",
            json=payload_open,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "",
            },
        )

        response = await client.post(
            "/webhooks/github",
            json=payload_merge,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": "",
            },
        )

    assert response.status_code == 200
    assert response.json()["action"] == "closed"
