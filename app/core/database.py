"""Async database configuration and session management."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Create async engine with appropriate settings based on database type
_engine_kwargs = {
    "echo": settings.debug,
}

# Pool settings only apply to connection-pooling databases (PostgreSQL, MySQL)
# SQLite uses StaticPool or NullPool and doesn't support these settings
if not settings.database_url.startswith("sqlite"):
    _engine_kwargs.update({
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_pre_ping": True,
    })

engine = create_async_engine(settings.database_url, **_engine_kwargs)

# Create async session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for database sessions outside of request context."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Type alias for dependency injection
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def init_db() -> None:
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()


def get_celery_session_factory():
    """Create a fresh session factory for Celery tasks.

    This creates a new engine and session factory each time to avoid
    event loop issues when running async code in Celery workers.
    """
    celery_engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    return async_sessionmaker(
        celery_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    ), celery_engine


@asynccontextmanager
async def get_celery_db_context():
    """Context manager for Celery tasks that creates fresh connections."""
    session_factory, celery_engine = get_celery_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
            await celery_engine.dispose()
