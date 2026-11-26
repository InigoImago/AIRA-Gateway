"""Alembic environment for the gateway DB (async, driven by GatewaySettings).

The migration container says what it did, as every process does (`FRD-631` FR-1): one line,
``migrations_applied`` with the revisions it applied and how long it took, or ``migrations_failed``
with where it stopped — through the export, and handed over before the process ends, so the log
store sees a failed upgrade without anybody reading the container's output.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from alembic import context
from sqlalchemy.engine import Connection

from aira_common.logging import get_logger, process_named
from aira_common.observability import flush_telemetry
from aira_gateway.config import GatewaySettings, configure_process
from aira_gateway.db import models  # noqa: F401  (register tables on Base.metadata)
from aira_gateway.db.base import Base, build_engine

target_metadata = Base.metadata

# Named before the settings are read: reading them from Vault writes this process's first lines.
process_named("migrate")

_log = get_logger("aira_gateway.migrations")

#: The revisions this run applied, in order.
_applied: list[str] = []


def _record(*, step: Any, **_kwargs: Any) -> None:
    """Alembic's `on_version_apply`: one call per revision it applied."""
    revision = getattr(step, "up_revision_id", None) if step.is_upgrade else None
    _applied.append(str(revision or getattr(step, "down_revision_ids", "") or "?"))


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, on_version_apply=_record
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async(settings: GatewaySettings) -> None:
    engine = build_engine(settings.database_url(use_sqlite=False))
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    url = GatewaySettings().database_url(use_sqlite=False)
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    settings = GatewaySettings()
    configure_process(settings, process="migrate")
    started = time.perf_counter()
    try:
        asyncio.run(_run_async(settings))
    except Exception as exc:
        _log.error(
            "migrations_failed",
            plane="gateway",
            migrations=list(_applied),
            duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            exc_info=exc,
        )
        raise
    else:
        _log.info(
            "migrations_applied",
            plane="gateway",
            migrations=list(_applied),
            count=len(_applied),
            duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
        )
    finally:
        flush_telemetry()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
