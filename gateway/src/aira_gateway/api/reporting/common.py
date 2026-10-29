"""What every reporting endpoint shares: who may see what, and how its query parameters are read."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

from fastapi import APIRouter, Response

from aira_common.clock import month_ends, month_starts, read_in
from aira_common.permissions import Permission
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.principal import Principal
from aira_gateway.persistence.sanitize import printable
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


def _parse(value: str, field: str, zone: tzinfo = UTC) -> datetime:
    """An ISO-8601 timestamp as an instant **in UTC**, or a 400. One without an offset — a bare date
    above all — is read on the installation's clock (``zone``, `FRD-629`), so `2026-10-01` means
    that day's midnight where the installation is.

    Converted, not merely labelled: the series' buckets are folded from UTC hours (`ADR-0028`), so a
    window kept in the caller's offset draws an axis its own rows fall off — and SQLite, which
    drops an offset from a comparison, evaluates another window.
    """
    try:
        parsed = datetime.fromisoformat(value)
        return read_in(parsed, zone).astimezone(UTC)
    except ValueError as exc:
        raise GeminiHTTPError(
            400, f"'{field}' is not an ISO-8601 timestamp.", "INVALID_ARGUMENT"
        ) from exc
    except OverflowError as exc:
        # The first instant of year one in an eastern offset is before the first one UTC has.
        raise GeminiHTTPError(
            400, f"'{field}' is outside the range of time this API can express.", "INVALID_ARGUMENT"
        ) from exc


def window(
    start: str | None, end: str | None, *, what: str, zone: tzinfo = UTC
) -> tuple[datetime, datetime]:
    """``[from, to)`` from the query: the current calendar month on the installation's clock by
    default, at most `MAX_WINDOW_DAYS` long. ``what`` names the document in the refusal."""
    now = datetime.now(UTC)
    window_start = _parse(start, "from", zone) if start else month_starts(now, zone)
    window_end = _parse(end, "to", zone) if end else month_ends(now, zone)

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
    # An id with a control character is no row's — and a NUL would be Postgres's 500.
    if not row_id or printable(row_id) != row_id:
        raise GeminiHTTPError(
            400, "'cursor' is not a cursor from this endpoint.", "INVALID_ARGUMENT"
        )
    return _parse(stamp, "cursor"), row_id
