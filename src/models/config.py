"""Application configuration loaded from environment variables.

Pydantic-settings automatically loads variables from:
1. System environment variables
2. The .env file (thanks to env_file=".env")

In production (Docker/K8s), variables are injected by the orchestrator.
Locally, we use the .env file to avoid exporting them manually.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized application configuration.

    Each attribute corresponds to an environment variable.
    Example: postgres_host → POSTGRES_HOST

    Pydantic-settings automatically handles:
    - Type conversion (str → int, str → bool, etc.)
    - Default values
    - Validation (if a required variable is missing → clear error)
    """

    model_config = SettingsConfigDict(
        env_file=".env",  # Automatically load the .env file
        env_file_encoding="utf-8",
        case_sensitive=False,  # POSTGRES_HOST = postgres_host
    )

    # ── Application ───────────────────────────────────────
    app_name: str = "SnapEnv"
    preview_domain: str = "preview.localhost"
    debug: bool = False
    log_level: str = "INFO"

    # ── Database ──────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str
    postgres_password: str
    postgres_db: str = "preview_platform"

    # ── Redis ─────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379

    # ── GitHub ────────────────────────────────────────────
    # Set in GitHub repo Settings → Webhooks → Secret.
    # Leave empty to skip signature validation in local dev.
    github_webhook_secret: str = ""
    # Personal access token with repo:write scope (for posting PR comments).
    github_token: str = ""
    # owner/repo of the project being previewed (e.g. "acme/my-app").
    github_repository: str = ""
    # Only successful runs of this workflow are allowed to deploy previews.
    github_workflow_name: str = "CI"
    # Optional registry credentials for private GHCR packages in local k3d.
    ghcr_username: str = ""
    ghcr_token: str = ""

    # ── ArgoCD ────────────────────────────────────────────
    # Full URL of the ArgoCD API server (e.g. "http://argocd.localhost").
    argocd_server: str = ""
    # ArgoCD API token — create via Settings → Accounts → Generate Token.
    argocd_token: str = ""

    # ── Preview environment ────────────────────────────────
    # Path inside the repo to the Helm chart used for preview deployments.
    helm_chart_path: str = "infra/helm/snapenv"
    # Image repository ArgoCD should deploy for PR previews.
    # Defaults to ghcr.io/<GITHUB_REPOSITORY in lowercase>.
    preview_image_repository: str = ""
    # GitHub Actions publishes preview tags to GHCR, so k3d should pull them.
    preview_image_pull_policy: str = "Always"
    preview_image_pull_secret_name: str = "ghcr-credentials"  # noqa: S105 - Kubernetes Secret name
    # Temporary contract while previews reuse the SnapEnv chart itself.
    # If left empty, the worker reuses the platform DB credentials.
    preview_postgres_user: str = ""
    preview_postgres_password: str = ""
    preview_postgres_db: str = ""

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def resolved_preview_image_repository(self) -> str:
        if self.preview_image_repository:
            return self.preview_image_repository
        if self.github_repository:
            return f"ghcr.io/{self.github_repository.lower()}"
        return ""

    @property
    def resolved_preview_postgres_user(self) -> str:
        return self.preview_postgres_user or self.postgres_user

    @property
    def resolved_preview_postgres_password(self) -> str:
        return self.preview_postgres_password or self.postgres_password

    @property
    def resolved_preview_postgres_db(self) -> str:
        return self.preview_postgres_db or self.postgres_db

    @property
    def database_url(self) -> str:
        """Async connection URL (used by the FastAPI app).

        Format: postgresql+asyncpg://user:password@host:port/dbname
        The '+asyncpg' tells SQLAlchemy to use the async driver.
        """
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync connection URL (used by Alembic for migrations).

        Alembic does not support async; it requires a sync driver (psycopg2).
        """
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache  # Singleton: config is loaded once and then cached
def get_settings() -> Settings:
    """Returns the single configuration instance.

    @lru_cache ensures this function is executed only once.
    Subsequent calls return the same object.
    This prevents reloading the .env file on every request.
    """
    return Settings()
