"""Diagnostics against the running stack (FRD-117).

The hermetic suite proves the prober's logic with fake adapters. What only shows up here is
whether it asks a **real** endpoint a real question — and whether the answer survives the trip
through a deployed gateway.

The case that matters most is the one the hermetic layer cannot stage honestly: an upstream that
was reachable and then **stops being**. Here it is a container that gets stopped and started
again, which is what an outage actually looks like.
"""

from __future__ import annotations

import asyncio
import subprocess

import httpx
import pytest
import stack_addresses

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration


async def _readyz() -> dict:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get("/readyz")
    assert response.status_code in (200, 503), response.text
    return response.json()


def _container(action: str, name: str) -> None:
    subprocess.run(["docker", action, name], capture_output=True, text=True, timeout=60)


async def _wait_for(predicate, timeout: float = 90.0) -> dict:
    """Poll `/readyz` until the predicate holds, or give the last body back for the assertion."""
    deadline = asyncio.get_running_loop().time() + timeout
    body: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        body = await _readyz()
        if predicate(body):
            return body
        await asyncio.sleep(2)
    return body


# == the probe asks something real ===============================================================


async def test_readiness_reports_a_verdict_per_configured_provider() -> None:
    body = await _readyz()
    assert body["upstreams"], "no provider was probed at all"


async def test_a_probed_provider_says_what_it_found_and_how_long_it_took() -> None:
    """The difference between a probe and a placeholder. `probed: true` with a duration means a
    round trip happened; the hermetic tests cannot tell the two apart."""
    body = await _readyz()
    probed = {name: v for name, v in body["upstreams"].items() if v["probed"]}
    if not probed:
        pytest.skip("no adapter with a cheap remote probe is configured")

    for name, verdict in probed.items():
        assert verdict["ok"] is True, f"{name}: {verdict['detail']}"
        assert "ms" in verdict["detail"], f"{name} reported no duration: {verdict['detail']}"


async def test_an_adapter_without_a_probe_says_it_was_not_checked() -> None:
    """The honest half. The mock provider has nothing cheap to ask, and reporting it green on that
    basis would be a board that describes nothing — worse than no board, because it is acted on."""
    body = await _readyz()
    unprobed = [v for v in body["upstreams"].values() if not v["probed"]]
    if not unprobed:
        pytest.skip("every configured adapter can be probed")

    for verdict in unprobed:
        assert "not checked" in verdict["detail"]


async def test_readiness_answers_quickly_even_though_it_reports_upstreams() -> None:
    """§5.2's whole argument. An inline probe would make this as slow as the slowest upstream, so
    a degraded provider would cause readiness timeouts and evict healthy pods."""
    started = asyncio.get_running_loop().time()
    for _ in range(10):
        await _readyz()
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 5.0, f"ten readiness probes took {elapsed:.1f}s — is it probing inline?"


async def test_the_probe_does_not_load_a_model() -> None:
    """A probe that generated would wake a scaled-to-zero endpoint on every health check. Asked of
    the model server directly, because only it knows what is resident."""
    async with httpx.AsyncClient(base_url=stack_addresses.url("ollama"), timeout=10.0) as client:
        try:
            before = await client.get("/api/ps")
        except httpx.HTTPError:
            pytest.skip("no local model server to ask")

    for _ in range(5):
        await _readyz()

    async with httpx.AsyncClient(base_url=stack_addresses.url("ollama"), timeout=10.0) as client:
        after = await client.get("/api/ps")

    if before.status_code == 200 and after.status_code == 200:
        loaded_before = {m["name"] for m in before.json().get("models", [])}
        loaded_after = {m["name"] for m in after.json().get("models", [])}
        assert loaded_after <= loaded_before, "readiness probing loaded a model"


# == an upstream that stops answering ============================================================


