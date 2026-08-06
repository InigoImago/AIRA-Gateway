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

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
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


@router.get("/v1beta/reporting")
async def reporting(
    request: Request,
    principal: Principal = Depends(require_principal),
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
) -> JSONResponse:
    """Spend and usage over a window, defaulting to the current calendar month."""
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

    service: ReportingService = request.app.state.reporting
    report = await service.report(visible_scope(principal), window_start, window_end)
    report["scope"] = "all" if visible_scope(principal) is None else "use_cases"
    return JSONResponse(report)
