"""A refused access attempt is on its span, so the delivery channel carries it (`FRD-616`).

A 401 or a 403 is refused before the request is attributed, so it writes no audit row and its span
carries no `aira.use_case`. The delivery channel keeps a request span by `aira.use_case` or by
`aira.outcome`; without the second, a SIEM received every served request and no refused attempt.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import Request
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aira_common.observability import request_sampler
from aira_common.pseudonym import pseudonym
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.kira.attribution import resolve as kira_attribution
from aira_gateway.api.kira.errors import KiraError
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_attribution, use_case_refusal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings

Traced = tuple[httpx.AsyncClient, InMemorySpanExporter, TracerProvider]


@pytest.fixture
def tracing() -> tuple[InMemorySpanExporter, TracerProvider]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider


@pytest_asyncio.fixture
async def authenticating(
    tracing: tuple[InMemorySpanExporter, TracerProvider],
) -> AsyncIterator[Traced]:
    app = create_app(GatewaySettings(auth_required=True, environment="local", log_queue_size=0))
    exporter, provider = tracing
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, exporter, provider


def _span(exporter: InMemorySpanExporter) -> dict[str, Any]:
    spans = exporter.get_finished_spans()
    assert spans, "the test's own span was never closed"
    return dict(spans[0].attributes or {})


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/v1beta/models/mock-1:generateContent", {"contents": [{"parts": [{"text": "x"}]}]}),
        ("/kira/api/external/chat", {"request": {"parts": [{"text": "x"}]}, "model_id": 1}),
    ],
    ids=["gemini", "kira"],
)
async def test_an_unauthenticated_attempt_is_marked_on_both_surfaces(
    authenticating: Traced, path: str, body: dict[str, Any]
) -> None:
    """Both surfaces authenticate through one dependency, so one mark covers both — and it carries
    where the attempt came from, which is the half of a failed login a SIEM correlates on."""
    client, exporter, provider = authenticating
    with provider.get_tracer("test").start_as_current_span("POST"):
        response = await client.post(path, json=body)

    assert response.status_code == 401
    attributes = _span(exporter)
    assert attributes["aira.outcome"] == "unauthenticated"
    assert attributes["aira.status"] == 401
    assert attributes.get("aira.source_ip")
    assert "aira.use_case" not in attributes


@pytest.mark.parametrize(
    ("principal", "reason"),
    [
        (Principal(subject="alice", method="oidc", use_cases=("other",)), "Not a member"),
        (
            Principal(subject="key", method="api_key", use_cases=("other",), credential="aira_ab"),
            "bound to use case",
        ),
    ],
    ids=["oidc-not-a-member", "key-bound-elsewhere"],
)
def test_a_forbidden_attempt_says_who_asked_for_what(
    tracing: tuple[InMemorySpanExporter, TracerProvider], principal: Principal, reason: str
) -> None:
    """The one rule both surfaces refuse by, so the mark is made where the decision is. The use case
    asked for is `aira.use_case.requested`: the request was not attributed to it."""
    exporter, provider = tracing
    with provider.get_tracer("test").start_as_current_span("POST"):
        refusal = use_case_refusal(principal, "wanted")

    assert refusal is not None and reason in refusal
    attributes = _span(exporter)
    assert attributes["aira.outcome"] == "forbidden"
    assert attributes["aira.status"] == 403
    assert attributes["aira.subject"] == pseudonym(principal.subject)
    assert attributes["aira.auth_method"] == principal.method
    assert attributes["aira.use_case.requested"] == "wanted"
    assert "aira.use_case" not in attributes


def test_an_allowed_request_is_not_marked_refused(
    tracing: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    exporter, provider = tracing
    member = Principal(subject="alice", method="oidc", use_cases=("wanted",))
    with provider.get_tracer("test").start_as_current_span("POST"):
        assert use_case_refusal(member, "wanted") is None

    assert "aira.outcome" not in _span(exporter)


def test_a_malformed_use_case_is_not_written_to_the_span(
    tracing: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    """The selector is the caller's input, and only a well-formed slug goes into a trace
    (`ADR-0007`)."""
    exporter, provider = tracing
    stranger = Principal(subject="alice", method="oidc", use_cases=())
    with provider.get_tracer("test").start_as_current_span("POST"):
        use_case_refusal(stranger, "Not A Slug\n")

    attributes = _span(exporter)
    assert attributes["aira.outcome"] == "forbidden"
    assert "aira.use_case.requested" not in attributes


async def test_a_refused_attempt_is_on_its_span_whatever_the_caller_asked_for() -> None:
    """The span above is the attempt's only record, so the caller must not be able to switch it
    off. A `traceparent` whose sampled flag is unset used to do exactly that: the server span
    followed the caller's flag, nothing was exported, and a SIEM fed from the delivery channel
    never saw the attempt (`request_sampler`). The caller's trace id is still continued."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=request_sampler(1.0))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app(GatewaySettings(auth_required=True, environment="local", log_queue_size=0))
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    trace_id = "0af7651916cd43dd8448eb211c80319c"
    try:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/v1beta/models/mock-1:generateContent",
                    json={"contents": [{"parts": [{"text": "x"}]}]},
                    headers={"traceparent": f"00-{trace_id}-00f067aa0ba902b7-00"},
                )
    finally:
        FastAPIInstrumentor.uninstrument_app(app)

    assert response.status_code == 401
    refused = [
        span
        for span in exporter.get_finished_spans()
        if (span.attributes or {}).get("aira.outcome") == "unauthenticated"
    ]
    assert refused, "the caller's not-sampled flag removed the only record of a refused attempt"
    assert f"{refused[0].context.trace_id:032x}" == trace_id


