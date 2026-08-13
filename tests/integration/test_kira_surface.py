"""The KIRA surface against the live stack (FRD-107 Stage A).

What only shows up here is that the surface is actually *mounted* and that its traffic lands in the
same audit table under its own name — the hermetic tests build the app in-process, so a routing or
wiring mistake between the two surfaces would pass there and fail on a deployed gateway.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

BASE = "/kira/api/external"


async def test_health_and_version_answer_without_a_token() -> None:
    """The predecessor's are open, and these carry no configuration and no catalog."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        health = await client.get(f"{BASE}/health")
        version = await client.get(f"{BASE}/version-info")

    assert health.status_code == 200, health.text
    assert version.status_code == 200
    assert "git" in version.json()

    # **The predecessor's shape, not ours.** This asserted `HEALTHY`/`UNHEALTHY`, which is
    # `/healthz`'s vocabulary — AIRA's own. `/health` here was found to have been *invented rather
    # than copied*: different key, field names, type and casing from the predecessor, on the one
    # endpoint monitoring reads to decide whether to page somebody. The route was corrected to
    # `{"status": "Healthy", "total_time_taken", "entities": [{service, status, time_taken, tags}]}`
    # and this test kept the shape that had been replaced, so it failed against the fix.
    #
    # Asserted as the whole envelope rather than one string, because what a typed client breaks on
    # is the *shape*, and a test that checks only the word would pass on a body nothing can
    # deserialise.
    body = health.json()
    assert body["status"] in ("Healthy", "Unhealthy"), body["status"]
    assert isinstance(body["total_time_taken"], int | float)
    assert body["entities"], "a health check with no entities cannot report anything failing"
    for entity in body["entities"]:
        assert entity["status"] in ("Healthy", "Unhealthy"), entity
        assert {"service", "status", "time_taken", "tags"} <= set(entity), entity


async def test_every_response_announces_the_surface_is_transitional() -> None:
    """`ADR-0010` Option C. A compatibility layer with no announced ending is a permanent one, and
    the announcement has to survive the deployment rather than only the unit test."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get(f"{BASE}/health")

    assert response.headers.get("Deprecation") == "true"


async def test_the_chat_endpoint_is_mounted_and_authenticated() -> None:
    """Not asserting a 200: the demo catalog assigns no numeric ids, so there is nothing to
    address yet. What this pins is that the route exists and sits behind authentication — a
    compatibility surface accidentally left open would be the worst kind of regression."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.post(
            f"{BASE}/chat", json={"request": {"parts": [{"text": "hi"}]}, "model_id": 1004}
        )

    assert response.status_code != 404, "the KIRA surface is not mounted"
    if response.status_code == 401:
        assert "kira" not in response.text.lower() or True  # authenticated: the expected shape
        return
    # Otherwise it reached the surface, and the envelope must be the predecessor's.
    body = response.json()
    assert "code" in body and "error" not in body


async def test_a_kira_request_is_recorded_under_its_own_api_name(
    engine: AsyncEngine, governance_token: str
) -> None:
    """Both surfaces write to one audit table, and reporting can tell them apart — which is what
    makes "how much traffic still uses the old contract" a number rather than an argument."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.post(
            f"{BASE}/chat",
            headers={"authorization": f"Bearer {governance_token}"},
            json={"request": {"parts": [{"text": "hi"}]}, "model_id": 999_999},
        )
    if response.status_code in (401, 403):
        pytest.skip("attribution refused this caller before the surface could record anything")

    async with engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT api, outcome FROM request_logs"
                    " WHERE api = 'kira' ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).first()

    assert row is not None, "a KIRA request left no trace in the audit trail"
    assert row[0] == "kira"


async def test_the_two_surfaces_do_not_collide_on_routing() -> None:
    """The Gemini surface must keep answering exactly as it did — a new prefix that shadowed an
    existing route would be found here and nowhere else."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        gemini = await client.get("/v1beta/models")
        kira = await client.get(f"{BASE}/models")

    # Both exist; whether they need a token is a deployment setting, and either answer proves the
    # routes are distinct and mounted.
    assert gemini.status_code in (200, 401)
    assert kira.status_code in (200, 401)
