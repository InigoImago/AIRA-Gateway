"""Fallback, rate limits, retention and the KIRA surface, against the running stack.

Each of these has hermetic tests that prove the logic. What only shows up here is the behaviour
**in the deployment**: a fallback across two separately configured servers, a `Retry-After` a
client would actually obey, a retention process that is a different container, and a second API
surface reaching the same real model as the first.

The fallback fixture is worth stating. Two servers are declared against one endpoint — `gpu-a`
offering a model that is not pulled, `gpu-b` offering one that is. `gpu-a` therefore answers 404
for real, over a real socket, and the chain has to move on. It is the closest thing to a second
machine that one machine can provide, and it exercises the part that matters: two transports, two
adapters, one decision.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import GATEWAY_URL, LOCAL_CHAT_MODEL_ID, Fixture

pytestmark = pytest.mark.integration

REAL_MODEL = "qwen3:0.6b"
GHOST_MODEL = "ghost-model:1b"
EMBED_MODEL = "all-minilm"
KIRA = "/kira/api/external"


async def _registered(fixture: Fixture) -> set[str]:
    """What the deployment actually serves.

    Asked **with the credential**: the model list requires one, and a helper that queried it
    anonymously would get a 401, read it as "nothing is registered", and skip the whole suite —
    silently, which is the failure mode a skip is supposed to prevent.
    """
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=15.0) as client:
        response = await client.get("/v1beta/models", headers=fixture.headers())
    assert response.status_code == httpx.codes.OK, response.text
    return {m["name"].removeprefix("models/") for m in response.json().get("models", [])}


async def _require(fixture: Fixture, *models: str) -> None:
    registered = await _registered(fixture)
    missing = [model for model in models if model not in registered]
    if missing:
        pytest.skip(f"not registered: {missing} — see AIRA_OPENAI_SERVERS")


async def _catalogue(engine: AsyncEngine, model: str) -> None:
    """Put a model in the catalog, approved (`FRD-307`).

    Needed since only catalogued models may be used. It also makes these two cases *more*
    realistic than they were: a model that is in the catalog and that the upstream no longer
    serves is exactly how a 404-from-every-candidate happens in practice — somebody removed it
    from the server and the declaration outlived it. Before, the scenario was reached with a name
    nobody had ever written down, which the gateway now refuses one step earlier and for a
    different reason.
    """
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM model_catalog WHERE model = :model"), {"model": model}
        )
        await connection.execute(
            text(
                "INSERT INTO model_catalog (model, display_name, provider, capabilities, approved)"
                " VALUES (:model, :model, 'test', '[\"generate\"]', true)"
            ),
            {"model": model},
        )


async def _fallback_chain(engine: AsyncEngine, slug: str, models: list[str]) -> None:
    """Configure the use case's dispatch chain, the way `FRD-300` distributes it."""
    import json

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO pipeline_configs (use_case, steps, fallback_models)"
                " VALUES (:slug, :steps, :models)"
                " ON CONFLICT (use_case) DO UPDATE SET fallback_models = :models"
            ),
            {"slug": slug, "steps": json.dumps([]), "models": json.dumps(models)},
        )


# == 1. fallback across two separately configured servers ========================================


async def test_a_dead_candidate_is_passed_over_and_the_next_one_answers(
    engine: AsyncEngine, fixture: Fixture
) -> None:
    """The chain crosses two adapters with two transports. `gpu-a` returns a real 404 from a real
    socket; the request must still be served, by `gpu-b`."""
    await _require(fixture, REAL_MODEL, GHOST_MODEL)
    await _fallback_chain(engine, fixture.slug, [REAL_MODEL])

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=300.0) as client:
        response = await client.post(
            f"/v1beta/models/{GHOST_MODEL}:generateContent",
            json={
                "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
                "generationConfig": {"maxOutputTokens": 600},
            },
            headers=fixture.headers(),
        )

    assert response.status_code == 200, response.text
    assert response.json()["modelVersion"] == REAL_MODEL


