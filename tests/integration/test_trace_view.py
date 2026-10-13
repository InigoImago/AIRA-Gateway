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

import asyncio
import time

import httpx
import pytest
from sqlalchemy import text

from aira_gateway.anomalies.suspensions import CACHE_TTL_SECONDS

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

#: The demo's chat model — served on every stack this layer runs against, and declared in the
#: catalogue as calling tools (`tools/seed_local_catalog.py`), which is what a trace of the
#: functions offered needs: the declaration is recorded whether or not the model then calls one.
MODEL = "qwen3:0.6b"
SHORT = {"generationConfig": {"maxOutputTokens": 8}}
#: How long the gateway may take to see a suspension: its cache lifetime, with margin for a loaded
#: runner (`FRD-503` §4.1).
BLOCK_SEEN_WITHIN = CACHE_TTL_SECONDS * 3
#: How long a refused request may take to reach the trace view: the audit write is off the request
#: path (`FRD-405`).
TRACE_WRITTEN_WITHIN = 20.0


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


async def _traces_until(
    client: httpx.AsyncClient,
    token: str,
    holds=lambda body: bool(body["traces"]),
    *,
    within: float = TRACE_WRITTEN_WITHIN,
    **params,
) -> dict:
    """Ask until the answer ``holds``, on a clock — and say so when it never does.

    The audit write is off the request path (`FRD-405`), and after a busy suite its queue is
    deep: twenty back-to-back calls are over in a fraction of a second, long before the row lands,
    and the test then reports a lost audit row that was merely late.
    """
    deadline = time.monotonic() + within
    body = await _traces(client, token, **params)
    while not holds(body):
        assert time.monotonic() < deadline, f"no trace row within {within}s for {params}"
        await asyncio.sleep(0.5)
        body = await _traces(client, token, **params)
    return body


async def test_a_real_request_appears_as_a_trace_with_what_it_actually_cost(
    fixture, governance_token
) -> None:
    """The columns are filled in by the recorder, so this is the only place they can be checked
    against a request that really happened rather than against a row a test wrote."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        # The audit write is off the hot path (`FRD-405`); the row lands within a moment.
        rows = (await _traces_until(client, governance_token, use_case=fixture.slug))["traces"]

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
    fixture, governance_token, engine
) -> None:
    """`FRD-502` FR-11, asserted where it matters: the use case has `store_payloads` on, so the
    prompt *is* in the row this endpoint selects from — checked in the database first, since
    without that the absence below would hold for a payload nobody stored. If the allow-list ever
    became an exclusion list, this is the test that fails."""
    secret = "zwiebelkuchen-4711"
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture, secret)).status_code == 200
        body = await _traces_until(client, governance_token, use_case=fixture.slug)

    async with engine.connect() as connection:
        stored = (
            await connection.execute(
                text("SELECT request_payload::text FROM request_logs WHERE use_case = :slug"),
                {"slug": fixture.slug},
            )
        ).scalar()
    assert stored and secret in stored, "the prompt was not stored, so its absence proves nothing"

    assert body["traces"], "no row to check"
    assert secret not in httpx.Response(200, json=body).text
    assert "request_payload" not in httpx.Response(200, json=body).text


async def test_a_refused_request_is_a_trace_too(fixture, governance_token) -> None:
    """The rows that matter most in an investigation are the ones `FRD-122` added: a request that
    was never served. A view that showed only successes would be a view of the wrong half."""
    await fixture.suspend(target="use_case", target_value=fixture.slug, action="block")

    async with httpx.AsyncClient(timeout=180.0) as client:
        # Waited for by the clock, not by a count of calls: the gateway sees a suspension only once
        # its cache expires, and a fast model answers many calls before that.
        deadline = time.monotonic() + BLOCK_SEEN_WITHIN
        response = await _generate(client, fixture)
        while response.status_code != 429 and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            response = await _generate(client, fixture)
        assert response.status_code == 429, "the block never took effect"

        deadline = time.monotonic() + TRACE_WRITTEN_WITHIN
        body = await _traces(client, governance_token, use_case=fixture.slug, refusals_only=True)
        while not body["traces"] and time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            body = await _traces(
                client, governance_token, use_case=fixture.slug, refusals_only=True
            )

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

        # **Wait for all six, not for a full first page.** The audit write is off the request path
        # (`FRD-405`), so a 200 and its row are two events — and this waited only until *three*
        # rows existed, then paged as though six did. Under a full suite the queue is deeper: page
        # one took the newest three of five, page two correctly returned the remaining two, and the
        # test reported "the second page lost rows" — a paging defect that was not one. It passed
        # in isolation every time, which is the shape that costs an afternoon.
        for _ in range(40):
            listed = await _traces(client, governance_token, use_case=fixture.slug, limit=10)
            if len(listed["traces"]) >= 6:
                break
            await asyncio.sleep(0.25)
        assert len(listed["traces"]) >= 6, (
            f"only {len(listed['traces'])} of six rows were written; the writer never drained"
        )

        first = await _traces(client, governance_token, use_case=fixture.slug, limit=3)
        assert len(first["traces"]) == 3
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


async def test_an_oversight_role_sees_a_use_case_and_a_non_member_is_answered_empty(
    fixture, member_token, governance_token
) -> None:
    """The scope comes from the token's roles, not from a test override — which is the only way to
    know Keycloak, the JWKS verification and `visible_scope` agree."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        governance = await _traces_until(client, governance_token, use_case=fixture.slug)
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


