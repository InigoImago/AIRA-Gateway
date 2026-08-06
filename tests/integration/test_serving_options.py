"""Thinking, structured output and batch embedding against the live stack (FRD-111/112/113).

The hermetic suites decide these features; what only shows up here is whether a **declaration
authored in Management** actually reaches the gateway and is enforced from the migrated schema.
That path failing is silent by design (`FRD-114` FR-8: the gateway never asks Management on the
request path), so a broken hop presents as "the feature does not work" rather than as an error.

The other thing only visible here is the audit: a batch that is metered as many requests and
recorded as one would leave "how much of our embedding traffic is batched" unanswerable from the
data — and nothing in-process compares the two.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import GATEWAY_URL
from .test_config_distribution import _management_engine, _run_relay, _wait_for

pytestmark = pytest.mark.integration


async def _declare(engine: AsyncEngine, name: str, payload: dict) -> None:
    """Author a declaration the way Management does, and wait for it to arrive.

    Published through the outbox rather than the API because authoring is Global-Admin-only and
    the dev realm's service accounts deliberately are not (`FRD-114` §5.4). The hop under test is
    the transport, which is the same either way.
    """
    management = await _management_engine()
    try:
        async with management.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO outbox_outboxevent (topic, key, event_type, payload, created_at)"
                    " VALUES ('aira.models', :key, 'model.upserted', :payload, now())"
                ),
                {"key": name, "payload": json.dumps({"name": name, **payload})},
            )
        relay = _run_relay()
        assert relay.returncode == 0, relay.stderr
        row = await _wait_for(
            engine, "SELECT model FROM model_catalog WHERE model = :name", {"name": name}
        )
        assert row is not None, "the declaration never reached the gateway's read-model"
    finally:
        await management.dispose()


async def _cleanup(engine: AsyncEngine, name: str) -> None:
    management = await _management_engine()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM model_catalog WHERE model = :name"), {"name": name}
            )
        async with management.begin() as connection:
            await connection.execute(
                text("DELETE FROM outbox_outboxevent WHERE key = :key"), {"key": name}
            )
    finally:
        await management.dispose()


async def test_a_thinking_declaration_authored_in_management_bounds_a_real_request(
    engine: AsyncEngine,
) -> None:
    """The declaration is what refuses an over-budget request. If the transport is broken the
    gateway decides from an empty declaration — and refuses *everything*, which looks like a
    gateway bug rather than a missing event."""
    name = f"itest-think-{uuid.uuid4().hex[:8]}"
    await _declare(
        engine,
        name,
        {
            "capabilities": ["generate", "thinking"],
            "max_output_tokens": 64000,
            "thinking": {"modes": ["limited"], "min_tokens": 128, "max_tokens": 24576},
        },
    )
    try:
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
            response = await client.post(
                f"/v1beta/models/{name}:generateContent",
                json={
                    "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                    "generationConfig": {"thinkingConfig": {"thinkingBudget": 99_999}},
                },
            )
        if response.status_code in (401, 403):
            pytest.skip("the gateway requires authentication for this route")
        # No provider serves this model, so the request cannot succeed either way. What is being
        # pinned is *which* refusal: the declared bound, named, rather than "model not found".
        assert response.status_code == 400, response.text
        assert "THINKING_TOKEN_COUNT_TOO_HIGH" in response.text
    finally:
        await _cleanup(engine, name)


async def test_the_schema_bounds_hold_on_a_deployed_gateway() -> None:
    """The bounds come from settings, so a deployment that shipped them unset would accept a
    schema of any depth — and nothing in-process reads the container's environment."""
    deep: dict = {"type": "STRING"}
    for _ in range(40):
        deep = {"type": "ARRAY", "items": deep}

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.post(
            "/v1beta/models/mock-1:generateContent",
            json={
                "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                "generationConfig": {"responseSchema": deep},
            },
        )
    if response.status_code in (401, 403):
        pytest.skip("the gateway requires authentication for this route")

    assert response.status_code == 400, response.text
    assert "nests deeper" in response.text


async def test_a_batch_is_recorded_under_the_verb_that_ran(engine: AsyncEngine) -> None:
    """Both embedding verbs write to one audit table and reporting has to tell them apart. A batch
    recorded as `embedContent` makes "how much of our embedding traffic is batched" unanswerable —
    and the difference is invisible to every hermetic test, which shares one in-process writer."""
    name = f"itest-embed-{uuid.uuid4().hex[:8]}"
    await _declare(
        engine, name, {"capabilities": ["embed"], "embedding": {"supports_batch": True}}
    )
    try:
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
            response = await client.post(
                "/v1beta/models/mock-1:batchEmbedContents",
                json={
                    "requests": [
                        {"content": {"parts": [{"text": "one"}]}},
                        {"content": {"parts": [{"text": "two"}]}},
                    ]
                },
            )
        if response.status_code in (401, 403):
            pytest.skip("the gateway requires authentication for this route")
        assert response.status_code in (200, 400), response.text
        if response.status_code == 200:
            assert len(response.json()["embeddings"]) == 2

            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text(
                            "SELECT operation FROM request_logs"
                            " ORDER BY created_at DESC LIMIT 1"
                        )
                    )
                ).first()
            assert row is not None and row[0] == "batchEmbedContents"
    finally:
        await _cleanup(engine, name)
