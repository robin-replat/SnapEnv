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
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

import src.models.database as db_module
from src.api.main import app
from src.models.database import get_db
from src.models.entities import Base

ROOT_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def postgres_container() -> AsyncGenerator[PostgresContainer, None]:
    """Start a PostgreSQL container for the entire test session.

    Yields:
        PostgresContainer: A running PostgreSQL test container instance.
    """
    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres


@pytest_asyncio.fixture
async def engine_test(postgres_container: PostgresContainer) -> AsyncGenerator[AsyncEngine, None]:
    """Create an async SQLAlchemy engine connected to the test container.

    Uses `NullPool` so each operation gets a fresh connection with no pooling,
    which avoids async driver state conflicts during tests.

    Args:
        postgres_container: The running PostgreSQL test container fixture.

    Yields:
        AsyncEngine: An async SQLAlchemy engine bound to the test database.
    """
    # testcontainers returns a sync URL like postgresql+psycopg2://...
    # We need to replace it with postgresql+asyncpg://...
    db_url = postgres_container.get_connection_url().replace("psycopg2", "asyncpg")

    engine = create_async_engine(
        db_url,
        echo=False,
        poolclass=NullPool,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session_factory(engine_test: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory bound to the test engine.

    Args:
        engine_test: The async SQLAlchemy engine fixture.

    Returns:
        async_sessionmaker[AsyncSession]: A factory for creating async database sessions.
    """
    return async_sessionmaker(
        bind=engine_test,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture
async def setup_database(engine_test: AsyncEngine) -> AsyncGenerator[None, None]:
    """Create all tables before each test and drop them afterwards.

    This fixture is requested only by database-backed test fixtures so pure
    unit tests can run without starting PostgreSQL.

    Args:
        engine_test: The async SQLAlchemy engine fixture.

    Yields:
        None
    """
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(
    setup_database: None,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session for tests.

    Args:
        setup_database: Ensures the test schema exists for the current test.
        test_session_factory: The async session factory fixture.

    Yields:
        AsyncSession: An active SQLAlchemy async session.
    """
    async with test_session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(
    setup_database: None,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client wired to the test database.

    Overrides the `get_db` FastAPI dependency so API endpoints use the test
    database. Each request gets its own session to avoid connection-sharing
    conflicts during async test execution.

    Args:
        setup_database: Ensures the test schema exists for the current test.
        test_session_factory: The async session factory fixture.

    Yields:
        AsyncClient: An HTTPX async client configured for the FastAPI app.
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
    db_module._async_session_factory = test_session_factory

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
    db_module._async_session_factory = None