async def test_a_stopped_upstream_becomes_degraded_but_the_gateway_stays_ready() -> None:
    """The case the hermetic layer cannot stage honestly, and `FRD-117` FR-3's whole point: a
    gateway that still refuses over-budget requests, still enforces limits and still serves
    reporting is **not down**. Taking it out of the load balancer would help nobody.

    The container is restarted afterwards whatever happens, because leaving the stack broken for
    the next test is worse than skipping this one.
    """
    before = await _readyz()
    probed = [name for name, v in before["upstreams"].items() if v["probed"]]
    if not probed:
        pytest.skip("no adapter with a cheap remote probe is configured")

    _container("stop", "aira-ollama")
    try:
        degraded = await _wait_for(
            lambda body: any(not v["ok"] for name, v in body["upstreams"].items() if name in probed)
        )
        assert degraded["status"] == "ready", "an upstream outage evicted a serving instance"
        assert degraded["degraded"] is True
        # HTTP 200, so a load balancer keeps the instance. The signal is for an alert, not an
        # eviction — that distinction is the entire feature.
        assert any(not v["ok"] for name, v in degraded["upstreams"].items() if name in probed)
    finally:
        _container("start", "aira-ollama")

    recovered = await _wait_for(
        lambda body: all(v["ok"] for name, v in body["upstreams"].items() if name in probed)
    )
    assert recovered["degraded"] is False, (
        "the degradation did not clear — a flag that only ever gets set becomes ignored noise"
    )


# == correlation and version =====================================================================


async def test_every_response_carries_a_trace_id_including_the_failures() -> None:
    """FR-4. The requests that most need correlating are the ones that went wrong, which is why
    the middleware sits outside the exception handlers — a placement only a deployed stack with
    tracing enabled can confirm."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        ok = await client.get("/healthz")
        refused = await client.post("/v1beta/models/mock-1:generateContent", json={})

    if "x-trace-id" not in ok.headers:
        pytest.skip("tracing is disabled on this deployment (AIRA_OTEL_ENABLED)")

    assert refused.status_code == 401
    assert refused.headers.get("x-trace-id"), "the failing response carries no trace id"
    assert refused.headers["x-trace-id"] != ok.headers["x-trace-id"], "the id is not per request"


async def test_version_info_answers_without_a_credential() -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get("/version-info")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["environment"]
    # Nulls are a valid answer: a development build has no build number and must still respond.
    assert "buildNumber" in body and "git" in body


async def test_liveness_needs_nothing_and_says_nothing_more() -> None:
    """A liveness probe that checks a dependency restarts a healthy process when that dependency
    blinks — which is how a restart loop gets built out of a transient outage."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10.0) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ═══ can this model be reached at all? (FRD-506) ════════════════════════════════════════════════
#
# The hermetic suite fakes the provider. Only here is the registry the real one, built from the
# credentials this installation actually has — which is the whole question: a model is *declared*
# without a key and *served* only with one.


async def test_a_model_this_installation_serves_is_reachable(security_token) -> None:
    """Against the real registry and the real local model. Never a generation — a check that woke
    a scaled-to-zero endpoint would bill for the question it was asked."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/v1beta/models/qwen3:0.6b:check",
            headers={"Authorization": f"Bearer {security_token}"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["served"] is True
    assert body["reachable"] is True, body


async def test_a_model_nobody_serves_says_so_rather_than_looking_healthy(security_token) -> None:
    """The case a missing credential produces: every request would come back `model_not_found`,
    which reads to a caller as a typo rather than as an installation that was never given a key.

    **Asked about a name nothing can claim, and that is the point of the name.** This test used to
    ask about `gemini-2.5-pro` on the assumption that the stack had no Vertex key — an assumption
    about the *developer's machine*, which stopped being true the day one was configured, and the
    test then asserted the opposite of what happens. A catalogued model becomes servable through
    its provider without appearing in `AIRA_VERTEX_MODELS` at all (`provider_for`), so no real
    model name is safe to assume unserved.

    The branch under test is `upstream is None`, and it is reached identically by a missing
    credential and by a name no adapter claims. Only the second can be forced on an installation
    that happens to have every credential — which is the one this runs against."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/v1beta/models/aira-no-adapter-claims-this:check",
            headers={"Authorization": f"Bearer {security_token}"},
        )

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["served"] is False
    assert body["reachable"] is None, "nothing was contacted, so this is not a failure"
    assert "credential" in body["detail"]


async def test_checking_a_model_needs_a_role_that_may_act(governance_token) -> None:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GATEWAY_URL}/v1beta/models/qwen3:0.6b:check",
            headers={"Authorization": f"Bearer {governance_token}"},
        )

    assert response.status_code == 403
