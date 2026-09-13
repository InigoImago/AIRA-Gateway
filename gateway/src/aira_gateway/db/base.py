"""SQLAlchemy async engine and session plumbing for the gateway (`FRD-101`).

The schema is owned by the Alembic migrations. :func:`create_all` is for SQLite — the tests, demo
mode and the CLI — which has no migration history to disagree with.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from aira_common.integration_debug import report


class Base(DeclarativeBase):
    """Declarative base for all gateway ORM models."""


def new_id() -> str:
    """A random UUID as text: the primary key of every row that has no natural one."""
    return str(uuid.uuid4())


def build_engine(url: str) -> AsyncEngine:
    """Create an async engine; in-memory SQLite shares one connection across sessions."""
    if url.startswith("sqlite"):
        engine = create_async_engine(
            url, poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
    else:
        engine = create_async_engine(url)
    watch_connections(engine)
    return engine


def watch_connections(engine: AsyncEngine) -> AsyncEngine:
    """Say when a physical connection is opened, and when one cannot be (`FRD-617` §3.3).

    Connections, not statements: statements are already in the trace (`FRD-117` §5.3). Errors are
    filtered to the ones about **reaching** the database — a unique violation is a correct answer
    from a working one. The address is rendered by SQLAlchemy with ``hide_password=True``, which
    knows where the password sits in every dialect.
    """
    target = engine.url.render_as_string(hide_password=True)
    sync_engine = engine.sync_engine

    @event.listens_for(sync_engine, "connect")
    def _opened(_dbapi_connection: object, _record: object) -> None:
        report("postgres", "connect", target=target)

    @event.listens_for(sync_engine, "handle_error")
    def _failed(context: Any) -> None:
        if not (getattr(context, "is_disconnect", False) or context.connection is None):
            return
        exc = context.original_exception
        report(
            "postgres",
            "error",
            outcome="failed",
            target=target,
            error_type=type(exc).__name__,
            error=str(exc)[:200],
            is_disconnect=bool(getattr(context, "is_disconnect", False)),
        )

    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    """Create every table from the models, bypassing the migrations."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
