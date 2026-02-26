"""Shared test fixtures and configuration.

Using Testcontainers to spin up an ephemeral PostgreSQL database for tests.
- No hardcoded credentials in .env files or CI pipelines.
- No port conflicts locally.
- Identical behavior locally and in CI.
- Clean, isolated database for every test run.
"""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from testcontainers.postgres import PostgresContainer

from src.api.main import app
from src.models.database import get_db
from src.models.entities import Base


ROOT_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def postgres_container():
    """Start a PostgreSQL container for the entire test session."""
    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres


@pytest_asyncio.fixture
async def engine_test(postgres_container):
    """Create an async SQLAlchemy engine connected to the test container."""
    # testcontainers returns a sync URL like postgresql+psycopg2://...
    # We need to replace it with postgresql+asyncpg://...
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")
    
    # NullPool: each operation gets a fresh connection, no pooling.
    # Slightly slower but eliminates all "another operation in progress" errors.
    engine = create_async_engine(
        db_url,
        echo=False,
        poolclass=NullPool,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session_factory(engine_test):
    """Create a session factory bound to the test engine."""
    return async_sessionmaker(
        bind=engine_test,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture(autouse=True)
async def setup_database(engine_test) -> AsyncGenerator[None, None]:
    """Create all tables before each test, drop them after."""
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(test_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session for tests."""
    async with test_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(test_session_factory) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client wired to the test database.

    Override get_db so the API endpoints use our test database.
    Each API request gets its own session from the same NullPool engine,
    so there's no connection sharing conflict.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