async def test_the_audit_names_both_the_model_asked_for_and_the_one_that_answered(
    engine: AsyncEngine, fixture: Fixture
) -> None:
    """`FRD-122` FR-3. Without `requested_model` beside `model`, "why did the spend on that server
    triple" has no answer — the substitution is invisible in every report."""
    await _require(fixture, REAL_MODEL, GHOST_MODEL)
    await _fallback_chain(engine, fixture.slug, [REAL_MODEL])

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=300.0) as client:
        served = await client.post(
            f"/v1beta/models/{GHOST_MODEL}:generateContent",
            json={
                "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
                "generationConfig": {"maxOutputTokens": 600},
            },
            headers=fixture.headers(),
        )
    if served.status_code != 200:
        pytest.skip(f"the chain did not serve ({served.status_code}): {served.text[:120]}")

    row = await _wait_for_row(engine, fixture.slug)
    assert row is not None
    assert row["requested_model"] == GHOST_MODEL
    assert row["model"] == REAL_MODEL
    assert str(row["model_selection"]).startswith("fallback")
    # Provenance follows the model that actually answered, not the one that was asked for.
    assert row["provider"] == "gpu-b"


async def test_a_chain_with_nothing_behind_it_fails_as_a_precondition_not_an_outage(
    fixture: Fixture, engine: AsyncEngine
) -> None:
    """A 404 from every candidate is an upstream problem and reports as one; being *excluded* is a
    configuration problem an operator can fix. Here the only candidate is genuinely broken, so a
    502 is the correct answer — the distinction is what `NoCapableModel` exists for."""
    await _require(fixture, GHOST_MODEL)
    await _catalogue(engine, GHOST_MODEL)

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120.0) as client:
        response = await client.post(
            f"/v1beta/models/{GHOST_MODEL}:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "hello"}]}]},
            headers=fixture.headers(),
        )

    assert response.status_code == 502, response.text
    assert response.json()["error"]["status"] == "UNAVAILABLE"


async def test_a_failed_chain_is_recorded_as_an_upstream_error(
    engine: AsyncEngine, fixture: Fixture
) -> None:
    await _require(fixture, GHOST_MODEL)
    await _catalogue(engine, GHOST_MODEL)
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120.0) as client:
        await client.post(
            f"/v1beta/models/{GHOST_MODEL}:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "hello"}]}]},
            headers=fixture.headers(),
        )

    row = await _wait_for_row(engine, fixture.slug)
    assert row is not None
    assert row["outcome"] == "upstream_error"
    assert row["status"] == 502


# == 2. rate limits, as a client would meet them =================================================


async def _rate_limit(engine: AsyncEngine, slug: str, rpm: int, burst: int) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rate_limits (id, use_case, scope, subject, limit_rpm, burst, enabled)"
                " VALUES (:id, :slug, 'use_case', '', :rpm, :burst, true)"
            ),
            {
                "id": 900_000_000 + int(uuid.uuid4().int % 90_000_000),
                "slug": slug,
                "rpm": rpm,
                "burst": burst,
            },
        )


async def test_over_the_limit_returns_429_with_a_retry_after_a_client_can_obey(
    engine: AsyncEngine, fixture: Fixture
) -> None:
    """A `Retry-After` of `0` invites the immediate retry the limit exists to stop, and a missing
    one leaves a well-behaved client with nothing but a busy loop."""
    await _rate_limit(engine, fixture.slug, rpm=60, burst=2)
    await asyncio.sleep(6)  # the gateway caches limit configuration for a few seconds

    statuses = []
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        for _ in range(5):
            response = await client.post(
                "/v1beta/models/mock-1:generateContent",
                json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
                headers=fixture.headers(),
            )
            statuses.append(response.status_code)
            if response.status_code == 429:
                assert int(response.headers["Retry-After"]) >= 1
                assert response.json()["error"]["status"] == "RESOURCE_EXHAUSTED"
                return

    pytest.fail(f"the limit never bit: {statuses}")


async def test_a_throttled_request_is_recorded_as_rate_limited(
    engine: AsyncEngine, fixture: Fixture
) -> None:
    """`FRD-122`: a refusal that leaves no trace is a control nobody can review."""
    await _rate_limit(engine, fixture.slug, rpm=60, burst=1)
    await asyncio.sleep(6)

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        for _ in range(4):
            await client.post(
                "/v1beta/models/mock-1:generateContent",
                json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
                headers=fixture.headers(),
            )

    rows = await _wait_for_rows(engine, fixture.slug, 2)
    outcomes = {row["outcome"] for row in rows}
    if "rate_limited" not in outcomes:
        pytest.skip(f"the limit did not bite in this window: {outcomes}")
    refusal = next(row for row in rows if row["outcome"] == "rate_limited")
    assert refusal["status"] == 429
    assert not refusal["completion_tokens"], "a refused request was recorded as producing output"


# == 3. retention, which is a different process ==================================================


