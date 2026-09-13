"""Persistence of request/response logs (`FRD-103`)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.models import RequestLog

#: Widths of the `request_logs` columns whose content the caller chooses (see `_fits`).
MODEL_COLUMN = 128  # `.model`, `.requested_model`
OPERATION_COLUMN = 64  # `.operation`
SUBJECT_COLUMN = 255  # `.subject`, `.username` — a token's `sub` and `preferred_username`


def _fits(value: str | None, width: int = MODEL_COLUMN) -> str | None:
    """``value``, in a form the column can store — **a row that does not fit is a lost row**.

    The model name and method come from the caller's URL and the subject from their token, and two
    shapes fail the INSERT on Postgres after the request was handled: a value wider than the column
    and a NUL byte. SQLite enforces neither, so the hermetic suite cannot see it. Control characters
    are removed **before** the cut, so the width counts characters that survive. Sanitised rather
    than refused: a slightly less faithful row beats no row (`FRD-122`).
    """
    if value is None:
        return None
    return "".join(c for c in value if ord(c) >= 0x20 and ord(c) != 0x7F)[:width]


class RequestLogService:
    """Writes ``request_logs`` rows, bound to an async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        subject: str,
        auth_method: str,
        #: Descriptive only (`FRD-606`); `subject` is the identity.
        username: str | None = None,
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
        #: No default — see `PendingLog.api`.
        api: str,
        credential: str | None = None,
        issuer: str | None = None,
        outcome: str | None = None,
        requested_model: str | None = None,
        model_selection: str | None = None,
        pipeline_decisions: list[dict[str, Any]] | None = None,
        flagged: bool = False,
        tool_calls: dict[str, Any] | None = None,
        degraded: dict[str, str] | None = None,
        provider: str | None = None,
        publisher: str | None = None,
        region: str | None = None,
        request_bytes: int | None = None,
    ) -> RequestLog:
        entry = RequestLog(
            subject=str(_fits(subject, SUBJECT_COLUMN)),
            username=_fits(username, SUBJECT_COLUMN),
            auth_method=auth_method,
            use_case=use_case,
            source_ip=source_ip,
            credential=credential,
            issuer=issuer,
            api=api,
            operation=str(_fits(operation, OPERATION_COLUMN)),
            model=str(_fits(model)),
            requested_model=_fits(requested_model),
            model_selection=model_selection,
            status=status,
            outcome=outcome,
            pipeline_decisions=pipeline_decisions,
            flagged=flagged,
            tool_calls=tool_calls,
            # `{}` means "nothing was degraded" and is kept apart from NULL, "we did not look".
            degraded=degraded,
            provider=provider,
            publisher=publisher,
            region=region,
            prompt_tokens=usage.prompt_tokens if usage else None,
            # Recorded even when zero: on a caching provider, zero is a miss, which is what makes a
            # cache that stopped working visible (`FRD-133` FR-5). NULL means no usage at all.
            cached_input_tokens=usage.cached_input_tokens if usage else None,
            cache_write_tokens=usage.cache_write_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            reasoning_tokens=usage.reasoning_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
            latency_ms=latency_ms,
            trace_id=trace_id,
            request_payload=request_payload,
            response_payload=response_payload,
            cost_nanos=cost_nanos,
            request_bytes=request_bytes,
        )
        self._session.add(entry)
        await self._session.commit()
        # No refresh: `expire_on_commit=False` leaves the entry populated, and re-selecting it on a
        # shared connection is a source of spurious failures.
        return entry
