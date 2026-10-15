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
from aira_gateway.payloads import people_restricted_use_cases, sees_people
from aira_gateway.reporting.csv_export import BREAKDOWNS, filename, render
from aira_gateway.reporting.register import RegisterService
from aira_gateway.reporting.register_csv import filename as register_filename
from aira_gateway.reporting.register_csv import render as render_register
from aira_gateway.reporting.series import SPLITS, TooManyBuckets, resolve
from aira_gateway.reporting.service import ReportingService, Scope
from aira_gateway.state import sessionmaker_of, settings_of

#: What may be asked of the series parameter. ``auto`` lets the window decide; the empty string is
#: *no series at all*, which is what a caller who only wants the tables sends.
GRANULARITIES = ("auto", "day", "hour")

#: The splits keyed on a human. The CSV offers one of them; the JSON carries both.
PERSON_BREAKDOWNS = ("member",)
PERSON_SPLITS = ("by_member", "by_person")

#: How long a person took, which under strict data mode no split by person carries: it measures
#: performance, and answers no question about cost (`FRD-627` FR-5).
LATENCY_FIELDS = ("avg_latency_ms", "max_latency_ms")


def _without_latency_per_person(report: dict[str, object]) -> None:
    for split in PERSON_SPLITS:
        for row in report.get(split) or []:  # type: ignore[attr-defined]
            for field in LATENCY_FIELDS:
                row.pop(field, None)


async def _people_limited_to(request: Request, principal: Principal, scope: Scope) -> str | None:
    """The caller's own name, when the use cases in view withhold **other people's** figures.

    ``None`` means "show everybody", which is the ordinary answer: a role that may read every
    figure, a use case on `members`, or an administrator of one on `own`. A name means the two
    splits keyed on a human carry that person's row and nothing else.

    **All or nothing across the window on purpose.** A report over several use cases, one of which
    withholds, cannot show "everybody except them" without saying which use case each person's
    figures came from — the splits are keyed on a person, not on a pair. So one withholding use
    case in view narrows the splits for the whole report, and a reader who needs the rest asks for
    one use case at a time, which is what the use-case page does anyway. The alternative — dropping
    the narrow use case's rows silently into an aggregate by person — is the disclosure this exists
    to stop.
    """
    if settings_of(request).strict_data_mode:
        # Every use case is `oversight`, and reading every figure is no exemption: a split by
        # person is for whoever sees people, and for the person (`FRD-627` FR-4).
        return None if sees_people(principal) else _own_name(principal)
    if principal.allows(Permission.REPORT_READ_ALL):
        return None
    async with sessionmaker_of(request)() as session:
        narrowed = await people_restricted_use_cases(session, principal, strict_data=False)
    if not narrowed:
        return None
    if scope is not None and not set(narrowed) & set(scope):
        return None
    return _own_name(principal)


def _own_name(principal: Principal) -> str:
    """The name the figures are keyed on, with the subject behind it: an API key's subject *is* the
    name, and an older row carries no name at all (`FRD-606`)."""
    return principal.person or principal.subject


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

    # Whether this caller may see **other people's** figures in the use cases they are looking at
    # (`FRD-603` §5.5): the use case's content setting decides, and where it does not allow it the
    # two splits keyed on a human carry the caller's own row only. Resolved here, beside the
    # scope, because this is the one place that decides what a report is about.
    limited = await _people_limited_to(request, principal, scope)
    if limited is not None and person and person not in {limited, principal.subject}:
        # `person` narrows the **whole** report to one person, so naming somebody whose split this
        # reader may not see would hand over that split by another route: the totals, every
        # breakdown and the series of a report narrowed to a colleague are the colleague's. Outside
        # the reader's reach, it answers what any filter outside it answers — an empty report.
        scope, in_scope = (), False

    # **A narrowing, and nothing more.** It adds a predicate to the window the caller's scope has
    # already bounded, so it can only ever subtract rows — and, with the check above, it reaches
    # nothing `by_person` does not already carry to the same caller (`FRD-606`). The scope is what
    # decides visibility, here as everywhere else on this surface.
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
        people_limited_to=limited,
    )
    report["person"] = person or None
    report["scope"] = scope_label(scope)
    report["use_case"] = use_case or None
    report["in_scope"] = in_scope
    # **Said rather than inferred** (`FRD-206`): the console shows one row instead of a table when
    # this is true, and a second copy of the rule in the browser would be the copy that goes stale
    # — and the one that could invent a restriction the server did not apply.
    report["people_limited"] = limited is not None
    # The installation's own budget is edited on this page (`FRD-610` §3.1), so the page is told
    # whether budgets refuse at all. Same fact as `/v1beta/usage` and `/v1beta/me/budget`, from the
    # same settings object — one plane answers for it, the one that enforces.
    report["enforced"] = settings_of(request).enforce_budgets
    # Said for the same reason as `people_limited`: the console hides the split by person rather
    # than show a table of one, and must not decide the mode itself (`FRD-627` FR-9).
    strict_data = settings_of(request).strict_data_mode
    report["strict_data"] = strict_data
    if strict_data:
        _without_latency_per_person(report)

    if fmt == "json":
        return JSONResponse(report)
    body = render(
        report,
        breakdown,
        settings_of(request).currency,
        latency=not (strict_data and breakdown in PERSON_BREAKDOWNS),
    )
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
    compiled = await RegisterService(
        sessionmaker_of(request), strict_data=settings_of(request).strict_data_mode
    ).compile(scope, window_start, window_end)

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