# ═══ what an incident may ask, against real roles (FRD-502 FR-10a–c, FRD-131 FR-7) ═════════════
#
# The hermetic suite overrides the principal, so every role it tests is one a test constructed.
# Here the roles come from Keycloak, which is the only place the mapping from a realm role to
# `is_oversight` / `may_act_on_incidents` is actually exercised — and it is the mapping that was
# wrong on 2026-08-08, when `visible_scope` asked the narrower predicate and IT Security's own
# console came back empty.


async def test_it_security_sees_every_use_case_and_not_an_empty_screen(
    fixture, security_token
) -> None:
    """The defect this test exists for: IT Security is *oversight* but not *governance*, and the
    scope function asked for the wrong one — so the role whose job is investigating saw nothing."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        body = await _traces_until(client, security_token, use_case=fixture.slug)

    assert body["scope"] == "all", "IT Security must not be scoped to its own memberships"
    assert body["traces"], "IT Security saw no trace of a request that certainly happened"


async def test_the_calling_machine_is_shown_to_an_incident_role_and_withheld_otherwise(
    fixture, security_token, governance_token
) -> None:
    """`source_ip` identifies a machine rather than a use case. `it-steuerung` sees every figure
    and may not act on an incident — **visibility and authority are different answers**."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        security = await _traces_until(client, security_token, use_case=fixture.slug)
        oversight = await _traces(client, governance_token, use_case=fixture.slug)

    assert "source_ip" in security["traces"][0]
    assert oversight["traces"], "the oversight role should still see the request itself"
    assert "source_ip" not in oversight["traces"][0], (
        "a role that may not act on an incident was handed the calling machine's address"
    )


async def test_filtering_by_address_is_refused_rather_than_ignored(governance_token) -> None:
    """A filter that silently does nothing lets somebody conclude an address made no requests —
    the opposite of what the screen just told them. Refusal is the honest answer."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/v1beta/traces",
            headers={"Authorization": f"Bearer {governance_token}"},
            params={"source_ip": "10.0.0.7"},
        )

    assert response.status_code == 403
    assert "source address" in response.json()["error"]["message"].lower()


async def test_a_filter_narrows_and_never_widens(fixture, member_token, governance_token) -> None:
    """Every filter is applied after `visible_scope`. No combination of them may reach a use case
    the caller could not already see — which is the property an added parameter is most likely to
    break, because each one is written next to the last.

    Asked about a use case that **has** a row the member may not see, with filters that **match**
    that row: asked about one with no rows at all, every filter returned nothing whether or not it
    respected the scope. And asked once naming the use case and once naming none.
    """
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        # The row exists, and what would match it is read off it by somebody allowed to see it.
        theirs = (await _traces_until(client, governance_token, use_case=fixture.slug))["traces"][0]
        for params in (
            {"mine": "true"},
            {"tools_only": "false"},
            {"credential": theirs["credential"]},
            {"subject": theirs["subject"]},
        ):
            named = await _traces(client, member_token, use_case=fixture.slug, **params)
            assert named["traces"] == [], f"{params} reached outside the caller's scope"
            assert named["in_scope"] is False, params
            unnamed = await _traces(client, member_token, **params)
            assert not [row for row in unnamed["traces"] if row["use_case"] == fixture.slug], (
                f"{params} with no use case named reached one the caller cannot see"
            )


async def test_only_my_own_requests_is_offered_to_a_role_that_sees_everything(
    fixture, governance_token
) -> None:
    """A reader checking what *they* did should not have to read past everybody else — and the
    filter must be a real question to the server, not a label."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        everybody = await _traces_until(client, governance_token, use_case=fixture.slug)
        mine = await _traces(client, governance_token, use_case=fixture.slug, mine="true")

    # The traffic was made by the *fixture's* API key, not by the governance reader, so "mine"
    # must exclude it. Asserting the filter changes the answer is the point; asserting a count
    # would be asserting how much other traffic the stack happens to hold.
    assert everybody["traces"], "no traffic to filter"
    assert all(row["subject"] != everybody["traces"][0]["subject"] for row in mine["traces"]), (
        "'only my own requests' returned somebody else's"
    )


