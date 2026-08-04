"""Helper that records a dispatched request from a route (FRD-103).

Pulls attribution/settings/redactor/session from the request + app state, applies the
redaction hook, and writes one ``request_logs`` row.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from aira_common.observability import trace_context_fields
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.persistence.redaction import Redactor
from aira_gateway.persistence.service import RequestLogService


def client_ip(request: Request) -> str | None:
    """Return the client IP: first ``X-Forwarded-For`` hop if present, else the socket peer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


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
) -> None:
    """Persist a request/response record with its attribution."""
    attribution = request.state.attribution
    settings = request.app.state.settings
    redactor: Redactor = request.app.state.redactor

    def _maybe(payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not settings.store_payloads or payload is None:
            return None
        return redactor.redact(payload)

    sessionmaker = request.app.state.db_sessionmaker
    async with sessionmaker() as session:
        await RequestLogService(session).record(
            subject=attribution.subject,
            auth_method=attribution.method,
            use_case=attribution.use_case,
            source_ip=client_ip(request),
            operation=operation,
            model=model,
            status=status,
            usage=usage,
            latency_ms=latency_ms,
            trace_id=trace_context_fields().get("trace_id"),
            request_payload=_maybe(request_payload),
            response_payload=_maybe(response_payload),
        )
