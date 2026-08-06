"""Spend and usage reporting (FRD-601).

Read-only aggregates — no payloads, and deliberately no way to reach one. Browsing individual
requests would show stored prompts to people who are precisely *not* members of the use case that
produced them, which is what content redaction (FRD-406) exists to make safe; ADR-0009 records
why that view waits rather than shipping alongside this one.

The visibility rule is resolved here, once, at the edge:

    governance role  → every use case
    otherwise        → the caller's Keycloak group memberships
    neither          → an empty report

The last line is the one worth stating: a use-case user with nothing to show is not an error.
Refusing them would say "you may not look", when the truth is "there is nothing there yet".
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.reporting.csv_export import BREAKDOWNS, filename, render
from aira_gateway.reporting.service import ReportingService, Scope

router = APIRouter(tags=["reporting"])

# A year at a time. The window is indexed, but an unbounded one invites a report over all of
# history from a caller who mistyped a date — and the answer to that is a bound, not a timeout.
MAX_WINDOW_DAYS = 366


def visible_scope(principal: Principal) -> Scope:
    """Which use cases this caller may be shown.

    ``None`` means every one of them and is deliberately distinct from ``()``, which means none.
    Returning the wrong one of those is the single mistake here that would show an installation's
    whole spend to somebody entitled to one use case.
    """
    if principal.is_governance:
        return None
    if principal.method == "demo":
        # Authentication is switched off entirely; there is no identity to scope by, and the
        # demo mode is not a place to invent one.
        return None
    return principal.use_cases


def _month_window(now: datetime) -> tuple[datetime, datetime]:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days = calendar.monthrange(start.year, start.month)[1]
    return start, start + timedelta(days=days)


def _parse(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise GeminiHTTPError(
            400, f"'{field}' is not an ISO-8601 timestamp.", "INVALID_ARGUMENT"
        ) from exc
    # A naive timestamp is read as UTC rather than refused: every figure in the system is stored
    # in UTC, and guessing the caller's zone would be worse than saying which one is assumed.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _negotiate(accept: str) -> str:
    """JSON or CSV, and **406 for anything else** (`FRD-602` FR-1).

    A caller asking for XML is better told no than handed JSON: the second answer looks like it
    worked, and the mismatch surfaces in their parser rather than in ours. `*/*` and an absent
    header mean JSON, because that is what every browser and every HTTP client sends by default.
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


@router.get("/v1beta/reporting")
async def reporting(
    request: Request,
    principal: Principal = Depends(require_principal),
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
    breakdown: str = Query(default="use_case"),
) -> Response:
    """Spend and usage over a window, defaulting to the current calendar month.

    CSV is a **rendering of this same result**, chosen by `Accept`, and deliberately not its own
    endpoint (`FRD-602` §5.3). The visibility rule below is one function guarded by its own
    mutations; a second entry point would be a second chance to forget it, and the way an export
    comes to return more than the screen does.
    """
    now = datetime.now(UTC)
    default_start, default_end = _month_window(now)
    window_start = _parse(start, "from") if start else default_start
    window_end = _parse(end, "to") if end else default_end

    if window_end <= window_start:
        raise GeminiHTTPError(400, "'to' must be after 'from'.", "INVALID_ARGUMENT")
    if window_end - window_start > timedelta(days=MAX_WINDOW_DAYS):
        raise GeminiHTTPError(
            400, f"A reporting window may span at most {MAX_WINDOW_DAYS} days.", "INVALID_ARGUMENT"
        )

    fmt = _negotiate(request.headers.get("accept", ""))
    if fmt == "csv" and breakdown not in BREAKDOWNS:
        raise GeminiHTTPError(
            400,
            f"'{breakdown}' is not a breakdown. Available: {', '.join(BREAKDOWNS)}.",
            "INVALID_ARGUMENT",
        )

    service: ReportingService = request.app.state.reporting
    scope = visible_scope(principal)
    report = await service.report(scope, window_start, window_end)
    report["scope"] = "all" if scope is None else "use_cases"

    if fmt == "json":
        return JSONResponse(report)

    settings = request.app.state.settings
    body = render(report, breakdown, settings.currency)
    name = filename(breakdown, window_start.isoformat(), window_end.isoformat())
    return Response(
        content=body,
        # The charset is declared even though the BOM says it too: a consumer that trusts the
        # header and one that sniffs the bytes must reach the same conclusion.
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
