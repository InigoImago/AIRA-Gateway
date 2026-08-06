"""The governed path, end to end, against a running gateway.

Every other suite tests a control in isolation. This one sends **real HTTP requests through a
deployed gateway with a real credential bound to a real use case**, and then reads the database to
see what the system decided — which is the only way to check the controls in the order and
combination a caller actually meets them.

The cases are the ones an operator would ask about before trusting the thing:

- a request is served, and the prompt **and the answer** are in the database afterwards
- a use case that must not store payloads stores **neither**, while still recording that the
  request happened — an audit that vanishes when payloads are off is not an audit
- a budget admits what fits and refuses what does not, with the right status
- a refusal is recorded too, because "what was asked" and "what was served" are different
  questions (`FRD-122`)
- a credential bound to one use case cannot spend another's budget
- concurrent traffic does not over-admit, double-count, or lose rows

Every fixture is created with a unique slug and removed afterwards, so the suite is re-runnable
against a stack that has other data in it.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import GATEWAY_URL, Fixture  # noqa: F401  (Fixture is the annotation)

pytestmark = pytest.mark.integration

MODEL = "mock-1"


async def _generate(fixture: Fixture, text_in: str = "hallo", **config: object) -> httpx.Response:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
        body: dict[str, object] = {"contents": [{"role": "user", "parts": [{"text": text_in}]}]}
        if config:
            body["generationConfig"] = config
        return await client.post(
            f"/v1beta/models/{MODEL}:generateContent", json=body, headers=fixture.headers()
        )


async def _settled(fixture: Fixture, expected: int, timeout: float = 10.0) -> list[dict]:
    """Wait for the audit rows. The write is deliberately **off the request path** (`FRD-405`), so
    reading immediately after a 200 is a race — and a test that polled once would be flaky in a way
    that looks like a lost row."""
    deadline = asyncio.get_running_loop().time() + timeout
    rows: list[dict] = []
    while asyncio.get_running_loop().time() < deadline:
        rows = await fixture.rows()
        if len(rows) >= expected:
            return rows
        await asyncio.sleep(0.2)
    return rows


# == 1. it works, and it is written down =========================================================


async def test_a_served_request_stores_the_prompt_and_the_answer(fixture: Fixture) -> None:
    marker = f"marker-{uuid.uuid4().hex[:8]}"
    response = await _generate(fixture, marker)
    assert response.status_code == 200, response.text

    rows = await _settled(fixture, 1)
    assert len(rows) == 1, "a served request left no audit row"
    row = rows[0]

    assert row["outcome"] == "served"
    assert row["status"] == 200
    assert row["model"] == MODEL
    assert marker in str(row["request_payload"]), "the stored prompt is not the prompt sent"
    assert row["response_payload"] is not None, "the answer was not stored"
    assert int(row["prompt_tokens"] or 0) > 0


async def test_the_row_names_the_use_case_the_subject_and_the_calling_key(
    fixture: Fixture,
) -> None:
    """`FRD-122` FR-5: five keys issued for one use case are one identity in the log without the
    credential column — and a leaked key can be revoked but its blast radius cannot be assessed."""
    assert (await _generate(fixture)).status_code == 200

    row = (await _settled(fixture, 1))[0]
    assert row["use_case"] == fixture.slug
    assert row["subject"] == "integration-probe"
    assert row["credential"], "the calling system is not identified"


# == 2. a use case that must not store payloads ==================================================


async def test_payloads_off_stores_neither_prompt_nor_answer_but_still_records_the_request(
    fixture: Fixture,
) -> None:
    """The half that is easy to get wrong. Turning storage off must not turn the *audit* off:
    "this use case made 4 000 requests last month" has to survive a privacy setting, or the
    setting quietly removes the evidence along with the content (`FRD-404`)."""
    await fixture.set_store_payloads(False)
    marker = f"secret-{uuid.uuid4().hex[:8]}"

    assert (await _generate(fixture, marker)).status_code == 200

    row = (await _settled(fixture, 1))[0]
    assert row["request_payload"] is None, "the prompt was stored although storage is off"
    assert row["response_payload"] is None, "the answer was stored although storage is off"

    # Everything that is not content is still there.
    assert row["outcome"] == "served"
    assert row["use_case"] == fixture.slug
    assert int(row["prompt_tokens"] or 0) > 0, "token accounting was lost with the payloads"


async def test_the_marker_is_nowhere_in_the_table_when_storage_is_off(
    fixture: Fixture, engine: AsyncEngine
) -> None:
    """Asserting the two columns are NULL is not quite enough: a payload copied into some other
    column would satisfy that and still be stored. This asks the table."""
    await fixture.set_store_payloads(False)
    marker = f"secret-{uuid.uuid4().hex[:8]}"
    assert (await _generate(fixture, marker)).status_code == 200
    await _settled(fixture, 1)

    async with engine.connect() as connection:
        found = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM request_logs WHERE use_case = :slug"
                    " AND (request_payload::text LIKE :like OR response_payload::text LIKE :like)"
                ),
                {"slug": fixture.slug, "like": f"%{marker}%"},
            )
        ).scalar()
    assert found == 0


# == 3. budgets ==================================================================================


async def test_a_budget_admits_what_fits(fixture: Fixture) -> None:
    await fixture.budget(limit_requests=5)
    assert (await _generate(fixture)).status_code == 200


async def test_an_exhausted_request_budget_refuses_with_429(fixture: Fixture) -> None:
    """One request allowed, two sent. The second must be refused — and refused *before* the
    upstream is called, which is what makes a budget a control rather than a report."""
    await fixture.budget(limit_requests=1)

    first = await _generate(fixture)
    second = await _generate(fixture)

    assert first.status_code == 200, first.text
    assert second.status_code == 429, second.text
    assert second.json()["error"]["status"] == "RESOURCE_EXHAUSTED"


async def test_a_refused_request_is_recorded_as_a_refusal(fixture: Fixture) -> None:
    """`FRD-122`'s central point: the log used to record what was *served*, so a request refused
    over budget left no trace at all — and "who tried to spend past the limit" had no answer."""
    await fixture.budget(limit_requests=1)
    await _generate(fixture)
    assert (await _generate(fixture)).status_code == 429

    rows = await _settled(fixture, 2)
    outcomes = [row["outcome"] for row in rows]
    assert "served" in outcomes
    assert "budget_exceeded" in outcomes, f"the refusal was not recorded: {outcomes}"

    refusal = next(row for row in rows if row["outcome"] == "budget_exceeded")
    assert refusal["status"] == 429
    # A refused request produced no tokens, and recording some would inflate every report.
    assert not refusal["completion_tokens"]


async def test_a_token_budget_refuses_once_the_tokens_are_spent(fixture: Fixture) -> None:
    """A token limit binds on a dimension a request-count limit cannot see."""
    await fixture.budget(limit_tokens=1)

    await _generate(fixture, "the first request spends the whole allowance")
    second = await _generate(fixture)

    assert second.status_code == 429


# == 4. the tenant boundary ======================================================================


async def test_a_key_bound_to_one_use_case_cannot_select_another(
    fixture: Fixture, engine: AsyncEngine
) -> None:
    """`FRD-205`: a key carries its use case, so a selector naming a different one is a 403 rather
    than a quiet re-attribution. Without this, one team's traffic could be charged to another's
    budget by adding a header."""
    other = f"itest-{uuid.uuid4().hex[:8]}"
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO use_cases (slug, name) VALUES (:slug, :slug)"), {"slug": other}
        )
    try:
        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60.0) as client:
            response = await client.post(
                f"/v1beta/models/{MODEL}:generateContent",
                json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
                headers={**fixture.headers(), "X-AIRA-Use-Case": other},
            )
        assert response.status_code == 403, response.text
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM use_cases WHERE slug = :slug"), {"slug": other}
            )


async def test_a_revoked_key_stops_working(fixture: Fixture, engine: AsyncEngine) -> None:
    """Revocation is terminal in the read-model (`ADR-0007`). A key that kept working after being
    revoked is the security control failing silently."""
    assert (await _generate(fixture)).status_code == 200

    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE api_keys SET is_active = false WHERE use_case = :slug"),
            {"slug": fixture.slug},
        )

    assert (await _generate(fixture)).status_code == 401


async def test_no_credential_is_refused_before_anything_else(fixture: Fixture) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.post(
            f"/v1beta/models/{MODEL}:generateContent",
            json={"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
        )
    assert response.status_code == 401
    # And it wrote no row: a row per unauthenticated request makes the audit table a target.
    assert await fixture.rows() == []


# == 5. stability ================================================================================


async def test_concurrent_requests_are_all_admitted_and_all_recorded(fixture: Fixture) -> None:
    """No limit configured, so every one must be served — and every one must leave a row. The
    audit writer is off the request path with a bounded queue (`FRD-405`), and "the queue silently
    dropped a few under load" is exactly the failure that would look like nothing at all."""
    count = 20
    responses = await asyncio.gather(*[_generate(fixture, f"parallel {i}") for i in range(count)])

    assert [r.status_code for r in responses] == [200] * count
    rows = await _settled(fixture, count, timeout=20.0)
    assert len(rows) == count, f"{count - len(rows)} of {count} requests left no audit row"


