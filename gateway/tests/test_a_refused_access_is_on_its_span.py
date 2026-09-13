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
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import use_case_refusal
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
    assert attributes["aira.subject"] == principal.subject
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
