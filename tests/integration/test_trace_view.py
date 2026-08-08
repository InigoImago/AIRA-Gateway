"""The trace overview against the live stack (`FRD-502`).

The hermetic suite drives the same endpoint through `TestClient` on SQLite. Three things only this
layer can say:

- **Postgres, not SQLite.** The cursor comparison is written out as `created_at < :at OR
  (created_at = :at AND id < :id)` precisely because SQLite has no tuple comparison — and a
  comparison written for one dialect is a comparison tested on one dialect. Here `created_at` is a
  real `timestamptz` and `id` a real `uuid`, and the ordering has to hold across both types.
- **Rows a real request wrote**, not rows a test invented. The columns a trace shows are the ones
  the recorder fills in; a test that seeds its own rows agrees with itself about what a served
  request looks like.
- **A real token**, so the scope comes from Keycloak's roles rather than from an override.

Nothing here asserts an answer's content.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

MODEL = "qwen3:0.6b"
SHORT = {"generationConfig": {"maxOutputTokens": 8}}


async def _generate(client: httpx.AsyncClient, fixture, text_in: str = "Say OK") -> httpx.Response:
    return await client.post(
        f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
        headers=fixture.headers(),
        json={"contents": [{"parts": [{"text": text_in}]}], **SHORT},
        timeout=180.0,
    )


async def _traces(client: httpx.AsyncClient, token: str, **params) -> dict:
    response = await client.get(
        f"{GATEWAY_URL}/v1beta/traces",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30.0,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_real_request_appears_as_a_trace_with_what_it_actually_cost(
    fixture, governance_token
) -> None:
    """The columns are filled in by the recorder, so this is the only place they can be checked
    against a request that really happened rather than against a row a test wrote."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        # The audit write is off the hot path (`FRD-405`); the row lands within a moment.
        for _ in range(20):
            body = await _traces(client, governance_token, use_case=fixture.slug)
            if body["traces"]:
                break
        rows = body["traces"]

    assert rows, "a served request left no trace row"
    row = rows[0]
    assert row["use_case"] == fixture.slug
    assert row["outcome"] == "served"
    assert row["status"] == 200
    assert row["model"] == MODEL
    assert row["prompt_tokens"] and row["prompt_tokens"] > 0
    assert row["latency_ms"] is not None
    assert row["credential"], "the calling system is not identified on the row"


async def test_the_prompt_and_the_answer_never_leave_the_database(
    fixture, governance_token
) -> None:
    """`FRD-502` FR-11, asserted where it matters: the use case has `store_payloads` on, so the
    prompt *is* in the row this endpoint selects from. If the allow-list ever became an exclusion
    list, this is the test that fails."""
    secret = "zwiebelkuchen-4711"
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture, secret)).status_code == 200
        for _ in range(20):
            body = await _traces(client, governance_token, use_case=fixture.slug)
            if body["traces"]:
                break

    assert body["traces"], "no row to check"
    assert secret not in httpx.Response(200, json=body).text
    assert "request_payload" not in httpx.Response(200, json=body).text


async def test_a_refused_request_is_a_trace_too(fixture, governance_token) -> None:
    """The rows that matter most in an investigation are the ones `FRD-122` added: a request that
    was never served. A view that showed only successes would be a view of the wrong half."""
    await fixture.suspend(target="use_case", target_value=fixture.slug, action="block")

    async with httpx.AsyncClient(timeout=180.0) as client:
        # The suspension cache is a few seconds behind on purpose (`FRD-503` §4.1).
        for _ in range(14):
            response = await _generate(client, fixture)
            if response.status_code == 429:
                break
        assert response.status_code == 429, "the block never took effect"

        for _ in range(20):
            body = await _traces(
                client, governance_token, use_case=fixture.slug, refusals_only=True
            )
            if body["traces"]:
                break

    outcomes = {row["outcome"] for row in body["traces"]}
    assert "suspended" in outcomes, outcomes
    assert "served" not in outcomes


async def test_paging_over_postgres_repeats_no_row_and_skips_none(
    fixture, governance_token
) -> None:
    """The cursor is `(timestamptz, uuid)` here, and neither comparison exists in the hermetic
    run's dialect. Traffic keeps arriving between the two pages, which is the case offset paging
    gets wrong invisibly."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        for _ in range(6):
            assert (await _generate(client, fixture)).status_code == 200
        for _ in range(20):
            first = await _traces(client, governance_token, use_case=fixture.slug, limit=3)
            if len(first["traces"]) == 3:
                break

        assert first["next_cursor"], "there are six rows and the first page claims to be the last"
        # Two more requests arrive between the pages — exactly what breaks an offset.
        for _ in range(2):
            await _generate(client, fixture)
        second = await _traces(
            client, governance_token, use_case=fixture.slug, limit=3, cursor=first["next_cursor"]
        )

    seen = [row["id"] for row in first["traces"]] + [row["id"] for row in second["traces"]]
    assert len(seen) == len(set(seen)), "a row was shown on both pages"
    assert len(second["traces"]) == 3, "the second page lost rows the first did not cover"


async def test_a_member_sees_their_own_use_case_and_an_oversight_role_sees_it_too(
    fixture, member_token, governance_token
) -> None:
    """The scope comes from the token's roles, not from a test override — which is the only way to
    know Keycloak, the JWKS verification and `visible_scope` agree."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        for _ in range(20):
            governance = await _traces(client, governance_token, use_case=fixture.slug)
            if governance["traces"]:
                break
        member = await _traces(client, member_token, use_case=fixture.slug)

    assert governance["scope"] == "all"
    assert governance["traces"], "an oversight role cannot see a use case it is meant to oversee"
    # The member is not in this ad-hoc use case, so they get emptiness rather than a refusal:
    # "there is nothing here" and "you may not look" are different answers (`FRD-601`).
    assert member["scope"] == "use_cases"
    assert member["traces"] == []


async def test_the_endpoint_refuses_an_anonymous_reader() -> None:
    """A trace names a caller, a credential, a model and a price. It is not public."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{GATEWAY_URL}/v1beta/traces")

    assert response.status_code == 401


async def test_a_malformed_cursor_is_answered_rather_than_crashed(governance_token) -> None:
    """Every live round here has ended with the same rule: never a 500, and a message that names
    the problem."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/v1beta/traces",
            headers={"Authorization": f"Bearer {governance_token}"},
            params={"cursor": "yesterday"},
        )

    assert response.status_code == 400
    assert "cursor" in response.json()["error"]["message"]
