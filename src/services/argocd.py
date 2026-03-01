"""ArgoCD service for managing preview environment applications.

This service communicates with the ArgoCD API to:
- Create ArgoCD Applications (one per PR)
- Delete Applications (when PR is closed)
- Check Application health status (polling during deployment)

Each preview environment is an ArgoCD Application that points to our
Helm chart with PR-specific overrides (image tag, namespace, ingress host).
ArgoCD then handles the actual K8s deployment, monitoring, and reconciliation.
"""

import logging
from typing import Any

import httpx

from src.models.config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)


class ArgocdService:
    """Client for the ArgoCD REST API."""

    def __init__(self) -> None:
        self.server = settings.argocd_server
        self.token = settings.argocd_token
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        # ArgoCD uses self-signed certs in local dev
        self.verify_ssl = not settings.debug

    def create_or_update_application(
        self,
        app_name: str,
        namespace: str,
        repo_url: str,
        chart_path: str,
        image_tag: str,
        pr_number: int,
    ) -> dict[str, Any]:
        """Create or update an ArgoCD Application for a preview environment.

        The Application spec tells ArgoCD:
        - Where to find the Helm chart (repo + path)
        - What values to override (image tag, namespace, ingress host)
        - Where to deploy (target namespace)
        - How to sync (automated, with pruning and self-healing)
        """
        preview_host = f"snapenv-pr-{pr_number}.{settings.preview_domain}"

        application = {
            "metadata": {
                "name": app_name,
                "namespace": "argocd",
                "labels": {
                    "app.kubernetes.io/managed-by": "snapenv",
                    "snapenv/pr-number": str(pr_number),
                },
            },
            "spec": {
                "project": "snapenv",
                "source": {
                    "repoURL": repo_url,
                    "targetRevision": "HEAD",
                    "path": chart_path,
                    "helm": {
                        "parameters": [
                            {"name": "image.tag", "value": image_tag},
                            {"name": "ingress.host", "value": preview_host},
                        ],
                    },
                },
                "destination": {
                    "server": "https://kubernetes.default.svc",
                    "namespace": namespace,
                },
                "syncPolicy": {
                    "automated": {
                        "prune": True,
                        "selfHeal": True,
                    },
                    "syncOptions": ["CreateNamespace=true"],
                },
            },
        }

        # Try to update first, create if not found
        try:
            response = httpx.put(
                f"{self.server}/api/v1/applications/{app_name}",
                json=application,
                headers=self.headers,
                verify=self.verify_ssl,
                timeout=30,
            )

            if response.status_code == 404:
                # Application doesn't exist, create it
                response = httpx.post(
                    f"{self.server}/api/v1/applications",
                    json=application,
                    headers=self.headers,
                    verify=self.verify_ssl,
                    timeout=30,
                )

            response.raise_for_status()
            logger.info("ArgoCD application created/updated: %s", app_name)
            return response.json()

        except httpx.HTTPError as exc:
            logger.error("ArgoCD API error: %s", exc)
            raise

    def delete_application(self, app_name: str) -> None:
        """Delete an ArgoCD Application and all its K8s resources.

        The cascade=true parameter tells ArgoCD to also delete all
        Kubernetes resources managed by this Application (pods, services,
        secrets, etc.), not just the Application record.
        """
        try:
            response = httpx.delete(
                f"{self.server}/api/v1/applications/{app_name}",
                params={"cascade": "true"},
                headers=self.headers,
                verify=self.verify_ssl,
                timeout=30,
            )

            if response.status_code == 404:
                logger.warning("ArgoCD application not found: %s", app_name)
                return

            response.raise_for_status()
            logger.info("ArgoCD application deleted: %s", app_name)

        except httpx.HTTPError as exc:
            logger.error("ArgoCD API error during delete: %s", exc)
            raise

    def get_application_status(self, app_name: str) -> str:
        """Get the health status of an ArgoCD Application.

        Returns one of: Healthy, Progressing, Degraded, Suspended, Missing, Unknown
        """
        try:
            response = httpx.get(
                f"{self.server}/api/v1/applications/{app_name}",
                headers=self.headers,
                verify=self.verify_ssl,
                timeout=15,
            )

            if response.status_code == 404:
                return "Missing"

            response.raise_for_status()
            data = response.json()

            health = data.get("status", {}).get("health", {}).get("status", "Unknown")
            sync = data.get("status", {}).get("sync", {}).get("status", "Unknown")

            logger.info("ArgoCD app %s: health=%s sync=%s", app_name, health, sync)
            return health

        except httpx.HTTPError as exc:
            logger.error("ArgoCD API error during status check: %s", exc)
            return "Unknown"
