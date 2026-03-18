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

    # ── Redis ─────────────────────────────────────
    # Redis serves two roles:
    # 1. Celery message broker (task queue)
    # 2. Celery result backend (task status/results)
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # ── GitHub ────────────────────────────────────
    # Webhook secret: used to verify that incoming webhooks
    # are genuinely from GitHub (HMAC-SHA256 signature).
    github_webhook_secret: str = ""
    # Personal access token: used to call the GitHub API
    # (read PR status, pipeline results, etc.)
    github_token: str = ""
    # The repository this instance monitors (owner/repo format)
    github_repository: str = ""

    # ── ArgoCD ────────────────────────────────────
    argocd_server: str = "https://localhost:8080"
    argocd_token: str = ""

    # ── Preview environments ──────────────────────
    # Helm chart path inside the Git repo
    helm_chart_path: str = "infra/helm/snapenv"

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

    @property
    def redis_url(self) -> str:
        """Redis connection URL for Celery."""
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache  # Singleton: config is loaded once and then cached
def get_settings() -> Settings:
    """Returns the single configuration instance.

    @lru_cache ensures this function is executed only once.
    Subsequent calls return the same object.
    This prevents reloading the .env file on every request.
    """
    return Settings()