async def test_retention_removes_the_payloads_and_keeps_the_row(
    engine: AsyncEngine, fixture: Fixture
) -> None:
    """`FRD-404`. The distinction the whole feature turns on: the *content* expires, the
    *evidence* does not. A retention pass that took the row with it would erase the record that
    the request ever happened, which is the opposite of what a retention policy is for.

    Run as a subprocess because that is how it is deployed — an hourly container, not a thread.
    """
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        served = await client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "expire me"}]}]},
            headers=fixture.headers(),
        )
    assert served.status_code == 200
    row = await _wait_for_row(engine, fixture.slug)
    assert row is not None and row["request_payload"] is not None

    # Expire immediately, and age the row past the boundary.
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE use_cases SET retention_days = 1 WHERE slug = :slug"),
            {"slug": fixture.slug},
        )
        await connection.execute(
            text(
                "UPDATE request_logs SET created_at = now() - interval '3 days'"
                " WHERE use_case = :slug"
            ),
            {"slug": fixture.slug},
        )

    result = await _run_retention()
    assert result.returncode == 0, result.stderr

    after = await _wait_for_row(engine, fixture.slug)
    assert after is not None, "retention deleted the audit row along with the payload"
    assert after["request_payload"] is None, "the prompt outlived its retention period"
    assert after["response_payload"] is None
    # The evidence survives.
    assert after["outcome"] == "served"
    assert int(after["prompt_tokens"] or 0) > 0


