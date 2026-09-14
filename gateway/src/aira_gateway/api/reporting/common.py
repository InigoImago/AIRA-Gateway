"""What every reporting endpoint shares: who may see what, and how its query parameters are read."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response

from aira_common.permissions import Permission
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.principal import Principal
from aira_gateway.reporting.service import Scope

#: The widest window a report or register may span. The window is indexed, but an unbounded one
#: invites a report over all of history from a caller who mistyped a date.
MAX_WINDOW_DAYS = 366

#: One router for the package. Each module registers on it rather than on a router of its own,
#: because an included router is nested and would hide its routes from `router.routes`.
router = APIRouter(tags=["reporting"])


def visible_scope(principal: Principal, permission: Permission) -> Scope:
    """Which use cases this caller may be shown.

    ``None`` means every one and is deliberately distinct from ``()``, which means none: returning
    the wrong one would show an installation's whole spend to somebody entitled to one use case.
    ``permission`` is what lets a caller see every use case on this surface (`FRD-614`): request
    lists, figures and findings are separate permissions, so a role can hold one without the others.
    """
    if principal.allows(permission):
        return None
    if principal.method == "demo":
        # Authentication is switched off: there is no identity to scope by.
        return None
    return principal.use_cases


def scope_label(scope: Scope) -> str:
    """Which answer this is: ``all``, or the caller's ``use_cases`` — possibly none of them."""
    return "all" if scope is None else "use_cases"


def _parse(value: str, field: str) -> datetime:
    """An ISO-8601 timestamp, or a 400. A naive one is read as UTC, the zone every figure is in."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise GeminiHTTPError(
            400, f"'{field}' is not an ISO-8601 timestamp.", "INVALID_ARGUMENT"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _month_window(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days = calendar.monthrange(start.year, start.month)[1]
    return start, start + timedelta(days=days)


def window(start: str | None, end: str | None, *, what: str) -> tuple[datetime, datetime]:
    """``[from, to)`` from the query: the current calendar month by default, at most
    `MAX_WINDOW_DAYS` long. ``what`` names the document in the refusal."""
    default_start, default_end = _month_window(datetime.now(UTC))
    window_start = _parse(start, "from") if start else default_start
    window_end = _parse(end, "to") if end else default_end

    if window_end <= window_start:
        raise GeminiHTTPError(400, "'to' must be after 'from'.", "INVALID_ARGUMENT")
    if window_end - window_start > timedelta(days=MAX_WINDOW_DAYS):
        raise GeminiHTTPError(
            400, f"A {what} window may span at most {MAX_WINDOW_DAYS} days.", "INVALID_ARGUMENT"
        )
    return window_start, window_end


def negotiate(accept: str) -> str:
    """JSON or CSV, and **406 for anything else** (`FRD-602` FR-1).

    XML answered with JSON would fail in the caller's parser instead of here. ``*/*`` and an absent
    header mean JSON, as every client sends by default.
    """
    wanted = accept.lower()
    if "text/csv" in wanted:
        return "csv"
    if not wanted.strip() or "application/json" in wanted or "*/*" in wanted:
        return "json"
    raise GeminiHTTPError(
        406,
        f"This endpoint serves application/json or text/csv, not '{accept}'.",
        "INVALID_ARGUMENT",
    )


def csv_attachment(body: str, name: str) -> Response:
    """A CSV download. The charset is declared although the BOM says it too, so a consumer that
    trusts the header and one that sniffs the bytes reach the same conclusion."""
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def parse_cursor(cursor: str) -> tuple[datetime, str]:
    """``<iso8601>|<id>`` — the id makes the order total, since two rows can share a millisecond
    (`FRD-502` §4.2)."""
    stamp, _, row_id = cursor.partition("|")
    if not row_id:
        raise GeminiHTTPError(
            400, "'cursor' is not a cursor from this endpoint.", "INVALID_ARGUMENT"
        )
    return _parse(stamp, "cursor"), row_id
