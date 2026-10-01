"""The spend report and the register of processing activities (`FRD-601`, `FRD-608`).

Both answer JSON, or CSV by `Accept` — a rendering of the same result, never a second endpoint or
query (`FRD-602` §5.3): a second entry point is a second chance to forget `visible_scope`.
"""

from __future__ import annotations

from fastapi import Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from aira_common.permissions import Permission
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.reporting.common import (
    csv_attachment,
    negotiate,
    router,
    scope_label,
    visible_scope,
    window,
)
from aira_gateway.auth.dependencies import require_principal, require_valid_use_case
from aira_gateway.auth.principal import Principal
from aira_gateway.reporting.csv_export import BREAKDOWNS, filename, render
from aira_gateway.reporting.register import RegisterService
from aira_gateway.reporting.register_csv import filename as register_filename
from aira_gateway.reporting.register_csv import render as render_register
from aira_gateway.reporting.series import SPLITS, TooManyBuckets, resolve
from aira_gateway.reporting.service import ReportingService
from aira_gateway.state import sessionmaker_of, settings_of

#: What may be asked of the series parameter. ``auto`` lets the window decide; the empty string is
#: *no series at all*, which is what a caller who only wants the tables sends.
GRANULARITIES = ("auto", "day", "hour")


@router.get("/v1beta/reporting")
async def reporting(
    request: Request,
    principal: Principal = Depends(require_principal),
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
    breakdown: str = Query(default="use_case"),
    use_case: str = Query(default="", max_length=64),
    series: str = Query(default="", max_length=8),
    split: str = Query(default="model", max_length=16),
    person: str = Query(default="", max_length=255),
) -> Response:
    """Spend and usage over a window, defaulting to the current calendar month.

    ``use_case`` narrows the report to one use case (`FRD-603`), so its consumption can be shown
    where no budget gives it a denominator. ``series`` adds the period bucket by bucket and
    ``split`` says what the bands are (`FRD-626`). ``person`` narrows it to one person's own
    traffic, which is what a member's own card on a use case's page asks for.
    """
    window_start, window_end = window(start, end, what="reporting")
    fmt = negotiate(request.headers.get("accept", ""))
    if fmt == "csv" and breakdown not in BREAKDOWNS:
        raise GeminiHTTPError(
            400,
            f"'{breakdown}' is not a breakdown. Available: {', '.join(BREAKDOWNS)}.",
            "INVALID_ARGUMENT",
        )

    # **Said rather than discovered through a 422** (`FRD-206`): every other refusal on this surface
    # carries the Gemini error envelope, and a client that parses one would have to parse two.
    if series and series not in GRANULARITIES:
        raise GeminiHTTPError(
            400,
            f"'{series}' is not a granularity. Available: {', '.join(GRANULARITIES)}.",
            "INVALID_ARGUMENT",
        )
    if split not in SPLITS:
        raise GeminiHTTPError(
            400, f"'{split}' is not a split. Available: {', '.join(SPLITS)}.", "INVALID_ARGUMENT"
        )
    try:
        granularity = resolve(window_start, window_end, series) if series else None
    except TooManyBuckets as exc:
        raise GeminiHTTPError(400, str(exc), "INVALID_ARGUMENT") from exc

    service: ReportingService = request.app.state.reporting
    scope = visible_scope(principal, Permission.REPORT_READ_ALL)

    # A filter narrows, never widens (`FRD-505` FR-3). Outside the caller's scope the report is
    # empty, and `in_scope` tells that empty apart from "nothing happened here".
    in_scope = True
    if use_case:
        require_valid_use_case(use_case)
        if scope is None or use_case in scope:
            scope = (use_case,)
        else:
            scope, in_scope = (), False

    # **A narrowing, and nothing more.** It adds a predicate to the window the caller's scope has
    # already bounded, so it can only ever subtract rows — and it reaches nothing `by_person` does
    # not already carry to the same caller (`FRD-606`). The scope above is what decides visibility,
    # here as everywhere else on this surface.
    report = await service.report(
        scope,
        window_start,
        window_end,
        granularity=granularity,
        split=split,
        # Passed as it arrived. `_window` is the one place that decides whether there is a filter
        # at all (`if person:`), and coercing `""` to `None` here as well would be a second copy of
        # that rule — written, observed not to be reachable by any single edit, and deleted rather
        # than kept as a claim (`tools/mutation_check.py`'s own note on a property enforced twice).
        person=person,
    )
    report["person"] = person or None
    report["scope"] = scope_label(scope)
    report["use_case"] = use_case or None
    report["in_scope"] = in_scope

    if fmt == "json":
        return JSONResponse(report)
    body = render(report, breakdown, settings_of(request).currency)
    return csv_attachment(
        body, filename(breakdown, window_start.isoformat(), window_end.isoformat())
    )


@router.get("/v1beta/register")
async def register(
    request: Request,
    principal: Principal = Depends(require_principal),
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
) -> Response:
    """The register of processing activities (`FRD-608`).

    One row per use case — purpose, released models and where they live, retention, controls,
    members — **and where its traffic actually went** over the window. Served by the gateway
    although Management authors the configuration: the read-model carries that half (`UseCaseRead`)
    and the audit trail is the other.
    """
    window_start, window_end = window(start, end, what="register")
    fmt = negotiate(request.headers.get("accept", ""))
    scope = visible_scope(principal, Permission.REPORT_READ_ALL)
    compiled = await RegisterService(sessionmaker_of(request)).compile(
        scope, window_start, window_end
    )

    if fmt == "csv":
        body = render_register(compiled, window_start.isoformat(), window_end.isoformat())
        return csv_attachment(
            body, register_filename(window_start.isoformat(), window_end.isoformat())
        )

    return JSONResponse(
        {
            "from": window_start.isoformat(),
            "to": window_end.isoformat(),
            "scope": scope_label(scope),
            **compiled.as_dict(),
        }
    )
