"""Shared test fixtures and configuration.

Strategy: use NullPool (no connection reuse) and create a fresh
session per test to completely avoid async connection conflicts.
Each test gets its own clean tables via create_all/drop_all.
"""

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.api.main import app
from src.models.database import get_db
from src.models.entities import Base

TEST_DATABASE_URL = "postgresql+asyncpg://preview:preview@localhost:5432/preview_platform_test"

# NullPool: each operation gets a fresh connection, no pooling.
# Slightly slower but eliminates all "another operation in progress" errors.
engine_test = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    poolclass=NullPool,
)

TestSessionFactory = async_sessionmaker(
    bind=engine_test,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest_asyncio.fixture(autouse=True)
async def setup_database() -> AsyncGenerator[None, None]:
    """Create all tables before each test, drop them after."""
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine_test.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a database session for tests."""
    async with TestSessionFactory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Provide an async HTTP client wired to the test database.

    Override get_db so the API endpoints use our test database.
    Each API request gets its own session from the same NullPool engine,
    so there's no connection sharing conflict.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with TestSessionFactory() as session:
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
