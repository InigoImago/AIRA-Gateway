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
        api: str = "gemini",
    ) -> RequestLog:
        entry = RequestLog(
            subject=subject,
            auth_method=auth_method,
            use_case=use_case,
            source_ip=source_ip,
            api=api,
            operation=operation,
            model=model,
            status=status,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            latency_ms=latency_ms,
            trace_id=trace_id,
            request_payload=request_payload,
            response_payload=response_payload,
        )
        self._session.add(entry)
        await self._session.commit()
        await self._session.refresh(entry)
        return entry
