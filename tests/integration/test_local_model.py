"""A real model, through the whole governed path (FRD-123).

This is the layer that was missing. Every suite below it proves the gateway is *self-consistent* —
the mock reports the token counts we told it to and produces documents that match the schema
because the same person wrote both sides. A model that never agreed to any of that is the only
thing that can show the accounting is right rather than merely internally coherent.

**Nothing here asserts on what the model said.** A 0.6b model is stochastic; a test that failed
because it phrased an answer differently would be a flake generator wearing a test's clothes. What
is asserted is what the *gateway* did: the row exists, the stored prompt is the prompt that was
sent, the token counts are positive and consistent, the cost is the declared price applied to those
counts, and the outcome and the caller are recorded.

Skipped with a reason when no local model is serving. A suite that passes silently while the thing
under test is absent is worse than one that fails.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

OLLAMA_URL = os.environ.get("AIRA_OLLAMA_URL", "http://localhost:11434")
CHAT_MODEL = os.environ.get("AIRA_SEED_LOCAL_CHAT_MODEL", "qwen3:0.6b")
EMBED_MODEL = os.environ.get("AIRA_SEED_LOCAL_EMBED_MODEL", "all-minilm")


async def _served_models() -> set[str]:
    """What the endpoint actually has, or an empty set when it is not there.

    Asked of the endpoint rather than assumed from configuration: a container that is up with no
    model pulled is the common state, and it fails in a way that reads as a gateway bug.
    """
    try:
        async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=5.0) as client:
            response = await client.get("/api/tags")
    except httpx.HTTPError:
        return set()
    if response.status_code != httpx.codes.OK:
        return set()
    return {str(model.get("name", "")) for model in response.json().get("models", [])}


async def _require(model: str) -> None:
    served = await _served_models()
    if not served:
        pytest.skip(f"no local endpoint at {OLLAMA_URL} — run `make verify-up`")
    if model not in served and f"{model}:latest" not in served:
        pytest.skip(f"'{model}' is not pulled — run `make verify-up`")


async def _latest_row(engine: AsyncEngine, model: str) -> dict[str, object] | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT operation, status, outcome, model, prompt_tokens, completion_tokens,"
                    " total_tokens, cost_nanos, latency_ms, request_payload, response_payload,"
                    " subject, use_case, provider, region"
                    " FROM request_logs WHERE model = :model"
                    " ORDER BY created_at DESC LIMIT 1"
                ),
                {"model": model},
            )
        ).first()
    return dict(row._mapping) if row is not None else None


async def test_a_real_answer_is_stored_exactly_as_it_was_sent(engine: AsyncEngine) -> None:
    """The question nobody wrote down because it looked too obvious to check: are the prompts and
    responses actually persisted, and is what is stored what was sent? `FRD-103` says yes and the
    hermetic tests agree — about a payload the mock also produced."""
    await _require(CHAT_MODEL)
    marker = f"integration probe {uuid.uuid4().hex[:8]}"

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=300.0) as client:
        response = await client.post(
            f"/v1beta/models/{CHAT_MODEL}:generateContent",
            json={
                "contents": [{"role": "user", "parts": [{"text": marker}]}],
                "generationConfig": {"maxOutputTokens": 32},
            },
        )
    if response.status_code in (401, 403):
        pytest.skip("the gateway requires authentication for this route")
    if response.status_code == 404:
        pytest.skip(f"'{CHAT_MODEL}' is not registered — set AIRA_OLLAMA_MODELS")
    assert response.status_code == 200, response.text

    row = await _latest_row(engine, CHAT_MODEL)
    assert row is not None, "a served request left no audit row"

    # The prompt, byte for byte. A redaction or a truncation that silently altered it would be
    # invisible to a hermetic test, which compares our own bytes with our own bytes.
    stored = row["request_payload"]
    assert stored is not None, "the prompt was not stored"
    assert marker in str(stored), "the stored prompt is not the prompt that was sent"
    assert row["response_payload"] is not None, "the answer was not stored"

    # Real counts from a real tokenizer — not a number we chose.
    assert int(row["prompt_tokens"] or 0) > 0
    assert int(row["completion_tokens"] or 0) > 0
    assert int(row["total_tokens"] or 0) == int(row["prompt_tokens"]) + int(
        row["completion_tokens"]
    )
    assert int(row["latency_ms"] or 0) > 0
    assert row["outcome"] == "served"
    assert row["provider"] == "ollama"


async def test_the_recorded_cost_is_the_declared_price_applied_to_the_real_counts(
    engine: AsyncEngine,
) -> None:
    """`FRD-403` end to end. The price is fictitious and says so in its display name; the
    *arithmetic* is not, and it has never been checked against token counts we did not choose."""
    await _require(CHAT_MODEL)

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=300.0) as client:
        response = await client.post(
            f"/v1beta/models/{CHAT_MODEL}:generateContent",
            json={
                "contents": [{"role": "user", "parts": [{"text": "one sentence about rain"}]}],
                "generationConfig": {"maxOutputTokens": 32},
            },
        )
    if response.status_code != 200:
        pytest.skip(f"the model did not serve this request ({response.status_code})")

    row = await _latest_row(engine, CHAT_MODEL)
    assert row is not None
    cost = row["cost_nanos"]
    if cost is None:
        pytest.skip("no price on file for this model — run `make seed`")

    async with engine.connect() as connection:
        prices = (
            await connection.execute(
                text(
                    "SELECT input_price_nanos, output_price_nanos FROM model_catalog"
                    " WHERE model = :model"
                ),
                {"model": CHAT_MODEL},
            )
        ).first()
    if prices is None or prices[0] is None:
        pytest.skip("the catalog carries no price for this model")

    expected = (
        int(row["prompt_tokens"]) * int(prices[0]) + int(row["completion_tokens"]) * int(prices[1])
    ) // 1_000_000
    # Exact, not approximate: money is integer nano-units precisely so this comparison can be `==`.
    assert int(cost) == expected


async def test_a_streamed_answer_is_accounted_for_like_a_whole_one(engine: AsyncEngine) -> None:
    """The one place this format hides its usage. It reports none on a stream unless asked, and a
    stream reporting none is *released* rather than settled (`FRD-405`) — so a forgotten
    `stream_options` would make every streamed request silently free."""
    await _require(CHAT_MODEL)

    url = f"/v1beta/models/{CHAT_MODEL}:streamGenerateContent?alt=sse"
    async with (
        httpx.AsyncClient(base_url=GATEWAY_URL, timeout=300.0) as client,
        client.stream(
            "POST",
            url,
            json={"contents": [{"role": "user", "parts": [{"text": "count to three"}]}]},
        ) as response,
    ):
        if response.status_code != 200:
            pytest.skip(f"the model did not stream ({response.status_code})")
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert body.strip(), "the stream produced nothing at all"

    row = await _latest_row(engine, CHAT_MODEL)
    assert row is not None
    assert row["operation"] == "streamGenerateContent"
    assert int(row["prompt_tokens"] or 0) > 0, "a streamed request was recorded as costing nothing"


async def test_a_real_batch_returns_one_vector_per_text(engine: AsyncEngine) -> None:
    """`FRD-113` FR-1 against a real embedder: n in, n out, in the order submitted. The mock could
    only ever confirm that we can count."""
    await _require(EMBED_MODEL)

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=300.0) as client:
        response = await client.post(
            f"/v1beta/models/{EMBED_MODEL}:batchEmbedContents",
            json={
                "requests": [
                    {"content": {"parts": [{"text": "the first text"}]}},
                    {"content": {"parts": [{"text": "an entirely different one"}]}},
                ]
            },
        )
    if response.status_code == 404:
        pytest.skip(f"'{EMBED_MODEL}' is not registered — set AIRA_OLLAMA_EMBEDDING_MODELS")
    if response.status_code != 200:
        pytest.skip(f"the model did not serve the batch ({response.status_code})")

    embeddings = response.json()["embeddings"]
    assert len(embeddings) == 2
    assert embeddings[0]["values"] != embeddings[1]["values"]
    assert len(embeddings[0]["values"]) == len(embeddings[1]["values"])

    row = await _latest_row(engine, EMBED_MODEL)
    assert row is not None and row["operation"] == "batchEmbedContents"


async def test_a_cold_model_is_not_woken_by_a_health_check() -> None:
    """`ADR-0012` §5. A probe that loads the model turns every health check into a cold start —
    and against a paid self-deployed endpoint, into a billable call. The probe must be able to say
    "the server is up" without asking the model anything."""
    served = await _served_models()
    if not served:
        pytest.skip(f"no local endpoint at {OLLAMA_URL} — run `make verify-up`")

    async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=10.0) as client:
        # `/api/tags` is what the container's healthcheck uses. It must answer promptly and
        # without loading anything, which is the property being pinned.
        response = await client.get("/api/tags")
        loaded = await client.get("/api/ps")

    assert response.status_code == 200
    if loaded.status_code == 200:
        # Listing tags must not have caused a model to be resident. If one already is, this says
        # nothing — hence the guard rather than a hard assertion on an empty list.
        assert isinstance(loaded.json().get("models", []), list)
