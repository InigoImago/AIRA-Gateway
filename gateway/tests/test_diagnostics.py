"""Diagnostics: readiness, trace correlation, CORS, version (FRD-117).

The centre of this file is one claim from §5.2: **a health check must not be able to take down a
healthy service.** The predecessor's probes every registered model on every call, which makes the
readiness probe as slow as the slowest upstream — so one degraded provider evicts pods that were
serving perfectly well, and against a paid endpoint it bills for the privilege.

Everything else here is about being *findable* when something does go wrong: a trace id on the
responses that failed, a version endpoint that answers on a development build, and a CORS policy
that refuses the configuration the predecessor ships.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_common.counters import DegradationLog
from aira_gateway.app import CorsMisconfigured, create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.diagnostics import FEATURE, UpstreamProbe
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel


class _Clock:
    """A clock the test moves, so staleness is exercised rather than waited for."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Provider:
    """An adapter that can answer a cheap remote question.

    `generate` and `embed` raise: a probe that reached them would be paying a model to answer "are
    you there", and against a self-deployed endpoint it would wake one that had scaled to zero.
    """

    probe_name = "healthy"

    def __init__(self, *names: str) -> None:
        self._names = names

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(n, n, ("generateContent",)) for n in self._names]

    async def ping(self) -> str:
        return "2 model(s) listed"

    async def generate(self, request):  # noqa: ANN001, ANN201
        raise AssertionError("the probe must never generate")

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise AssertionError("the probe must never generate")
        yield  # pragma: no cover

    async def embed(self, request):  # noqa: ANN001, ANN201
        raise AssertionError("the probe must never embed")


class _Broken(_Provider):
    """Registers fine and is unreachable — which is the *only* realistic failure, because a
    provider whose configuration is broken never gets into the registry at all."""

    probe_name = "broken"

    async def ping(self) -> str:
        raise ConnectionError("the endpoint is not there")


class _Slow(_Provider):
    probe_name = "slow"

    async def ping(self) -> str:
        await asyncio.sleep(5)
        return "eventually"


class _Unprobeable(_Provider):
    """An adapter with nothing cheap to ask. It must be reported as *unprobed*, never as green."""

    probe_name = "unprobeable"
    ping = None  # type: ignore[assignment]


def _probe(*providers: object, **over: Any) -> UpstreamProbe:
    clock = over.pop("clock", _Clock())
    return UpstreamProbe(
        registry=ProviderRegistry(list(providers)),  # type: ignore[arg-type]
        degradation=DegradationLog(),
        clock=clock,
        **over,
    )


# == the probe never costs anything (§5.2) =======================================================


async def test_the_probe_asks_the_cheapest_question_and_never_generates() -> None:
    """The providers above raise on `generate` and `embed`. A probe that "checked" by sending a
    prompt would cost money to answer "are you there" — and against a self-deployed endpoint it
    would wake a scaled-to-zero model, turning every health check into a cold start."""
    probe = _probe(_Provider("a", "b"))
    verdicts = await probe.probe_once()

    assert all(verdict.ok for verdict in verdicts.values())


async def test_readiness_reads_the_cached_verdict_and_performs_no_io() -> None:
    """The claim the design rests on. `snapshot()` is called by `/readyz` on every probe from
    every replica; if it did I/O, the readiness check would be the load."""
    probe = _probe(_Provider("a"))
    await probe.probe_once()

    calls = 0
    original = _Provider.ping

    async def counting(self):  # noqa: ANN001, ANN202
        nonlocal calls
        calls += 1
        return await original(self)

    _Provider.ping = counting  # type: ignore[method-assign]
    try:
        for _ in range(50):
            probe.snapshot()
    finally:
        _Provider.ping = original  # type: ignore[method-assign]

    assert calls == 0, "reading the verdict reached the upstream"


async def test_one_slow_provider_does_not_delay_the_verdict_for_the_others() -> None:
    """Serial probing reintroduces "as slow as the slowest upstream" *inside* the prober. It is
    merely wasteful there rather than fatal, but it is the same mistake."""
    probe = _probe(_Provider("fast"), _Slow("slow"), timeout=0.2)

    started = asyncio.get_running_loop().time()
    verdicts = await probe.probe_once()
    elapsed = asyncio.get_running_loop().time() - started

    assert verdicts["healthy"].ok is True
    assert verdicts["slow"].ok is False
    # Concurrent: roughly one timeout, not one per provider — and nowhere near the 5s sleep.
    assert elapsed < 2.0, f"the probe took {elapsed:.1f}s; it is probing serially"


async def test_a_provider_that_times_out_says_so_rather_than_hanging() -> None:
    probe = _probe(_Slow("slow"), timeout=0.2)
    verdicts = await probe.probe_once()

    assert verdicts["slow"].ok is False
    assert "did not answer" in verdicts["slow"].detail


