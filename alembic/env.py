"""Alembic environment configuration.

This file is executed by Alembic for every command (upgrade, revision, etc.).
It configures:
1. The database connection
2. The "target_metadata": the SQLAlchemy models that Alembic must know
   in order to detect changes and automatically generate migrations.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from src.models.config import settings
from src.models.entities import Base

config = context.config

config.set_main_option("sqlalchemy.url", settings.database_url_sync)

# Configure logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── Target metadata ──
# Alembic compares Base.metadata (what our Python models describe)
# with the current state of the database (what actually exists in PostgreSQL)
# and generates the SQL needed to move from one to the other.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Offline mode: generates SQL without connecting to the database.

    Useful for review: `alembic upgrade head --sql` produces the SQL
    that can be reviewed before executing it.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online mode: connects to the database and applies migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No pool for migrations (single connection)
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