async def test_a_budget_is_not_overshot_by_concurrent_requests(fixture: Fixture) -> None:
    """The race `FRD-405` closed: without an atomic reservation, N requests all read the same
    stale usage and all pass a limit with room for one. Twenty at once against a budget of five —
    the count that gets through must be the limit, not the concurrency."""
    await fixture.budget(limit_requests=5)

    responses = await asyncio.gather(*[_generate(fixture, f"burst {i}") for i in range(20)])
    admitted = sum(1 for r in responses if r.status_code == 200)
    refused = sum(1 for r in responses if r.status_code == 429)

    from collections import Counter

    seen = Counter(r.status_code for r in responses)
    assert admitted + refused == 20, f"statuses were {dict(seen)}; a body: {responses[0].text}"
    # The limit is a ceiling. Admitting *fewer* is legitimate (an in-flight reservation is
    # conservative and released afterwards); admitting more is the defect.
    assert admitted <= 5, f"{admitted} requests were admitted against a limit of 5"
    assert admitted >= 1, "the limit refused everything, which is a different bug"


async def test_the_gateway_stays_healthy_under_that_load(fixture: Fixture) -> None:
    """A control that works and a process that survives are different claims."""
    await asyncio.gather(*[_generate(fixture, f"load {i}") for i in range(20)])

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        ready = await client.get("/readyz")

    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready"
    # Degradation is reported rather than hidden; under this load nothing should have fallen back.
    assert body["degraded"] is False, f"the gateway degraded under load: {body['fallbacks']}"
