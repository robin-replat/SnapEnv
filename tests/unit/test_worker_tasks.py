"""Focused unit tests for Celery workflow dispatch."""

from typing import Any
from unittest.mock import AsyncMock, call

import pytest

from src.api.routes import webhooks
from src.workers import tasks


def test_webhook_tasks_import_without_database_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    assert webhooks.handle_pr_opened is tasks.handle_pr_opened


@pytest.mark.asyncio
async def test_completed_workflow_deploys_each_attached_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    deploy = AsyncMock()
    monkeypatch.setattr(tasks, "_deploy_pr_from_ci", deploy)
    payload = {
        "workflow_run": {
            "head_sha": "a" * 40,
            "pull_requests": [{"number": 12}, {"number": 34}],
        },
        "repository": {"full_name": "robin-replat/SnapEnv"},
    }

    await tasks._handle_workflow_completed(payload)

    assert deploy.await_args_list == [
        call("robin-replat/SnapEnv", 12, "a" * 40),
        call("robin-replat/SnapEnv", 34, "a" * 40),
    ]


@pytest.mark.asyncio
async def test_provision_argocd_app_requires_argocd_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: type(
            "Settings",
            (),
            {
                "argocd_server": "",
                "argocd_token": "",
            },
        )(),
    )

    with pytest.raises(RuntimeError, match="ARGOCD_SERVER and ARGOCD_TOKEN"):
        await tasks._provision_argocd_app(
            "preview-pr-42",
            "pr-42",
            "a" * 40,
            "pr-42-aaaaaaa",
            "pr-42.localhost",
        )


@pytest.mark.asyncio
async def test_provision_argocd_app_sends_local_k3d_preview_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeSettings:
        argocd_server = "http://argocd-server.argocd.svc.cluster.local"
        github_repository = "Robin-Replat/SnapEnv"
        helm_chart_path = "infra/helm/snapenv"
        preview_image_pull_policy = "Always"
        preview_domain = "localhost"
        ghcr_username = "robin"

        @property
        def argocd_token(self) -> str:
            return "argocd-" + "token"

        @property
        def ghcr_token(self) -> str:
            return "ghcr-" + "token"

        @property
        def preview_image_pull_secret_name(self) -> str:
            return "ghcr-credentials"

        @property
        def resolved_preview_image_repository(self) -> str:
            return "ghcr.io/robin-replat/snapenv"

        @property
        def resolved_preview_postgres_user(self) -> str:
            return "preview"

        @property
        def resolved_preview_postgres_password(self) -> str:
            return "password"

        @property
        def resolved_preview_postgres_db(self) -> str:
            return "preview_platform"

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> FakeResponse:
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(tasks, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(tasks.httpx, "AsyncClient", FakeClient)

    await tasks._provision_argocd_app(
        "preview-pr-42",
        "pr-42",
        "a" * 40,
        "pr-42-aaaaaaa",
        "pr-42.localhost",
    )

    assert captured["url"] == "http://argocd-server.argocd.svc.cluster.local/api/v1/applications"
    assert captured["params"] == {"upsert": "true"}
    assert captured["headers"] == {"Authorization": "Bearer argocd-token"}

    parameters = {
        item["name"]: item["value"] for item in captured["json"]["spec"]["source"]["helm"]["parameters"]
    }
    assert parameters["image.repository"] == "ghcr.io/robin-replat/snapenv"
    assert parameters["image.tag"] == "pr-42-aaaaaaa"
    assert parameters["image.pullPolicy"] == "Always"
    assert parameters["ingress.host"] == "pr-42.localhost"
    assert parameters["monitoring.enabled"] == "false"
    assert parameters["postgresql.auth.username"] == "preview"
    assert parameters["postgresql.auth.password"] == "password"
    assert parameters["postgresql.auth.database"] == "preview_platform"
    assert parameters["registryCredentials.enabled"] == "true"
    assert parameters["registryCredentials.username"] == "robin"
    assert parameters["registryCredentials.password"] == "ghcr-token"