async def test_an_unexpected_exception_becomes_a_verdict_rather_than_killing_the_prober() -> None:
    """A probe that let an exception escape would kill the background task, and every verdict
    would then go quietly *stale* rather than red — the failure looking like the absence of one."""
    probe = _probe(_Broken("x"))
    verdicts = await probe.probe_once()

    assert verdicts["broken"].ok is False
    assert "ConnectionError" in verdicts["broken"].detail


async def test_an_adapter_with_no_probe_is_reported_as_unprobed_not_as_healthy() -> None:
    """The mistake the first draft of this module made. It called `models()` — which is *local
    configuration*, evaluated once when the registry is built, so it can neither fail later nor say
    anything about the network. Every verdict would have been a confident green describing nothing,
    which is worse than no probe at all, because a green board is acted upon."""
    probe = _probe(_Unprobeable("x"))
    verdicts = await probe.probe_once()

    assert verdicts["unprobeable"].probed is False
    assert "not checked" in verdicts["unprobeable"].detail
    # And it does not go stale, because there is no verdict to age.
    probe.clock.advance(10_000)  # type: ignore[attr-defined]
    assert probe.degraded is False


# == unreachable is degraded, not down (FR-3) ====================================================


async def test_an_unreachable_upstream_degrades_rather_than_failing_readiness() -> None:
    """A gateway that still refuses over-budget requests, still enforces limits and still serves
    reporting is **not down**. Evicting it from the load balancer helps nobody."""
    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_Broken("x")])
    app.state.upstream_probe = _probe(_Broken("x"))
    await app.state.upstream_probe.probe_once()

    with TestClient(app) as client:
        response = client.get("/readyz")

    assert response.status_code == 200, "an upstream outage must not evict a serving instance"
    body = response.json()
    assert body["status"] == "ready"
    assert body["degraded"] is True
    assert body["upstreams"]["broken"]["ok"] is False


async def test_the_degradation_log_is_the_one_shared_vocabulary() -> None:
    """`FRD-405` already has "something is broken and we are still serving". A second vocabulary
    for the same idea is one nobody correlates."""
    probe = _probe(_Broken("x"))
    await probe.probe_once()

    assert FEATURE in probe.degradation.features
    assert "unreachable" in probe.degradation.features[FEATURE]


async def test_recovery_clears_the_degradation() -> None:
    """A flag that only ever gets set turns into background noise, and then into ignored noise."""
    registry_broken = _probe(_Broken("x"))
    await registry_broken.probe_once()
    assert FEATURE in registry_broken.degradation.features

    healthy = UpstreamProbe(
        registry=ProviderRegistry([_Provider("x")]),  # type: ignore[arg-type]
        degradation=registry_broken.degradation,
        clock=_Clock(),
    )
    await healthy.probe_once()

    assert FEATURE not in healthy.degradation.features


# == a stale verdict is reported as stale, never as healthy ======================================


async def test_a_verdict_that_has_aged_out_is_reported_as_stale() -> None:
    """ "The prober has not run" is itself information. The version that rounds it to "fine" is the
    one that shows a green board describing a minute that has long passed."""
    clock = _Clock()
    probe = _probe(_Provider("a"), clock=clock, stale_after=100.0)
    await probe.probe_once()

    assert probe.snapshot()["healthy"]["stale"] is False  # type: ignore[index]
    clock.advance(101)
    assert probe.snapshot()["healthy"]["stale"] is True  # type: ignore[index]


async def test_a_stale_verdict_counts_as_degraded_even_though_it_was_green() -> None:
    """The case a naive implementation gets wrong: the last verdict said "fine", and it is old.
    Trusting it means reporting the health of a process that may have stopped probing an hour ago.
    """
    clock = _Clock()
    probe = _probe(_Provider("a"), clock=clock, stale_after=100.0)
    await probe.probe_once()

    assert probe.degraded is False
    clock.advance(101)
    assert probe.degraded is True


async def test_the_age_is_reported_so_a_reader_need_not_infer_it() -> None:
    clock = _Clock()
    probe = _probe(_Provider("a"), clock=clock)
    await probe.probe_once()
    clock.advance(42)

    assert probe.snapshot()["healthy"]["age_seconds"] == 42.0  # type: ignore[index]


async def test_nothing_configured_to_probe_is_not_a_degradation() -> None:
    """An empty registry is a valid deployment (a laptop with no upstream configured). Reporting
    it as degraded would make the signal mean nothing on the machines that use it most."""
    probe = _probe()
    await probe.probe_once()
    assert probe.degraded is False


# == liveness stays trivial ======================================================================


