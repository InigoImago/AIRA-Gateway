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

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from aira_common.anomalies import RuleTarget
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.audit import Outcome
from aira_gateway.auth.dependencies import require_principal, require_valid_use_case
from aira_gateway.auth.principal import Principal
from aira_gateway.db.models import AnomalyEvent, PayloadAccess, RequestLog
from aira_gateway.payloads import may_read_payload, payload_body, restricted_use_cases
from aira_gateway.reporting.csv_export import BREAKDOWNS, filename, render
from aira_gateway.reporting.service import ReportingService, Scope
from aira_gateway.state import sessionmaker_of, settings_of

_log = structlog.get_logger(__name__)

router = APIRouter(tags=["reporting"])

# A year at a time. The window is indexed, but an unbounded one invites a report over all of
# history from a caller who mistyped a date — and the answer to that is a bound, not a timeout.
MAX_WINDOW_DAYS = 366


def visible_scope(principal: Principal) -> Scope:
    """Which use cases this caller may be shown.

    ``None`` means every one of them and is deliberately distinct from ``()``, which means none.
    Returning the wrong one of those is the single mistake here that would show an installation's
    whole spend to somebody entitled to one use case.

    **`is_oversight`, not `is_governance` — corrected 2026-08-08.** They differ by exactly one role:
    IT Security. Asking the narrower predicate meant the role whose job is investigating an incident
    saw an **empty** reporting screen and an **empty** trace list — not a refusal, which would at
    least have been a question, but nothing, which reads as "no traffic exists".

    This is `FRD-206`'s defect arriving in the other plane. It was found there in the console
    (`scope_queryset` used one role set for both "sees every use case" and "sees every figure") and
    fixed there with `OVERSIGHT_ROLES ⊃ GOVERNANCE_ROLES`; the gateway kept the narrow one. One
    definition, two planes, one of them corrected — the shape this project has now recorded four
    times.

    Found by writing a test for a *different* feature: an incident role filtering traces by source
    address got no rows, and the reason was one predicate up.
    """
    if principal.is_oversight:
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
    use_case: str = Query(default="", max_length=64),
) -> Response:
    """Spend and usage over a window, defaulting to the current calendar month.

    CSV is a **rendering of this same result**, chosen by `Accept`, and deliberately not its own
    endpoint (`FRD-602` §5.3). The visibility rule below is one function guarded by its own
    mutations; a second entry point would be a second chance to forget it, and the way an export
    comes to return more than the screen does.

    `use_case` narrows the report to one of them (`FRD-603`). It exists because consumption was
    only ever *displayed* as a fraction of a limit: with no budget configured there was no
    denominator, so the console showed nothing at all — while every request had been counted and
    priced in the request log all along. The figure needed a reader, not a calculation, and this
    is the one query that already knows who may see which use case.
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

    # **A filter narrows, never widens.** The same rule the trace view states (`FRD-505` FR-3),
    # and the one that matters most here: `visible_scope` decides what this caller may be shown,
    # and this parameter may only take an intersection of it. Assigning `scope = (use_case,)`
    # unconditionally would read as a narrowing and be a widening — anybody could name any use
    # case and be told its spend.
    in_scope = True
    if use_case:
        require_valid_use_case(use_case)
        if scope is None or use_case in scope:
            scope = (use_case,)
        else:
            # Emptiness rather than a refusal, and `in_scope` saying which of the two empties this
            # is. The same answer the findings list gives, for the same reason: "nothing happened
            # here" and "this is not yours to see" are different facts, and a screen that cannot
            # tell them apart reports the second as the first.
            scope, in_scope = (), False

    report = await service.report(scope, window_start, window_end)
    report["scope"] = "all" if scope is None else "use_cases"
    report["use_case"] = use_case or None
    report["in_scope"] = in_scope

    if fmt == "json":
        return JSONResponse(report)

    settings = settings_of(request)
    body = render(report, breakdown, settings.currency)
    name = filename(breakdown, window_start.isoformat(), window_end.isoformat())
    return Response(
        content=body,
        # The charset is declared even though the BOM says it too: a consumer that trusts the
        # header and one that sniffs the bytes must reach the same conclusion.
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _about_this_caller(restricted: list[str], principal: Principal) -> ColumnElement[bool]:
    """Findings a caller may see inside a use case that restricts members to their own traffic.

    **Written over `AnomalyEvent`, and that is the whole correction.** This condition was a copy of
    the trace view's, which is written over `RequestLog` — pasted onto a `select(AnomalyEvent)`
    without its subject changing with it. SQLAlchemy resolved the foreign columns by adding
    `request_logs` to the FROM clause with no join predicate, so the statement rendered as
    ``FROM anomaly_events, request_logs``: a cartesian product, and a filter that asked a question
    about *unrelated rows*. Both halves were wrong in the same line — the restriction did not
    apply to a single finding (any one matching request log let every finding through), and one
    page of fifty cost the database *findings × request logs* rows. Found on 2026-08-15 by
    rendering the statement.

    A finding is not a request, so the rule has to be translated rather than copied. What the
    restriction withholds is **other people's activity**, and a finding says who it is about in
    two columns:

    - ``target = use_case`` — about the use case, naming nobody. Kept: withholding it would blind
      a use case's own members to its health while hiding nothing the restriction is about.
    - ``target = subject`` — kept only where it is this caller. `AnomalyEvent.target_value` is
      grouped from `RequestLog.subject`, which is written from the same attribution
      `principal.subject` carries, so the two are the same alphabet (unlike `use_case_members`,
      which keys on the username — see `payloads.grant_role_in`).
    - ``target = credential`` — kept only where it is this caller's own credential. A key prefix
      belongs to its owner, so somebody else's is exactly what is being withheld.
    """
    own: list[ColumnElement[bool]] = [AnomalyEvent.target == RuleTarget.USE_CASE.value]
    if principal.subject:
        own.append(
            and_(
                AnomalyEvent.target == RuleTarget.SUBJECT.value,
                AnomalyEvent.target_value == principal.subject,
            )
        )
    if principal.credential:
        own.append(
            and_(
                AnomalyEvent.target == RuleTarget.CREDENTIAL.value,
                AnomalyEvent.target_value == principal.credential,
            )
        )
    return or_(AnomalyEvent.use_case.notin_(restricted), or_(*own))


@router.get("/v1beta/anomalies")
async def anomalies(
    request: Request,
    principal: Principal = Depends(require_principal),
    limit: int = Query(50, ge=1, le=200),
    cursor: str = Query("", max_length=128),
    use_case: str = Query("", max_length=64),
) -> JSONResponse:
    """What the detector has found (`FRD-501` FR-8).

    Scoped by **the same** `visible_scope` the report uses. Not a second rule that happens to
    agree: a second entry point is a second chance to forget one, which is exactly how an export
    comes to return more than the screen it was exported from (`FRD-602` §1).

    A global rule's findings are shown to everybody who may see the use case the traffic belonged
    to. A finding with no use case at all is oversight-only — there is nobody else it is *about*.
    """
    scope = visible_scope(principal)
    if use_case and scope is not None and use_case not in scope:
        # Same answer, and the same reason, as the trace view: emptiness rather than a refusal,
        # with `in_scope` saying which of the two empties this is (see `_out_of_scope`).
        return JSONResponse(
            {"events": [], "next_cursor": None, "scope": "use_cases", "in_scope": False}
        )

    stmt = select(AnomalyEvent)
    if scope is not None:
        stmt = stmt.where(AnomalyEvent.use_case.in_(list(scope)))
    # Filtered here rather than in the browser: a console that fetched the newest hundred findings
    # and kept the ones matching its slug would show a busy installation's quiet use case nothing,
    # because its findings were pushed off the end by somebody else's.
    if use_case:
        stmt = stmt.where(AnomalyEvent.use_case == use_case)

    # A use case may show its **users** their own requests only (`FRD-505` FR-4). Applied to the
    # list and not only to the payload: leaving the rows visible would still disclose who else
    # calls, how often and at what cost, which is the interesting half of what is being withheld.
    sessionmaker = sessionmaker_of(request)
    async with sessionmaker() as session:
        restricted = await restricted_use_cases(session, principal)
    if restricted:
        stmt = stmt.where(_about_this_caller(restricted, principal))

    # Paged by cursor, for the same reason the trace view is (`FRD-502` §4.2) and not the one the
    # use-case list is: findings are an **append-only log**. A detector firing while somebody reads
    # page two of an offset-paged list pushes rows across the boundary, so they see one row twice
    # and never see another — invisibly. The cursor is `(created_at, id)` because two findings can
    # share a millisecond, written out because SQLite has no tuple comparison.
    if cursor:
        at, row_id = _parse_cursor(cursor)
        stmt = stmt.where(
            or_(
                AnomalyEvent.created_at < at,
                and_(AnomalyEvent.created_at == at, AnomalyEvent.id < row_id),
            )
        )
    stmt = stmt.order_by(AnomalyEvent.created_at.desc(), AnomalyEvent.id.desc()).limit(limit + 1)

    sessionmaker = sessionmaker_of(request)
    async with sessionmaker() as session:
        rows = list((await session.execute(stmt)).scalars().all())

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = f"{rows[-1].created_at.isoformat()}|{rows[-1].id}" if has_more and rows else None

    return JSONResponse(
        {
            "events": [
                {
                    "id": row.id,
                    "created_at": row.created_at.isoformat(),
                    "rule": row.rule_name,
                    "kind": row.kind,
                    "use_case": row.use_case,
                    "target": row.target,
                    "target_value": row.target_value,
                    "observed": row.observed,
                    "threshold": row.threshold,
                    "sample": row.sample,
                    "window_minutes": row.window_minutes,
                    # What was *done*, which `ADR-0014` §3 keeps separate from what was detected.
                    "action_taken": row.action_taken,
                    "detail": row.detail,
                }
                for row in rows
            ],
            "next_cursor": next_cursor,
            "scope": "all" if scope is None else "use_cases",
            "in_scope": True,
        }
    )


#: What a trace row exposes. A **allow-list**, not an exclusion list: a column added to
#: `request_logs` tomorrow must not appear here because somebody forgot to exclude it, and the two
#: columns that must never appear (`request_payload`, `response_payload`) are exactly the ones a
#: forgotten exclusion would leak (`FRD-502` FR-11).
TRACE_FIELDS = (
    "id",
    "created_at",
    "operation",
    "api",
    "model",
    "requested_model",
    "model_selection",
    "status",
    "outcome",
    "prompt_tokens",
    "completion_tokens",
    #: Of the completion, how much was thinking (`FRD-135`). Beside the two it is a subset of, so
    #: a reader can see what reasoning cost without doing arithmetic against the provider's bill.
    "reasoning_tokens",
    "total_tokens",
    "latency_ms",
    "cost_nanos",
    "provider",
    "region",
    "trace_id",
    "subject",
    "credential",
    "use_case",
    #: What the model asked to have run (`FRD-131` FR-7). **Names and counts, never arguments** —
    #: the column itself holds no more than that, so exposing it here cannot leak content.
    #:
    #: The most-asked question of this whole view: a governed *model* is evidenced by tokens and
    #: cost, a governed *agent* is evidenced by what it tried to do.
    "tool_calls",
    #: Whether a pipeline step objected — blocked, or flagged and let through (`FRD-505` FR-5).
    #: The second is the interesting one on a screen: a blocked request announces itself by
    #: failing, a flagged one is a 200 with a note nobody would otherwise look at twice.
    "flagged",
)

#: Fields only an incident role sees (`FRD-502` FR-12, added 2026-08-08).
#:
#: `source_ip` answers "which machine is doing this", which is the first question of an incident and
#: personal data the rest of the time. It is therefore not in the list above: a use-case
#: administrator investigating their own traffic gets everything except the address, and IT Security
#: gets the address as well. Two different answers to "what may I see", kept apart rather than
#: merged into the more permissive one.
INCIDENT_FIELDS = ("source_ip",)

#: Rows per page. Enough to fill a screen twice, few enough that a live refresh is cheap.
TRACE_PAGE = 50
MAX_TRACE_PAGE = 200


def _parse_cursor(cursor: str) -> tuple[datetime, str]:
    """``<iso8601>|<id>`` — a timestamp alone is not a cursor.

    Two rows can share a millisecond, and under an appending table an offset page shows some rows
    twice and skips others (`FRD-502` §4.2). The id makes the ordering total.
    """
    stamp, _, row_id = cursor.partition("|")
    if not row_id:
        raise GeminiHTTPError(
            400, "'cursor' is not a cursor from this endpoint.", "INVALID_ARGUMENT"
        )
    return _parse(stamp, "cursor"), row_id


def _out_of_scope() -> dict[str, object]:
    """The answer when the caller's visible scope does not cover what was asked for.

    Still an empty list and still a 200 — "there is nothing here" and "you may not look" stay
    indistinguishable, which is the point of answering emptiness rather than 403 (`FRD-601`).
    But `in_scope: false` says *which of the two empties this is* without naming anything: the
    caller learns about **their own** visibility, not about the existence of a use case.

    It exists because of what the live round showed. Membership reaches the gateway from Keycloak
    **groups** (`FRD-102`), and a use case created in the console does not create one — so its
    administrator opens the trace view and reads "no requests match" while the traffic is right
    there. An empty state that states the wrong reason is worse than one that states none: the
    reader concludes the recording is broken, and then distrusts every figure on the page.
    """
    return {"traces": [], "next_cursor": None, "scope": "use_cases", "in_scope": False}


@router.get("/v1beta/traces")
async def traces(
    request: Request,
    principal: Principal = Depends(require_principal),
    use_case: str = Query("", max_length=64),
    outcome: str = Query("", max_length=32),
    refusals_only: bool = Query(False),
    # The three questions an incident starts with: which system, which machine, whose identity.
    # `credential` is an API key **prefix** — the public half, already stored unhashed — and it is
    # what identifies a calling *system* rather than a person (`FRD-122`).
    credential: str = Query("", max_length=64),
    source_ip: str = Query("", max_length=64),
    subject: str = Query("", max_length=255),
    #: "Only my own requests." Offered to every role including the ones that see everything: an
    #: administrator checking what *they* did should not have to read past everybody else, and a
    #: view narrowable only by somebody else's identity is a view nobody uses on themselves.
    mine: bool = Query(False),
    #: Only requests where the model asked for a function. The fastest way to the rows that matter
    #: when the question is "what has this agent been trying to do".
    tools_only: bool = Query(False),
    flagged_only: bool = Query(False),
    limit: int = Query(TRACE_PAGE, ge=1, le=MAX_TRACE_PAGE),
    cursor: str = Query("", max_length=128),
) -> JSONResponse:
    """What actually happened, request by request (`FRD-502`).

    **Metadata only.** Not the prompt, not the response, not a snippet of either — see `FRD-502` §2
    for why this is not the per-request browsing `ADR-0009` deferred until `FRD-406`: that
    reasoning is about showing *stored prompts* to *non-members*, and this is neither.

    Scoped by the same `visible_scope` the report uses. A member sees their use case, an oversight
    role sees every one, and a caller with neither gets an **empty list rather than a refusal** —
    "there is nothing here" and "you may not look" are different answers.
    """
    scope = visible_scope(principal)
    fields = TRACE_FIELDS + (INCIDENT_FIELDS if principal.may_act_on_incidents else ())
    stmt = select(*(getattr(RequestLog, field) for field in fields))

    if scope is not None:
        allowed = list(scope)
        if not allowed:
            return JSONResponse(_out_of_scope())
        stmt = stmt.where(RequestLog.use_case.in_(allowed))
    if use_case:
        if scope is not None and use_case not in scope:
            # Asking about somebody else's use case is answered as emptiness, not as a refusal:
            # a 403 here would confirm the use case exists to somebody who may not see it.
            return JSONResponse(_out_of_scope())
        stmt = stmt.where(RequestLog.use_case == use_case)
    if outcome:
        stmt = stmt.where(RequestLog.outcome == outcome)
    if refusals_only:
        stmt = stmt.where(RequestLog.outcome != Outcome.SERVED.value)
    if credential:
        stmt = stmt.where(RequestLog.credential == credential)
    if subject:
        stmt = stmt.where(RequestLog.subject == subject)
    if source_ip:
        if not principal.may_act_on_incidents:
            # Refused rather than ignored. A filter that silently does nothing lets somebody
            # conclude an address made no requests, which is the opposite of what they were told.
            raise GeminiHTTPError(
                403,
                "Filtering by source address is available to IT Security and Global "
                "Administrators.",
                "PERMISSION_DENIED",
            )
        stmt = stmt.where(RequestLog.source_ip == source_ip)
    if mine:
        stmt = stmt.where(RequestLog.subject == principal.subject)
    if tools_only:
        stmt = stmt.where(RequestLog.tool_calls.is_not(None))
    if flagged_only:
        stmt = stmt.where(RequestLog.flagged.is_(True))

    # A use case may show its **users** their own requests only (`FRD-505` FR-4). Applied to the
    # list and not only to the payload: leaving the rows visible would still disclose who else
    # calls, how often and at what cost — the interesting half of what is being withheld — while
    # the content refusal beside it would read as a broken screen rather than a boundary.
    sessionmaker = sessionmaker_of(request)
    async with sessionmaker() as session:
        restricted = await restricted_use_cases(session, principal)
    if restricted:
        stmt = stmt.where(
            or_(RequestLog.use_case.notin_(restricted), RequestLog.subject == principal.subject)
        )

    if cursor:
        at, row_id = _parse_cursor(cursor)
        # Written out rather than as a row comparison: SQLite has no tuple comparison, and the
        # hermetic tests run on SQLite while production runs on Postgres. Paging exercised against
        # only one of the two is paging tested on one of the two.
        stmt = stmt.where(
            or_(
                RequestLog.created_at < at,
                and_(RequestLog.created_at == at, RequestLog.id < row_id),
            )
        )

    stmt = stmt.order_by(RequestLog.created_at.desc(), RequestLog.id.desc()).limit(limit + 1)

    async with sessionmaker() as session:
        rows = list((await session.execute(stmt)).mappings().all())

    has_more = len(rows) > limit
    rows = rows[:limit]
    traces = [
        {
            field: (value.isoformat() if isinstance(value, datetime) else value)
            for field, value in row.items()
        }
        for row in rows
    ]
    next_cursor = (
        f"{rows[-1]['created_at'].isoformat()}|{rows[-1]['id']}" if has_more and rows else None
    )
    return JSONResponse(
        {
            "traces": traces,
            "next_cursor": next_cursor,
            "scope": "all" if scope is None else "use_cases",
            "in_scope": True,
        }
    )


@router.get("/v1beta/traces/{trace_row_id}/payload")
async def trace_payload(
    request: Request,
    trace_row_id: str,
    principal: Principal = Depends(require_principal),
) -> Response:
    """The prompt and the answer, for a reader entitled to them — and a record that they looked.

    The authorization is `payloads.may_read_payload`, deliberately in its own module: it is the
    one place four roles, a per-use-case switch, a storage switch and a retention clock meet, and
    a decision spread over an endpoint is a decision the next endpoint restates differently.

    The access row is written **before** the content is returned. If recording the read fails, the
    read does not happen — the record is the condition on which `ADR-0009` was reopened, not a log
    line beside it.
    """
    scope = visible_scope(principal)
    sessionmaker = sessionmaker_of(request)
    async with sessionmaker() as session:
        row = (
            await session.execute(select(RequestLog).where(RequestLog.id == trace_row_id))
        ).scalar_one_or_none()
        if row is None or (scope is not None and row.use_case not in scope):
            # One answer for "no such row" and "not yours": distinguishing them would confirm that
            # a request exists to somebody who may not see it.
            raise GeminiHTTPError(404, "No such request.", "NOT_FOUND")

        verdict = await may_read_payload(session, principal, row)
        if not verdict.allowed:
            if verdict.is_authority_refusal:
                raise GeminiHTTPError(403, verdict.message, "PERMISSION_DENIED")
            # Absent, not forbidden. A 200 saying *which* absence, because "not stored", "expired"
            # and "never had one" are three different facts about the installation and a single
            # message for all three teaches the reader to distrust the screen.
            return JSONResponse(
                {
                    "id": row.id,
                    "available": False,
                    "reason": verdict.refusal,
                    "message": verdict.message,
                }
            )

        session.add(
            PayloadAccess(
                request_log_id=row.id,
                use_case=row.use_case or "",
                subject=principal.subject,
                ground=verdict.ground,
            )
        )
        await session.commit()
        body = payload_body(row)

    _log.info(
        "payload_read",
        request_log_id=row.id,
        use_case=row.use_case,
        subject=principal.subject,
        ground=verdict.ground,
    )
    return JSONResponse({**body, "available": True, "ground": verdict.ground})
