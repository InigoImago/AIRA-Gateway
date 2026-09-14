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
from aira_gateway.reporting.service import ReportingService
from aira_gateway.state import sessionmaker_of, settings_of


@router.get("/v1beta/reporting")
async def reporting(
    request: Request,
    principal: Principal = Depends(require_principal),
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
    breakdown: str = Query(default="use_case"),
    use_case: str = Query(default="", max_length=64),
) -> Response:
    """Spend and usage over a window, defaulting to the current calendar month.

    ``use_case`` narrows the report to one use case (`FRD-603`), so its consumption can be shown
    where no budget gives it a denominator.
    """
    window_start, window_end = window(start, end, what="reporting")
    fmt = negotiate(request.headers.get("accept", ""))
    if fmt == "csv" and breakdown not in BREAKDOWNS:
        raise GeminiHTTPError(
            400,
            f"'{breakdown}' is not a breakdown. Available: {', '.join(BREAKDOWNS)}.",
            "INVALID_ARGUMENT",
        )

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

    report = await service.report(scope, window_start, window_end)
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
