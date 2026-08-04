"""Alembic environment for the gateway DB (async, driven by GatewaySettings)."""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from aira_gateway.config import GatewaySettings
from aira_gateway.db import models  # noqa: F401  (register tables on Base.metadata)
from aira_gateway.db.base import Base, build_engine

target_metadata = Base.metadata


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = build_engine(GatewaySettings().database_url(use_sqlite=False))
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    url = GatewaySettings().database_url(use_sqlite=False)
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_async())
