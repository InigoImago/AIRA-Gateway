"""Persistence of request/response logs (FRD-103)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.models import RequestLog


class RequestLogService:
    """Writes ``request_logs`` rows, bound to an async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        subject: str,
        auth_method: str,
        use_case: str | None,
        source_ip: str | None,
        operation: str,
        model: str,
        status: int,
        usage: CanonicalUsage | None,
        latency_ms: int | None,
        trace_id: str | None,
        request_payload: dict[str, Any] | None,
        response_payload: dict[str, Any] | None,
        cost_nanos: int | None = None,
        api: str = "gemini",
        credential: str | None = None,
        outcome: str | None = None,
        requested_model: str | None = None,
        model_selection: str | None = None,
        pipeline_decisions: list[dict[str, Any]] | None = None,
        degraded: dict[str, str] | None = None,
        provider: str | None = None,
        publisher: str | None = None,
        region: str | None = None,
    ) -> RequestLog:
        entry = RequestLog(
            subject=subject,
            auth_method=auth_method,
            use_case=use_case,
            source_ip=source_ip,
            credential=credential,
            api=api,
            operation=operation,
            model=model,
            requested_model=requested_model,
            model_selection=model_selection,
            status=status,
            outcome=outcome,
            pipeline_decisions=pipeline_decisions,
            # An empty mapping means "nothing was degraded", which is a fact worth keeping
            # distinct from "we did not look" — so it is stored rather than collapsed to NULL.
            degraded=degraded,
            provider=provider,
            publisher=publisher,
            region=region,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            latency_ms=latency_ms,
            trace_id=trace_id,
            request_payload=request_payload,
            response_payload=response_payload,
            cost_nanos=cost_nanos,
        )
        self._session.add(entry)
        await self._session.commit()
        # No refresh: the sessionmaker uses expire_on_commit=False, so everything a caller reads
        # from the returned entry is already populated. Re-selecting the row would be an extra
        # query per logged request for nothing — and on a shared connection it is not merely
        # wasteful but a source of spurious failures.
        return entry
