"""Database connection management.

This module configures:
1. The "engine": the connection to PostgreSQL (connection pool)
2. The "session factory": creates sessions to interact with the DB
3. The FastAPI dependency get_db(): injects a session into each endpoint

API request flow:
    HTTP request → FastAPI → get_db() opens a session
    → the endpoint uses the session to read/write to the DB
    → get_db() commits if everything is fine, rolls back on error
    → the session is closed
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.models.config import settings

# ── Engine ────────────────────────────────────────────────
# The engine is the connection point to PostgreSQL.
# It manages a connection pool (not a single connection).
# When the app needs the DB, it borrows a connection from the pool,
# uses it, and returns it. This avoids opening/closing a TCP connection
# for every request (expensive).

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,    # If debug=True, prints SQL queries in logs
    pool_size=20,           # Number of connections kept open
    max_overflow=10,        # Extra connections for peak load (total max = 30)
    pool_pre_ping=True,     # Checks that the connection is alive before using it
)

# ── Session factory ───────────────────────────────────────
# A session = a "transaction" with the DB.
# The factory creates sessions configured the same way.
# expire_on_commit=False: after a commit, Python objects remain
# usable (without this, accessing pr.title after a commit would trigger
# an additional SQL query).

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that provides a database session.

    Used in endpoints like this:
        @router.get("/pull-requests")
        async def list_prs(db: AsyncSession = Depends(get_db)):
            ...

    FastAPI calls get_db() automatically, injects the session,
    and closes it cleanly after the response (even on error).

    This is the "Unit of Work" pattern:
    - One session per HTTP request
    - Automatic commit if no error
    - Automatic rollback on error
    """
    async with async_session_factory() as session:
        try:
            # Pause here and give the session to the FastAPI endpoint.
            # Execution of this function will resume AFTER the endpoint finishes.
            yield session

            # This runs only if the endpoint completed successfully.
            # "await" suspends execution until the commit operation is fully done.
            await session.commit()

        except Exception:
            # If the endpoint raised an error, execution resumes here.
            # "await" ensures the rollback completes before continuing.
            await session.rollback()

            # Re-raise the original exception so FastAPI can handle it properly.
            raise
