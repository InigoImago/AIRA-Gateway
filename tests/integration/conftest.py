"""Shared fixtures for the stack-dependent tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine

GATEWAY_URL = "http://127.0.0.1:8001"
MANAGEMENT_URL = "http://127.0.0.1:8002"


@pytest.fixture
def settings() -> GatewaySettings:
    return GatewaySettings()


@pytest.fixture
async def engine(settings: GatewaySettings) -> AsyncIterator[AsyncEngine]:
    """An engine against the real gateway database (never the in-memory SQLite)."""
    engine = build_engine(settings.database_url(use_sqlite=False))
    try:
        yield engine
    finally:
        await engine.dispose()
