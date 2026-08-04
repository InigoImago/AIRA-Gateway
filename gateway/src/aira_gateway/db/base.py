"""SQLAlchemy async engine/session plumbing for the gateway (FRD-101).

Phase 1 creates tables with ``create_all``; Alembic migrations arrive with FRD-103.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    """Declarative base for all gateway ORM models."""


def build_engine(url: str) -> AsyncEngine:
    """Create an async engine; in-memory SQLite shares one connection across sessions."""
    if url.startswith("sqlite"):
        return create_async_engine(
            url, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    return create_async_engine(url)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """Create all tables (Phase 1; replaced by migrations in FRD-103)."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
