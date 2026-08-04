"""Integration tests — require the live Compose stack (``make up``).

Excluded from the default hermetic run via the ``integration`` marker; run with
``make test-integration``. This is the pattern for codifying stack-dependent checks
(the manual curl/e2e verifications) into a CI "integration" stage.
"""

import pytest
from sqlalchemy import text

from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine

pytestmark = pytest.mark.integration


async def test_postgres_reachable_and_queryable() -> None:
    engine = build_engine(GatewaySettings().database_url(use_sqlite=False))
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()
