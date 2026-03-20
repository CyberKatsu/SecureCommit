"""
connection.py — Async SQLAlchemy engine and session factory.

Design decisions:
* asyncpg driver: PostgreSQL's async driver; significantly faster than
  psycopg2 for I/O-heavy workloads like ours (lots of INSERTs per review).
* pool_pre_ping=True: the pool tests connections before handing them to the
  application, preventing "connection closed" errors after Postgres restarts.
* AsyncSession is yielded via a context manager so callers never have to
  remember to close it — standard async FastAPI dependency pattern.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=settings.debug,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Keep objects usable after commit without re-querying.
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session."""
    async with AsyncSessionLocal() as session:
        yield session