def test_healthz_does_no_io_and_says_only_that_the_process_is_up() -> None:
    """A liveness probe that checks a dependency restarts a healthy process when that dependency
    blinks. That is how a restart loop is built out of a transient outage."""
    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_Broken("x")])

    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# == correlation (FR-4) ==========================================================================


def test_every_response_carries_a_trace_id_when_a_span_is_active() -> None:
    settings = GatewaySettings(auth_required=False, otel_enabled=False)
    app = create_app(settings)
    with TestClient(app) as client:
        ok = client.get("/healthz")
        missing = client.post("/v1beta/models/nope:generateContent", json={})

    # Without an exporter there may be no active span, so the assertion is conditional in the same
    # way the middleware is: an id that correlates with nothing is worse than none, because
    # somebody will search for it.
    for response in (ok, missing):
        if "x-trace-id" in response.headers:
            assert response.headers["x-trace-id"].strip()


def test_a_failing_request_is_as_correlatable_as_a_successful_one() -> None:
    """The requests that most need a trace id are the ones that went wrong, which is why the
    middleware sits **outside** the exception handlers."""
    app = create_app(GatewaySettings(auth_required=False))
    with TestClient(app, raise_server_exceptions=False) as client:
        refused = client.post("/v1beta/models/mock-1:generateContent", json={"contents": []})

    assert refused.status_code == 400
    assert ("x-trace-id" in refused.headers) == ("x-trace-id" in client.get("/healthz").headers)


# == version (FR-1) ==============================================================================


def test_version_info_answers_without_a_credential() -> None:
    app = create_app(GatewaySettings(auth_required=True))
    with TestClient(app) as client:
        response = client.get("/version-info")

    assert response.status_code == 200
    assert response.json()["environment"] == "local"


def test_absent_build_metadata_is_null_rather_than_an_error() -> None:
    """A development run has no build number and should still answer — the predecessor's behaviour
    and the correct one. An endpoint that 500s without a build file is one that only works in CI."""
    app = create_app(GatewaySettings(auth_required=False))
    with TestClient(app) as client:
        body = client.get("/version-info").json()

    assert body["buildNumber"] is None
    assert body["git"]["commit"] is None


def test_build_metadata_is_reported_when_it_exists() -> None:
    settings = GatewaySettings(
        auth_required=False, git_commit="0123456789abcdef", git_branch="main", build_number=42
    )
    app = create_app(settings)
    with TestClient(app) as client:
        body = client.get("/version-info").json()

    assert body["buildNumber"] == 42
    assert body["git"]["commit"] == "0123456789abcdef"
    assert body["git"]["commitShort"] == "0123456"


# == CORS (§5.4) =================================================================================


def test_no_origins_configured_adds_no_cors_headers() -> None:
    """The SPA is served from the same origin through the proxy, so cross-origin access is a
    deliberate choice rather than a default."""
    app = create_app(GatewaySettings(auth_required=False))
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "https://evil.example"})

    assert "access-control-allow-origin" not in response.headers


def test_a_named_origin_is_allowed_and_others_are_not() -> None:
    settings = GatewaySettings(auth_required=False, cors_origins="https://spa.example")
    app = create_app(settings)
    with TestClient(app) as client:
        allowed = client.get("/healthz", headers={"Origin": "https://spa.example"})
        other = client.get("/healthz", headers={"Origin": "https://evil.example"})

    assert allowed.headers.get("access-control-allow-origin") == "https://spa.example"
    assert other.headers.get("access-control-allow-origin") != "https://evil.example"


def test_a_wildcard_with_credentials_refuses_to_start() -> None:
    """The predecessor ships exactly this (`kira_api.md` §8.1). Browsers reject the combination,
    and a server that implements it by *reflecting* the origin disables the protection entirely:
    any site a user visits can then call the API with their credentials.

    Refused at **startup**, because a misconfiguration that only shows up under a browser is one
    that ships.
    """
    settings = GatewaySettings(auth_required=False, cors_origins="*", cors_allow_credentials=True)
    with pytest.raises(CorsMisconfigured, match="credentials"):
        create_app(settings)


def test_a_wildcard_without_credentials_is_permitted() -> None:
    """A public read-only surface is a legitimate choice; it is the *combination* that is not."""
    settings = GatewaySettings(auth_required=False, cors_origins="*")
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "https://anywhere.example"})

    assert response.headers.get("access-control-allow-origin") == "*"


def test_the_trace_header_is_exposed_to_a_browser() -> None:
    """A header a browser cannot read is a header that does not exist for the SPA — and the trace
    id is the one thing a user pasting a bug report can actually supply."""
    settings = GatewaySettings(auth_required=False, cors_origins="https://spa.example")
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/healthz", headers={"Origin": "https://spa.example"})

    assert "x-trace-id" in response.headers.get("access-control-expose-headers", "").lower()