async def test_a_trace_carries_the_functions_the_model_was_offered(
    fixture, governance_token
) -> None:
    """`FRD-131` FR-7: names and a count, never the arguments. Proved on a request that really
    declared a tool, because the column is filled in by the recorder — on the demo model, which
    every stack serves, so this runs rather than skipping where the larger tool model is absent."""
    await fixture.enable_tools()
    async with httpx.AsyncClient(timeout=180.0) as client:
        response = await client.post(
            f"{GATEWAY_URL}/v1beta/models/{MODEL}:generateContent",
            headers=fixture.headers(),
            json={
                "contents": [{"parts": [{"text": "What is the weather in Berlin?"}]}],
                "tools": [
                    {
                        "functionDeclarations": [
                            {
                                "name": "get_weather",
                                "description": "Current weather for a city",
                                "parameters": {
                                    "type": "object",
                                    "properties": {"city": {"type": "string"}},
                                },
                            }
                        ]
                    }
                ],
                **SHORT,
            },
            timeout=180.0,
        )
        assert response.status_code == 200, response.text

        body = await _traces_until(
            client,
            governance_token,
            lambda body: any(row.get("tool_calls") for row in body["traces"]),
            use_case=fixture.slug,
            tools_only="true",
        )
        rows = [row for row in body["traces"] if row.get("tool_calls")]

    assert rows, "a request that declared a function left no record of having declared one"
    recorded = rows[0]["tool_calls"]
    assert recorded["declared"] == 1
    # Names only. The arguments are the caller's content and belong to `FRD-406`, not to a list
    # anybody with an oversight role can read.
    assert all(isinstance(name, str) for name in recorded.get("called", []))
    assert "arguments" not in recorded


# ═══ reading what was actually sent (FRD-505) ══════════════════════════════════════════════════
#
# `ADR-0009` refused this view; the owner granted it on 2026-08-09 for the two incident roles, on
# the condition that every read is recorded. These tests check the condition, not only the feature —
# a permission whose audit trail did not work would be the ADR's objection with a feature on top.


async def _payload(client: httpx.AsyncClient, token: str, row_id: str) -> httpx.Response:
    return await client.get(
        f"{GATEWAY_URL}/v1beta/traces/{row_id}/payload",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )


async def _first_trace_id(client: httpx.AsyncClient, token: str, slug: str) -> str:
    return str((await _traces_until(client, token, use_case=slug))["traces"][0]["id"])


async def test_an_incident_role_reads_the_prompt_and_the_read_is_recorded(
    fixture, security_token, engine
) -> None:
    """The record is the condition the permission rests on, so it is asserted in the database
    rather than inferred from a 200."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture, "Say OK")).status_code == 200
        row_id = await _first_trace_id(client, security_token, fixture.slug)
        response = await _payload(client, security_token, row_id)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["ground"] == "incident"
    assert "Say OK" in str(body["request"]), "the reader was allowed and handed nothing"

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT subject, ground FROM payload_access WHERE request_log_id = :id"),
                {"id": row_id},
            )
        ).all()
    assert len(rows) == 1, "reading a stored prompt left no record"
    assert rows[0][1] == "incident"


async def test_an_oversight_role_that_may_not_act_is_refused_the_content(
    fixture, security_token, governance_token
) -> None:
    """`it-steuerung` sees every figure about every use case and no content. Visibility and content
    are different answers — the split this whole feature turns on."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        row_id = await _first_trace_id(client, security_token, fixture.slug)
        # It can see the row itself…
        listed = await _traces(client, governance_token, use_case=fixture.slug)
        # …and not what was in it.
        response = await _payload(client, governance_token, row_id)

    assert listed["traces"], "the oversight role lost sight of the request as well"
    assert response.status_code == 403
    assert "payload.read_any" in response.json()["error"]["message"]


async def test_a_refused_read_leaves_no_access_record(
    fixture, security_token, governance_token, engine
) -> None:
    """An access log filling up with attempts nobody was granted would make the real reads harder
    to find, and the attempt is a 403 in the request log already."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        row_id = await _first_trace_id(client, security_token, fixture.slug)
        assert (await _payload(client, governance_token, row_id)).status_code == 403

    async with engine.connect() as connection:
        count = (
            await connection.execute(
                text("SELECT count(*) FROM payload_access WHERE request_log_id = :id"),
                {"id": row_id},
            )
        ).scalar_one()
    assert count == 0


async def test_a_use_case_that_stores_nothing_says_so_rather_than_showing_an_empty_panel(
    fixture, security_token
) -> None:
    """Three ways to have nothing, and this is the one somebody can change. A single "not
    available" would leave the reader unable to tell a setting from a clock."""
    await fixture.set_store_payloads(False)
    async with httpx.AsyncClient(timeout=180.0) as client:
        assert (await _generate(client, fixture)).status_code == 200
        row_id = await _first_trace_id(client, security_token, fixture.slug)
        response = await _payload(client, security_token, row_id)

    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["reason"] == "not_stored"
    assert "storage" in body["message"].lower()
