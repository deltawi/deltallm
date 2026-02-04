"""Database session management for ProxyLLM.

Provides async session management with connection pooling and
lifecycle management for the SQLAlchemy async ORM.
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from deltallm.db.base import Base

# Global engine and session factory
_engine: Optional[AsyncEngine] = None
_async_session_maker: Optional[async_sessionmaker[AsyncSession]] = None


def get_database_url() -> str:
    """Get database URL from environment or use default.
    
    Returns:
        Database connection URL for async PostgreSQL.
    """
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://deltallm:deltallm@localhost:5432/deltallm"
    )


def init_db(database_url: Optional[str] = None) -> None:
    """Initialize the database engine and session maker.
    
    Args:
        database_url: Optional database URL. If not provided, uses
                     DATABASE_URL environment variable or default.
    """
    global _engine, _async_session_maker
    
    if _engine is not None:
        return
    
    url = database_url or get_database_url()
    
    # Create async engine with connection pooling
    _engine = create_async_engine(
        url,
        echo=os.environ.get("SQL_ECHO", "false").lower() == "true",
        pool_size=int(os.environ.get("DB_POOL_SIZE", "10")),
        max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "20")),
        pool_pre_ping=True,  # Verify connections before using
        pool_recycle=3600,   # Recycle connections after 1 hour
    )
    
    # Create session factory
    _async_session_maker = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


async def close_db() -> None:
    """Close the database engine and cleanup resources."""
    global _engine, _async_session_maker
    
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_maker = None


async def create_tables() -> None:
    """Create all database tables.
    
    Note: In production, use Alembic migrations instead.
    This is useful for testing and development.
    """
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables() -> None:
    """Drop all database tables.
    
    Warning: This will delete all data. Use with caution.
    """
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session as an async context manager.
    
    Usage:
        async with get_session() as session:
            result = await session.execute(...)
            await session.commit()
    
    Yields:
        AsyncSession: Database session that auto-commits on success
                      and rolls back on exception.
    """
    if _async_session_maker is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    
    session = _async_session_maker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions.
    
    Usage in FastAPI:
        @app.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db_session)):
            ...
    
    Yields:
        AsyncSession: Database session managed by FastAPI dependency.
    """
    if _async_session_maker is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    
    session = _async_session_maker()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def get_engine() -> AsyncEngine:
    """Get the current database engine.
    
    Returns:
        AsyncEngine: The configured async SQLAlchemy engine.
    
    Raises:
        RuntimeError: If database hasn't been initialized.
    """
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine
