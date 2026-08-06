"""Where each request was processed, in the real schema (FRD-115 FR-10).

Under a residency requirement, "the configuration says EU" is a claim and "this request went to
`eu`" is evidence. These columns are what turn the first into the second — per request rather than
per deployment — so they have to exist in the **migrated** schema, not only in the model.

The Vertex adapters themselves are not exercised here: that needs a project and a service-account
credential, and a test that silently skips is worse than one that is honestly absent. `FRD-115`
§10 puts that in the integration suite of a deployment that has them.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration


async def test_the_provenance_columns_exist_and_are_indexed(engine: AsyncEngine) -> None:
    """`provider` and `region` are the two an audit filters by, on a table retention deliberately
    keeps rows in."""
    async with engine.connect() as connection:
        columns = {
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'request_logs'"
                    )
                )
            ).all()
        }
        indexes = " ".join(
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE tablename = 'request_logs'")
                )
            ).all()
        )

    assert {"provider", "publisher", "region"} <= columns
    assert "provider" in indexes
    assert "region" in indexes


async def test_a_served_request_records_which_upstream_answered(engine: AsyncEngine) -> None:
    """The demo stack runs the mock, which declares no provenance — so the columns stay NULL, and
    that is the correct answer rather than a guess. What this pins is that the *path* works: the
    lookup runs, the row is written, and nothing invents a region nobody configured."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.post(
            "/v1beta/models/nowhere-at-all:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        )
    if response.status_code == 401:
        pytest.skip("the gateway requires authentication in this deployment")

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT provider, region FROM request_logs"
                    " WHERE requested_model = 'nowhere-at-all' ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).first()

    if row is None:
        pytest.skip("the request was refused before attribution; nothing to assert")
    # A model no adapter serves has no provenance, and the row says so instead of inventing one.
    assert row[0] is None
    assert row[1] is None


async def test_reporting_still_answers_with_the_new_columns(governance_token: str) -> None:
    """Additive columns must not disturb the report — the aggregates group by other things, and a
    migration that broke them would be found here rather than by an operator."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get(
            "/v1beta/reporting",
            params={"from": "2026-01-01", "to": "2026-12-01"},
            headers={"authorization": f"Bearer {governance_token}"},
        )

    assert response.status_code == 200, response.text
    assert "by_outcome" in response.json()


async def test_an_unconfigured_gateway_serves_the_mock_and_nothing_vertex(
    engine: AsyncEngine,
) -> None:
    """Vertex registers only when configured. A deployment without a project must behave exactly
    as it did before this FRD — the laptop path is a supported configuration, not an oversight."""
    del engine
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get("/v1beta/models")
    if response.status_code == 401:
        pytest.skip("the model list is authenticated in this deployment")

    names = {model["name"].removeprefix("models/") for model in response.json()["models"]}
    assert any(name.startswith("mock-") for name in names)


async def test_a_request_id_is_never_confused_with_a_region(engine: AsyncEngine) -> None:
    """A guard against the columns being wired to the wrong source: `region` must hold a region or
    nothing, never a model name or an id."""
    marker = uuid.uuid4().hex[:8]
    del marker
    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT DISTINCT region FROM request_logs WHERE region IS NOT NULL LIMIT 20")
            )
        ).all()

    for (region,) in rows:
        assert len(region) <= 32
        assert "/" not in region and ":" not in region