async def test_retention_leaves_a_row_inside_its_window_alone(
    engine: AsyncEngine, fixture: Fixture
) -> None:
    """The other half. A pruner that cannot tell "expired" from "recent" deletes everything the
    first time it runs, and nothing about the run would say so."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        await client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "keep me"}]}]},
            headers=fixture.headers(),
        )
    await _wait_for_row(engine, fixture.slug)

    await _run_retention()

    row = await _wait_for_row(engine, fixture.slug)
    assert row is not None
    assert row["request_payload"] is not None, "a payload inside its window was pruned"


# == 4. the KIRA surface, against the same real model ============================================


async def test_the_predecessors_surface_reaches_the_real_model(fixture: Fixture) -> None:
    """`FRD-107`: a migrating client changes a base URL and nothing else. Here that claim meets a
    model, an integer id and the same controls as the Gemini surface."""
    await _require(fixture, REAL_MODEL)

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=300.0) as client:
        response = await client.post(
            f"{KIRA}/chat",
            json={
                "request": {"parts": [{"text": "Say hello in one word."}]},
                "model_id": LOCAL_CHAT_MODEL_ID,
                "maxTokens": 900,
            },
            headers=fixture.headers(),
        )

    if response.status_code == 404 and f"id {LOCAL_CHAT_MODEL_ID}" in response.text:
        pytest.skip("no numeric id for the local model — run tools/seed_local_catalog.py")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert set(payload) == {"parts", "usage_data"}
    assert payload["parts"][0]["text"]
    assert payload["usage_data"]["token_output"] > 0
    # Announced as transitional on every response, including this one (`ADR-0010` Option C).
    assert response.headers.get("Deprecation") == "true"


async def test_both_surfaces_record_the_same_request_the_same_way(
    engine: AsyncEngine, fixture: Fixture
) -> None:
    """The only way to be sure the shared controls were *run* rather than merely present: send one
    request through each and compare what the audit kept."""
    await _require(fixture, REAL_MODEL)
    body = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}], "generationConfig": {}}

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=300.0) as client:
        gemini = await client.post(
            f"/v1beta/models/{REAL_MODEL}:generateContent",
            json={**body, "generationConfig": {"maxOutputTokens": 900}},
            headers=fixture.headers(),
        )
        kira = await client.post(
            f"{KIRA}/chat",
            json={
                "request": {"parts": [{"text": "hi"}]},
                "model_id": LOCAL_CHAT_MODEL_ID,
                "maxTokens": 900,
            },
            headers=fixture.headers(),
        )
    if gemini.status_code != 200 or kira.status_code != 200:
        pytest.skip(f"gemini={gemini.status_code} kira={kira.status_code}")

    rows = await _wait_for_rows(engine, fixture.slug, 2)
    by_api = {row["api"]: row for row in rows}
    assert {"gemini", "kira"} <= set(by_api), f"one surface left no row: {list(by_api)}"

    for api, row in by_api.items():
        assert row["outcome"] == "served", api
        assert row["model"] == REAL_MODEL, api
        # **That it is recorded, not what it is called.** This asserted `"gpu-b"`, a server name
        # from *this file's own* two-server fallback fixture — so on any other deployment the test
        # failed for naming rather than for behaviour, and it did: `make showcase` calls its single
        # server `local`. A test that hard-codes a deployment's `AIRA_OPENAI_SERVERS` entry is
        # testing the environment.
        #
        # What `FRD-115` actually promises is that the column is **evidence**: blank is neither a
        # claim nor a record. And what *this* test is about is that the two surfaces agree — so the
        # provenance is compared between them, below, rather than against a constant.
        assert row["provider"], f"{api}: no provenance recorded"
        assert int(row["prompt_tokens"] or 0) > 0, api
        assert row["use_case"] == fixture.slug, api
        assert row["credential"], api

    # The property the test is named after: one request, two spellings, the same facts kept.
    for field in ("provider", "publisher", "region", "model"):
        assert by_api["gemini"][field] == by_api["kira"][field], (
            f"the two surfaces recorded a different {field}: "
            f"gemini={by_api['gemini'][field]!r} kira={by_api['kira'][field]!r}"
        )


async def test_a_kira_caller_meets_the_same_budget(engine: AsyncEngine, fixture: Fixture) -> None:
    """A compatibility surface that skipped the controls would be a way around every one of them,
    which is why `api/serving.py` exists rather than a second copy."""
    await fixture.budget(limit_requests=1)

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=120.0) as client:
        first = await client.post(
            f"{KIRA}/chat",
            json={
                "request": {"parts": [{"text": "hi"}]},
                "model_id": LOCAL_CHAT_MODEL_ID,
                "maxTokens": 200,
            },
            headers=fixture.headers(),
        )
        second = await client.post(
            f"{KIRA}/chat",
            json={
                "request": {"parts": [{"text": "hi"}]},
                "model_id": LOCAL_CHAT_MODEL_ID,
                "maxTokens": 200,
            },
            headers=fixture.headers(),
        )

    if first.status_code not in (200, 429):
        pytest.skip(f"the surface did not serve ({first.status_code}): {first.text[:120]}")
    assert second.status_code == 429, second.text
    # The predecessor's vocabulary, not Google's — that is the whole point of the surface.
    assert second.json()["code"] == "EXTERNAL_KI_API_TOO_MANY_REQUEST"


# == helpers =====================================================================================


async def _run_retention() -> subprocess.CompletedProcess[str]:
    """The retention pass as it is deployed — a separate process, not a thread. Run off the event
    loop, because a blocking call inside an async test is how a suite starts timing out for
    reasons that have nothing to do with the system under test."""
    return await asyncio.to_thread(
        subprocess.run,
        ["docker", "exec", "aira-gateway", "python", "-m", "aira_gateway.retention"],
        capture_output=True,
        text=True,
        timeout=120,
    )


async def _all_rows(engine: AsyncEngine, slug: str) -> list[dict]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT api, operation, status, outcome, model, requested_model, model_selection,"
                # The provenance triple, whole. `publisher` was the one of the three left out, and
                # a comparison across surfaces can only cover the columns it selects.
                " provider, publisher, region, prompt_tokens, completion_tokens, request_payload,"
                " response_payload, use_case, credential"
                " FROM request_logs WHERE use_case = :slug ORDER BY created_at"
            ),
            {"slug": slug},
        )
        return [dict(row._mapping) for row in result]


async def _wait_for_rows(
    engine: AsyncEngine, slug: str, count: int = 1, *, wait: float = 25.0
) -> list[dict]:
    """Wait until ``count`` audit rows exist.

    The write is deliberately **off the request path** (`FRD-405`), so reading immediately after a
    200 is a race — and waiting for *one* row is not enough when the test sent two requests. That
    caught me three times in this suite, each time producing a failure that reads exactly like a
    lost audit row, which is one of the most serious failures this system could have. Waiting for
    the count the test actually expects is the only version that cannot lie in either direction.
    """
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        rows = await _all_rows(engine, slug)
        if len(rows) >= count or asyncio.get_running_loop().time() > deadline:
            return rows
        await asyncio.sleep(0.3)


async def _wait_for_row(engine: AsyncEngine, slug: str, *, wait: float = 25.0) -> dict | None:
    rows = await _wait_for_rows(engine, slug, 1, wait=wait)
    return rows[-1] if rows else None
