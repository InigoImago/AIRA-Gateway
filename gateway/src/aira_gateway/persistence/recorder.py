"""Helper that records a dispatched request from a route (FRD-103, FRD-405).

Resolves everything that can only be read from the live request — attribution, source IP, trace
context — and hands the result to the :class:`~aira_gateway.persistence.writer.RequestLogWriter`,
which writes it after the response has gone out.

Span attributes are set *here* rather than in the writer: they belong to the request's own span,
which no longer exists by the time the row is written.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from aira_common.observability import set_span_attributes, trace_context_fields
from aira_gateway.audit import Outcome, was_flagged
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.persistence.writer import PendingLog
from aira_gateway.state import settings_of, writer_of


def client_ip(request: Request) -> str | None:
    """Return the client IP for the audit trail (FRD-105, PRD FR-GW-9).

    ``X-Forwarded-For`` is honoured **only** when ``trust_forwarded_for`` is set, and then it is
    read ``trusted_proxy_hops`` entries **from the right** — never from the left.

    The left end is whatever the caller sent. A proxy *appends*: the nginx this repository ships
    uses ``$proxy_add_x_forwarded_for``, so ``X-Forwarded-For: 10.9.9.9`` from a client arrives
    here as ``10.9.9.9, <real address>``. Reading the leftmost entry — which this did until
    2026-08-09, on a docstring that assumed a proxy which *overwrites* — let a caller choose:

    - the address written onto every audit row,
    - the address `FRD-505`'s incident view filters by, so a search for the real one finds nothing,
    - and the key the failed-authentication bound counts against, so rotating the header made the
      brute-force bound unreachable.

    A chain shorter than the configured number of hops did not traverse them, so its header is
    ignored in favour of the socket peer. That is the safe direction: an unspoofable address that
    is merely the proxy's beats a spoofable one that claims to be the client's.
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
    """Which controls were running on a fallback while this request was handled (FRD-122 FR-6).

    Read here, at the end of handling, from the gateway-wide log — the controls update it as they
    run, so this captures what they experienced during this request. It is a snapshot of a shared
    state, not a per-request guarantee: under concurrency a neighbour's degradation can appear on
    this row. That imprecision is worth stating and worth keeping, because the alternative —
    threading the status back out of every control — buys exactness for a question ("was this
    request under the full guarantee?") that is asked about periods far more often than about
    single requests.

    ``None`` when there is no degradation log at all, ``{}`` when there is one and nothing is
    degraded: "we did not look" and "nothing was wrong" are different answers.
    """
    degradation = getattr(request.app.state, "degradation", None)
    return None if degradation is None else dict(degradation.features)


def _request_bytes(request: Request) -> int | None:
    """How many bytes the caller sent, or ``None`` when nothing counted them.

    The middleware puts it on the ASGI scope's state. Never a 0 default: `FRD-501`'s
    `payload_size` rule excludes rows of unknown size from **both** sides of its share, and an
    unknown that arrived as a zero would make a large request look small.

    Wired here because it was not wired at all — the column existed, the middleware wrote the
    count, and nothing carried it between them, so the whole `payload_size` kind measured a column
    that was always NULL. Two correct halves and no wire, invisible to coverage; found by posting
    a 4 kB body at the running gateway and reading the row.
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

    ``outcome`` defaults to ``served`` so every existing call site keeps its meaning; a refusal
    passes its own value from the closed vocabulary in :mod:`aira_gateway.audit`.

    ``api`` deliberately has **no default**. It used to default to ``"gemini"``, which made a
    caller that forgot it right on one surface and silently wrong on every other — measured on
    2026-08-13, when a KIRA request's pipeline classifier row (`FRD-125b`) turned up filed under
    the Gemini surface. A discriminator with a default is a discriminator that stops discriminating
    at the first call site somebody adds in a hurry; it now travels on the :class:`AuditTrail`, set
    once by the surface that owns the request.
    """
    attribution = request.state.attribution
    source_ip = client_ip(request)
    set_span_attributes(
        {
            "aira.model": model,
            "aira.operation": operation,
            "aira.status": status,
            "aira.outcome": str(outcome),
            "aira.source_ip": source_ip,
            "aira.total_tokens": usage.total_tokens if usage else None,
            "aira.cost_nanos": cost_nanos,
            # Residency, per request. A configuration claim that nothing records is a claim
            # nobody can check afterwards (FRD-115 FR-10).
            "aira.upstream.provider": provenance[0] if provenance else None,
            "aira.upstream.publisher": provenance[1] if provenance else None,
            "aira.upstream.region": provenance[2] if provenance else None,
        }
    )

    await writer_of(request).submit(
        PendingLog(
            subject=attribution.subject,
            # A name for grouping a display, never the identity (`FRD-606`).
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
            # Derived **here**, from the argument every caller already passes, rather than at the
            # three call sites that build it. `FRD-122` learned this once: a fact repeated at every
            # exit is a fact eventually missing from one of them, and a fourth surface would have
            # to remember. The rule is one site or none.
            flagged=was_flagged(pipeline_decisions, outcome),
            tool_calls=tool_calls,
            degraded=_degradation_snapshot(request),
            provider=provenance[0] if provenance else None,
            publisher=provenance[1] if provenance else None,
            region=provenance[2] if provenance else None,
            api=api,
            # What the caller sent, as counted by the body-size middleware while it enforced the
            # ceiling. Read here rather than recomputed: the body has already been consumed by the
            # time a row is written, and re-reading it would be the second copy of a number the
            # process already has.
            request_bytes=_request_bytes(request),
        )
    )
