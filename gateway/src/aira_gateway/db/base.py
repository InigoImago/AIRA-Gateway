"""SQLAlchemy async engine/session plumbing for the gateway (FRD-101).

Phase 1 creates tables with ``create_all``; Alembic migrations arrive with FRD-103.
"""

from __future__ import annotations

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

    **Connections, not statements.** A line per query is not integration debugging, it is a second
    slow-query log with none of the tooling — and the statements are already in the trace, with
    their placeholders and without their bound values (`FRD-117` §5.3). What is *not* anywhere is
    the moment the pool reaches for the database and finds a wrong host, a closed port, a rejected
    password or a certificate it does not trust, which is the whole of what the first day of an
    integration consists of.

    Errors are filtered to the ones that are about **reaching** the database — SQLAlchemy's
    `is_disconnect`, plus anything raised with no connection in hand. A unique-violation on a busy
    gateway is a correct answer from a working database, and reporting it here would bury the four
    lines that matter under thousands that do not.

    The address is rendered by SQLAlchemy with `hide_password=True` rather than by us: it knows
    where the password is in every dialect it supports, and a redaction that has to be re-derived
    per URL scheme is one that is eventually wrong for the scheme nobody tested.
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
    """Create all tables (Phase 1; replaced by migrations in FRD-103)."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