# == a known caller whose request no use case could be attributed to =============================


def _request(app: Any, headers: dict[str, str] | None = None) -> Request:
    """A request as the dependency sees it: the app's settings, the caller's headers."""
    raw = [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "app": app,
            "method": "POST",
            "path": "/v1beta/models/mock-1:generateContent",
            "headers": raw,
            "query_string": b"",
        }
    )


async def test_a_request_that_names_no_use_case_is_on_its_span(
    tracing: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    """The Gemini surface's `400`: the caller is known, and the request belongs to no use case.
    It writes no audit row, and without a mark its span carried nothing the delivery channel keeps
    a request by — the attempt never reached a SIEM."""
    exporter, provider = tracing
    app = create_app(GatewaySettings(log_queue_size=0))
    caller = Principal(subject="alice", method="oidc", use_cases=("a", "b"))
    with (
        provider.get_tracer("test").start_as_current_span("POST"),
        pytest.raises(GeminiHTTPError) as refused,
    ):
        await require_attribution(_request(app), caller)

    assert refused.value.code == 400
    attributes = _span(exporter)
    assert attributes["aira.outcome"] == "unattributed"
    assert attributes["aira.status"] == 400
    assert attributes["aira.subject"] == pseudonym("alice")
    assert "aira.use_case" not in attributes


async def test_a_malformed_selector_is_on_its_span_without_the_selector(
    tracing: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    exporter, provider = tracing
    app = create_app(GatewaySettings(log_queue_size=0))
    caller = Principal(subject="alice", method="oidc", use_cases=("a",))
    with (
        provider.get_tracer("test").start_as_current_span("POST"),
        pytest.raises(GeminiHTTPError),
    ):
        await require_attribution(_request(app, {"X-AIRA-Use-Case": "Not A Slug"}), caller)

    attributes = _span(exporter)
    assert attributes["aira.outcome"] == "unattributed"
    assert attributes["aira.status"] == 400
    assert "aira.use_case.requested" not in attributes


@pytest.mark.parametrize(
    ("memberships", "selector"),
    [(("a", "b"), None), ((), None), (("a",), "Not A Slug")],
    ids=["several", "none", "malformed"],
)
async def test_the_kira_surface_marks_what_it_cannot_attribute(
    tracing: tuple[InMemorySpanExporter, TracerProvider],
    memberships: tuple[str, ...],
    selector: str | None,
) -> None:
    """KIRA attributes a caller of exactly one use case by itself; several, none, or a malformed
    name is refused — and is on its span like the Gemini surface's refusals."""
    exporter, provider = tracing
    app = create_app(GatewaySettings(log_queue_size=0))
    caller = Principal(subject="alice", method="oidc", use_cases=memberships)
    headers = {"X-AIRA-Use-Case": selector} if selector else {}
    with provider.get_tracer("test").start_as_current_span("POST"), pytest.raises(KiraError):
        kira_attribution(_request(app, headers), caller)

    attributes = _span(exporter)
    assert attributes["aira.outcome"] == "unattributed"
    assert attributes["aira.status"] in (400, 403)
    assert attributes["aira.subject"] == pseudonym("alice")


async def test_an_address_asked_to_wait_is_on_its_span(
    tracing: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    """The `429` for too many failed attempts from one address: a credential prober is the thing a
    SIEM watches for, and this refusal writes no audit row either."""
    exporter, provider = tracing
    app = create_app(
        GatewaySettings(
            auth_required=True,
            environment="local",
            log_queue_size=0,
            redis_url="",
            max_auth_failures_per_minute=1,
        )
    )
    statuses: list[int] = []
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(3):
                exporter.clear()
                with provider.get_tracer("test").start_as_current_span("POST"):
                    response = await client.post(
                        "/v1beta/models/mock-1:generateContent",
                        json={"contents": [{"parts": [{"text": "x"}]}]},
                        headers={"x-goog-api-key": "aira_x_y"},
                    )
                statuses.append(response.status_code)

    assert statuses[-1] == 429, statuses
    attributes = _span(exporter)
    assert attributes["aira.outcome"] == "unauthenticated"
    assert attributes["aira.status"] == 429
