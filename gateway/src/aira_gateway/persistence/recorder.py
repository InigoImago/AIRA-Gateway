"""Recording a dispatched request from a route (`FRD-103`, `FRD-405`).

Resolves everything only the live request can tell — attribution, source IP, trace context — and
hands the row to the :class:`~aira_gateway.persistence.writer.RequestLogWriter`, which writes it
after the response has gone out. Span attributes are set here, because the request's span no
longer exists by the time the row is written.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from aira_common.observability import set_span_attributes, trace_context_fields
from aira_gateway.audit import Outcome, was_flagged
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.persistence.writer import PendingLog
from aira_gateway.state import settings_of, writer_of


def _tool_attributes(tool_calls: dict[str, Any] | None) -> dict[str, object]:
    """The tool figures as span attributes; empty when the request offered and called nothing.

    Absent rather than zero for a request without tools, so "which traffic uses tools" is an
    existence check. **Names, never arguments**: arguments are caller content, and a span has no
    retention clock (`FRD-131` FR-7, `FRD-406`).
    """
    if not tool_calls:
        return {}
    called = list(tool_calls.get("called") or [])
    attributes: dict[str, object] = {
        "aira.tools.offered": int(tool_calls.get("declared") or 0),
        "aira.tools.called": len(called),
    }
    if called:
        # Joined, because a span attribute is a primitive here.
        attributes["aira.tools.names"] = ", ".join(called)
    return attributes


def client_ip(request: Request) -> str | None:
    """The client IP for the audit trail (`FRD-105`, PRD FR-GW-9).

    ``X-Forwarded-For`` is honoured **only** when ``trust_forwarded_for`` is set, and then read
    ``trusted_proxy_hops`` entries **from the right**. A proxy appends, so the left end is whatever
    the caller sent: reading it would let a caller choose the address on every audit row, evade the
    incident view's filter and rotate past the failed-authentication bound. A chain shorter than
    the configured hops did not traverse them, and the socket peer is used instead.
    """
    settings = settings_of(request)
    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            chain = [part.strip() for part in forwarded.split(",") if part.strip()]
            hops = max(1, int(settings.trusted_proxy_hops))
            if len(chain) >= hops:
                return chain[-hops][:64]
    return request.client.host if request.client else None


def _degradation_snapshot(request: Request) -> dict[str, str] | None:
    """Which controls were running on a fallback while this request was handled (`FRD-122` FR-6).

    A snapshot of the gateway-wide log, so under concurrency a neighbour's degradation can appear
    on this row — accepted, since the question is asked of periods rather than single requests.
    ``None`` when there is no log, ``{}`` when nothing is degraded: "we did not look" and "nothing
    was wrong" are different answers.
    """
    degradation = getattr(request.app.state, "degradation", None)
    return None if degradation is None else dict(degradation.features)


def _request_bytes(request: Request) -> int | None:
    """How many bytes the caller sent, as counted by the body-size middleware, or ``None``.

    Never a 0 default: `FRD-501`'s `payload_size` rule leaves rows of unknown size out of both
    sides of its share, and an unknown read as zero would make a large request look small.
    """
    value = getattr(request.state, "request_bytes", None)
    return int(value) if isinstance(value, int) else None


async def record_request(
    request: Request,
    *,
    operation: str,
    model: str,
    status: int,
    usage: CanonicalUsage | None,
    latency_ms: int | None,
    request_payload: dict[str, Any] | None,
    response_payload: dict[str, Any] | None,
    cost_nanos: int | None = None,
    outcome: str = Outcome.SERVED,
    requested_model: str | None = None,
    model_selection: str | None = None,
    pipeline_decisions: list[dict[str, Any]] | None = None,
    tool_calls: dict[str, Any] | None = None,
    provenance: tuple[str, str, str] | None = None,
    api: str,
) -> None:
    """Queue a request/response record with its attribution for persistence.

    ``outcome`` defaults to ``served``; a refusal passes its own from :mod:`aira_gateway.audit`.
    ``api`` has **no default**: it travels on the :class:`AuditTrail`, set once by the surface
    that owns the request, because a discriminator with a default stops discriminating.
    """
    attribution = request.state.attribution
    source_ip = client_ip(request)
    set_span_attributes(
        {
            "aira.model": model,
            "aira.operation": operation,
            # Which surface answered (`FRD-107` §9) — set here, which refused requests pass too.
            "aira.api.surface": api,
            "aira.status": status,
            "aira.outcome": str(outcome),
            "aira.source_ip": source_ip,
            "aira.total_tokens": usage.total_tokens if usage else None,
            "aira.cost_nanos": cost_nanos,
            # Functions offered and called (`FRD-131` FR-7): "offered ten, asked for none" and
            # "offered none" are different events.
            **_tool_attributes(tool_calls),
            # Residency, per request (`FRD-115` FR-10).
            "aira.upstream.provider": provenance[0] if provenance else None,
            "aira.upstream.publisher": provenance[1] if provenance else None,
            "aira.upstream.region": provenance[2] if provenance else None,
        }
    )

    await writer_of(request).submit(
        PendingLog(
            subject=attribution.subject,
            username=attribution.username,
            auth_method=attribution.method,
            use_case=attribution.use_case,
            source_ip=source_ip,
            credential=attribution.credential,
            issuer=attribution.issuer,
            operation=operation,
            model=model,
            status=status,
            usage=usage,
            latency_ms=latency_ms,
            trace_id=trace_context_fields().get("trace_id"),
            request_payload=request_payload,
            response_payload=response_payload,
            cost_nanos=cost_nanos,
            outcome=str(outcome),
            requested_model=requested_model,
            model_selection=model_selection,
            pipeline_decisions=pipeline_decisions,
            # Derived once, here, rather than at each call site that records a request.
            flagged=was_flagged(pipeline_decisions, outcome),
            tool_calls=tool_calls,
            degraded=_degradation_snapshot(request),
            provider=provenance[0] if provenance else None,
            publisher=provenance[1] if provenance else None,
            region=provenance[2] if provenance else None,
            api=api,
            request_bytes=_request_bytes(request),
        )
    )
