"""A model declaration, authored in Management and enforced by the gateway (FRD-114).

The hermetic suites prove each half. What only shows up here is the **path between them**: a
declaration written through the API has to travel the outbox → relay → Kafka → consumer route and
land in the gateway's read-model, in the migrated schema, before it decides anything.

FR-8's whole point is that the gateway never asks Management on the request path — so if this path
is broken, nothing fails. The gateway simply keeps deciding from a stale or empty declaration,
which reads as "the feature does not work" and looks like a gateway bug.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import MANAGEMENT_URL
from .test_config_distribution import _management_engine, _run_relay, _wait_for

pytestmark = pytest.mark.integration

_DECLARED_COLUMNS = {
    "capabilities",
    "publisher",
    "platform",
    "addressing",
    "underlying_model",
    "context_window",
    "max_output_tokens",
    "default_max_output_tokens",
    "thinking",
    "embedding",
    "attachments",
    "hosting",
    "deprecated",
    "numeric_id",
}


async def test_the_gateway_table_was_renamed_and_extended(engine: AsyncEngine) -> None:
    """``model_prices`` became ``model_catalog``: a table that decides whether a thinking budget
    is accepted must not be called *prices*. The rename is only real if the migration did it."""
    async with engine.connect() as connection:
        tables = {
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = 'public'"
                    )
                )
            ).all()
        }
        columns = {
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'model_catalog'"
                    )
                )
            ).all()
        }

    assert "model_catalog" in tables
    assert "model_prices" not in tables, "the old name survived the rename"
    assert columns >= _DECLARED_COLUMNS, f"missing: {_DECLARED_COLUMNS - columns}"


async def test_a_declaration_published_by_the_relay_reaches_the_gateway(
    engine: AsyncEngine,
) -> None:
    """The whole point of FR-8 is that the gateway never asks Management on the request path — so
    when this route is broken, **nothing fails**. The gateway simply keeps deciding from an empty
    declaration, which presents as "the feature does not work" and looks like a gateway bug.

    Published through the outbox rather than the API because authoring is Global-Admin-only
    (§5.4) and the dev realm's service accounts deliberately are not. The restriction is a
    requirement, not an obstacle to route around — and the hop under test is the transport, which
    is the same either way.
    """
    name = f"itest-model-{uuid.uuid4().hex[:8]}"
    management = await _management_engine()
    try:
        async with management.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO outbox_outboxevent (topic, key, event_type, payload, created_at)"
                    " VALUES ('aira.models', :key, 'model.upserted', :payload, now())"
                ),
                {
                    "key": name,
                    "payload": json.dumps(
                        {
                            "name": name,
                            "display_name": "Integration probe",
                            "provider": "anthropic",
                            "capabilities": ["generate", "thinking"],
                            "publisher": "anthropic",
                            "platform": "vertex",
                            "context_window": 200000,
                            "max_output_tokens": 64000,
                            "default_max_output_tokens": 4096,
                            "thinking": {"modes": ["auto", "limited"], "max_tokens": 32000},
                            "hosting": "managed",
                            "deprecated": False,
                        }
                    ),
                },
            )

        relay = _run_relay()
        assert relay.returncode == 0, relay.stderr

        row = await _wait_for(
            engine,
            "SELECT capabilities, max_output_tokens, publisher, hosting, context_window"
            " FROM model_catalog WHERE model = :name",
            {"name": name},
        )
        assert row is not None, "the declaration never reached the gateway's read-model"
        assert set(row[0]) == {"generate", "thinking"}
        assert row[1] == 64000
        assert row[2] == "anthropic"
        assert row[3] == "managed"
        # `FRD-132` §11. The newest field on this event, and the one whose absence is silent: the
        # console offers it, the database stores it, Kafka carries it, and a consumer that has not
        # been taught the key drops it with no error anywhere. Asserted here as well as in
        # `tools/tests/test_a_model_event_is_applied_whole.py`, because that one reads source and
        # this one reads the wire.
        assert row[4] == 200000
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM model_catalog WHERE model = :name"), {"name": name}
            )
        async with management.begin() as connection:
            await connection.execute(
                text("DELETE FROM outbox_outboxevent WHERE key = :key"), {"key": name}
            )
        await management.dispose()


async def test_management_refuses_a_declaration_that_cannot_work(governance_token: str) -> None:
    """The catalog is a runtime authority, so a self-contradictory declaration is refused where it
    is written — not discovered as a vendor error on every request against that model."""
    async with httpx.AsyncClient(base_url=MANAGEMENT_URL, timeout=30.0) as client:
        response = await client.post(
            "/api/v1/models/",
            headers={"authorization": f"Bearer {governance_token}"},
            json={
                "name": f"itest-impossible-{uuid.uuid4().hex[:8]}",
                "capabilities": ["generate", "thinking"],
                "max_output_tokens": 4096,
                # Anthropic draws thinking tokens from the output allowance, so this model could
                # never answer (FRD-119 §5.4).
                "thinking": {"modes": ["limited"], "max_tokens": 8192},
            },
        )
    if response.status_code == 403:
        pytest.skip("this token may not author the catalog; the restriction is the requirement")
    assert response.status_code == 400, response.text
    assert "max_output_tokens" in response.text


async def test_the_gateway_reports_what_it_believes_about_each_model() -> None:
    """`GET /v1beta/models` is what makes a wrong declaration inspectable rather than a mystery —
    it says what *this gateway* thinks, which is the thing enforcement actually reads."""
    from .conftest import GATEWAY_URL

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get("/v1beta/models")
    if response.status_code == 401:
        pytest.skip("the model list is authenticated in this deployment")
    assert response.status_code == 200, response.text

    models = response.json()["models"]
    assert models
    assert all("airaDeclared" in model for model in models)
    assert all("airaCapabilities" in model for model in models)
