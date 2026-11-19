"""Persistence of request/response logs (`FRD-103`)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession

from aira_gateway.audit import UsageBasis
from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.models import RequestLog
from aira_gateway.persistence.sanitize import printable

#: Every text column of `request_logs` and the width it holds, `None` for text without one —
#: **read from the model**, so a column a migration widens is wider here too, and a column added
#: later is held to its width without anybody remembering to (see `_fits`).
TEXT_COLUMNS: dict[str, int | None] = {
    column.key: column.type.length
    for column in RequestLog.__table__.columns
    if isinstance(column.type, String)
}

#: The widths of the columns a caller chooses the content of: the model and the method from the
#: URL, the subject and the name from the token.
MODEL_COLUMN = TEXT_COLUMNS["model"]  # also `.requested_model`
OPERATION_COLUMN = TEXT_COLUMNS["operation"]
SUBJECT_COLUMN = TEXT_COLUMNS["subject"]  # also `.username`

#: The largest count a 32-bit `request_logs` column holds: the token counts, the latency and the
#: request's size (see `_count`).
COUNT_CEILING = 2**31 - 1


def _fits(value: str | None, width: int | None = MODEL_COLUMN) -> str | None:
    """``value``, in a form its column can store — **a row that does not fit is a lost row**.

    Every text of an audit row passes here on its way in (:meth:`RequestLogService.record`): the
    caller chooses the model and the method in the URL, the subject in the token and the words its
    refusal quotes, and an installation the name of a server and its region. Two shapes fail the
    INSERT on Postgres after the request was handled — a NUL in any text, and a value wider than
    its `varchar` — and the row that would have recorded the request is what is lost. SQLite
    enforces neither, so the hermetic suite cannot see it.

    A column with a width holds one line — a name, an identifier, a code — and keeps no control
    character; they are removed **before** the cut, so the width counts characters that survive.
    One without, the message a caller was given, loses only the NUL. Sanitised rather than refused:
    a slightly less faithful row beats no row (`FRD-122`).
    """
    if value is None:
        return None
    if width is None:
        return value.replace("\x00", "")
    return printable(value)[:width]


def _count(value: int | None) -> int | None:
    """``value`` held to what an integer column of `request_logs` can store.

    The token counts arrive as the upstream adapter reported them, and a misbehaving server can
    report anything: a figure of 2³¹ or more, or a negative one, fails the INSERT on Postgres and
    loses the row — the way `_fits` keeps an over-long string from doing the same. SQLite stores
    either, which is why the hermetic suite cannot see it. ``None`` stays: unknown is not zero.
    """
    if value is None:
        return None
    return max(0, min(int(value), COUNT_CEILING))


#: The longest caller's message an audit row keeps. A refusal's message is a sentence or two; an
#: upstream's error text is capped where it is read, and this bounds whatever reaches the row.
ERROR_MESSAGE_LIMIT = 1000


def _bounded_message(message: str | None) -> str | None:
    if not message:
        return None
    return (
        message if len(message) <= ERROR_MESSAGE_LIMIT else message[: ERROR_MESSAGE_LIMIT - 1] + "…"
    )


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
        usage_basis: str | None = None,
        requested_model: str | None = None,
        model_selection: str | None = None,
        pipeline_decisions: list[dict[str, Any]] | None = None,
        flagged: bool = False,
        tool_calls: dict[str, Any] | None = None,
        degraded: dict[str, str] | None = None,
        technical: dict[str, Any] | None = None,
        provider: str | None = None,
        publisher: str | None = None,
        region: str | None = None,
        request_bytes: int | None = None,
        #: The audit row's id where the request already carries one (`X-AIRA-Request-Id`).
        row_id: str | None = None,
        error: str | None = None,
    ) -> RequestLog:
        # A counted usage is two counts and nothing more: the cache split and the thinking were in
        # a report that never came, so they are unknown here — and zero would claim a cache miss
        # and a model that did not think (`FRD-128` FR-6).
        counted = usage_basis == UsageBasis.COUNTED
        entry = RequestLog(
            # The id the caller was given in `X-AIRA-Request-Id`, so the row is found by it.
            **({"id": row_id} if row_id else {}),
            error=_bounded_message(error),
            subject=subject,
            username=username,
            auth_method=auth_method,
            use_case=use_case,
            source_ip=source_ip,
            credential=credential,
            issuer=issuer,
            api=api,
            operation=operation,
            model=model,
            requested_model=requested_model,
            model_selection=model_selection,
            status=status,
            outcome=outcome,
            usage_basis=usage_basis,
            pipeline_decisions=pipeline_decisions,
            flagged=flagged,
            tool_calls=tool_calls,
            # `{}` means "nothing was degraded" and is kept apart from NULL, "we did not look".
            degraded=degraded,
            technical=technical,
            provider=provider,
            publisher=publisher,
            region=region,
            prompt_tokens=_count(usage.prompt_tokens) if usage else None,
            # Recorded even when zero: on a caching provider, zero is a miss, which is what makes a
            # cache that stopped working visible (`FRD-133` FR-5). NULL means no usage at all.
            cached_input_tokens=(
                _count(usage.cached_input_tokens) if usage and not counted else None
            ),
            cache_write_tokens=_count(usage.cache_write_tokens) if usage and not counted else None,
            completion_tokens=_count(usage.completion_tokens) if usage else None,
            reasoning_tokens=_count(usage.reasoning_tokens) if usage and not counted else None,
            total_tokens=_count(usage.total_tokens) if usage else None,
            latency_ms=_count(latency_ms),
            trace_id=trace_id,
            request_payload=request_payload,
            response_payload=response_payload,
            cost_nanos=cost_nanos,
            request_bytes=_count(request_bytes),
        )
        # Column by column rather than argument by argument: nothing reaches a text column of the
        # row without passing `_fits`, whatever was added to it since.
        for name, width in TEXT_COLUMNS.items():
            value = getattr(entry, name)
            if isinstance(value, str):
                setattr(entry, name, _fits(value, width))
        self._session.add(entry)
        await self._session.commit()
        # No refresh: `expire_on_commit=False` leaves the entry populated, and re-selecting it on a
        # shared connection is a source of spurious failures.
        return entry
