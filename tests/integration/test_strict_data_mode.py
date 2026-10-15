"""Strict data mode on the live stack, either way it was started (`FRD-627`).

CI starts the stack once with `AIRA_STRICT_DATA_MODE=true` (the default) and once with `false`, and
runs this suite against each. Every test here states both answers — who sees a person in each mode —
and checks the one the stack is in, which it asks Management rather than assumes.
"""

from __future__ import annotations

import httpx
import pytest

from .conftest import GATEWAY_URL, wait_for_row

pytestmark = pytest.mark.integration


async def _served(client: httpx.AsyncClient, fixture) -> None:
    response = await client.post(
        f"{GATEWAY_URL}/v1beta/models/mock-1:generateContent",
        headers=fixture.headers(),
        json={"contents": [{"parts": [{"text": "who am I to IT Steuerung?"}]}]},
    )
    assert response.status_code == 200, response.text
    await wait_for_row(
        fixture.engine,
        "SELECT 1 FROM request_logs WHERE use_case = :slug",
        {"slug": fixture.slug},
        timeout=20.0,
    )


async def _get(client: httpx.AsyncClient, token: str, path: str, **params) -> dict:
    response = await client.get(
        f"{GATEWAY_URL}{path}", headers={"Authorization": f"Bearer {token}"}, params=params
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_it_steuerung_reads_a_row_without_its_person_only_in_strict_mode(
    fixture, governance_token, security_token, strict_data
) -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        await _served(client, fixture)
        governed = await _get(client, governance_token, "/v1beta/traces", use_case=fixture.slug)
        secured = await _get(client, security_token, "/v1beta/traces", use_case=fixture.slug)

    row, full = governed["traces"][0], secured["traces"][0]
    assert row["id"] == full["id"], "both roles read the same row"
    # IT Security sees whose request it was in either mode.
    assert full["subject"] == fixture.subject and full["credential"]
    if strict_data:
        assert row["person_withheld"] is True
        assert row["subject"] is None and row["credential"] is None
        assert row["cost_nanos"] == full["cost_nanos"], "what it cost is not the person's"
    else:
        assert row["person_withheld"] is False
        assert row["subject"] == fixture.subject and row["credential"] == full["credential"]


async def test_the_split_by_person_follows_the_mode(
    fixture, governance_token, security_token, strict_data
) -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        await _served(client, fixture)
        governed = await _get(client, governance_token, "/v1beta/reporting", use_case=fixture.slug)
        secured = await _get(client, security_token, "/v1beta/reporting", use_case=fixture.slug)

    assert governed["strict_data"] is strict_data
    assert fixture.subject in {member["key"] for member in secured["by_member"]}
    governed_people = {member["key"] for member in governed["by_member"]}
    if strict_data:
        # Reading every figure is no exemption; IT Steuerung called nothing here itself.
        assert governed_people == set()
        assert all("avg_latency_ms" not in member for member in secured["by_member"])
    else:
        assert fixture.subject in governed_people
    assert governed["totals"]["requests"] == secured["totals"]["requests"], "totals for everybody"


async def test_filtering_by_a_key_is_the_incident_roles_only_in_strict_mode(
    fixture, governance_token, strict_data
) -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        await _served(client, fixture)
        response = await client.get(
            f"{GATEWAY_URL}/v1beta/traces",
            headers={"Authorization": f"Bearer {governance_token}"},
            params={"subject": fixture.subject},
        )

    if strict_data:
        assert response.status_code == 403
        assert "strict data mode" in response.json()["error"]["message"]
    else:
        assert response.status_code == 200
        assert {row["use_case"] for row in response.json()["traces"]} >= {fixture.slug}
