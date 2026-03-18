"""Tests for the ArgoCD service.

These tests mock the HTTP calls to the ArgoCD API and verify
that our service correctly creates, deletes, and checks applications.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.services.argocd import ArgocdService


@pytest.fixture
def argocd_service() -> ArgocdService:
    """Create an ArgoCD service with test configuration."""
    with patch("src.services.argocd.get_settings") as mock_get_settings:
        mock_get_settings.return_value.argocd_server = "https://argocd.test"
        mock_get_settings.return_value.argocd_token = "test-token"  # noqa: S105
        mock_get_settings.return_value.debug = True
        mock_get_settings.return_value.preview_domain = "preview.localhost"
        return ArgocdService()


class TestCreateOrUpdateApplication:
    """Tests for creating and updating ArgoCD applications."""

    def test_creates_application_when_not_found(self, argocd_service: ArgocdService) -> None:
        """If PUT returns 404, fall back to POST to create the application."""
        put_response = MagicMock()
        put_response.status_code = 404

        post_response = MagicMock()
        post_response.status_code = 200
        post_response.json.return_value = {"metadata": {"name": "snapenv-pr-42"}}
        post_response.raise_for_status.return_value = None

        with (
            patch("src.services.argocd.httpx.put", return_value=put_response),
            patch("src.services.argocd.httpx.post", return_value=post_response),
        ):
            result = argocd_service.create_or_update_application(
                app_name="snapenv-pr-42",
                namespace="pr-42",
                repo_url="https://github.com/robin-replat/SnapEnv.git",
                chart_path="infra/helm/snapenv",
                image_tag="abc1234",
                pr_number=42,
            )

        assert result["metadata"]["name"] == "snapenv-pr-42"

    def test_updates_existing_application(self, argocd_service: ArgocdService) -> None:
        """If PUT succeeds, the application is updated in place."""
        put_response = MagicMock()
        put_response.status_code = 200
        put_response.json.return_value = {"metadata": {"name": "snapenv-pr-42"}}
        put_response.raise_for_status.return_value = None

        with patch("src.services.argocd.httpx.put", return_value=put_response):
            result = argocd_service.create_or_update_application(
                app_name="snapenv-pr-42",
                namespace="pr-42",
                repo_url="https://github.com/robin-replat/SnapEnv.git",
                chart_path="infra/helm/snapenv",
                image_tag="abc1234",
                pr_number=42,
            )

        assert result["metadata"]["name"] == "snapenv-pr-42"

    def test_raises_on_api_error(self, argocd_service: ArgocdService) -> None:
        """ArgoCD API errors should propagate."""
        with (
            patch("src.services.argocd.httpx.put", side_effect=httpx.ConnectError("refused")),
            pytest.raises(httpx.ConnectError),
        ):
            argocd_service.create_or_update_application(
                app_name="snapenv-pr-42",
                namespace="pr-42",
                repo_url="https://github.com/robin-replat/SnapEnv.git",
                chart_path="infra/helm/snapenv",
                image_tag="abc1234",
                pr_number=42,
            )


class TestDeleteApplication:
    """Tests for deleting ArgoCD applications."""

    def test_deletes_application(self, argocd_service: ArgocdService) -> None:
        """Successful delete should not raise."""
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None

        with patch("src.services.argocd.httpx.delete", return_value=response):
            argocd_service.delete_application("snapenv-pr-42")

    def test_handles_already_deleted(self, argocd_service: ArgocdService) -> None:
        """Deleting a non-existent application should not raise (idempotent)."""
        response = MagicMock()
        response.status_code = 404

        with patch("src.services.argocd.httpx.delete", return_value=response):
            # Should not raise
            argocd_service.delete_application("snapenv-pr-42")


class TestGetApplicationStatus:
    """Tests for checking ArgoCD application health."""

    def test_returns_healthy(self, argocd_service: ArgocdService) -> None:
        """Healthy application should return 'Healthy'."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": {
                "health": {"status": "Healthy"},
                "sync": {"status": "Synced"},
            }
        }
        response.raise_for_status.return_value = None

        with patch("src.services.argocd.httpx.get", return_value=response):
            status = argocd_service.get_application_status("snapenv-pr-42")

        assert status == "Healthy"

    def test_returns_progressing(self, argocd_service: ArgocdService) -> None:
        """Deploying application should return 'Progressing'."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "status": {
                "health": {"status": "Progressing"},
                "sync": {"status": "OutOfSync"},
            }
        }
        response.raise_for_status.return_value = None

        with patch("src.services.argocd.httpx.get", return_value=response):
            status = argocd_service.get_application_status("snapenv-pr-42")

        assert status == "Progressing"

    def test_returns_missing_for_404(self, argocd_service: ArgocdService) -> None:
        """Non-existent application should return 'Missing'."""
        response = MagicMock()
        response.status_code = 404

        with patch("src.services.argocd.httpx.get", return_value=response):
            status = argocd_service.get_application_status("snapenv-pr-99")

        assert status == "Missing"

    def test_returns_unknown_on_error(self, argocd_service: ArgocdService) -> None:
        """API errors should return 'Unknown' instead of crashing."""
        with patch("src.services.argocd.httpx.get", side_effect=httpx.ConnectError("refused")):
            status = argocd_service.get_application_status("snapenv-pr-42")

        assert status == "Unknown"
