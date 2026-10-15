"""What actually happened, request by request (`FRD-502`) — and one request's payload.

The list is **metadata only**. A prompt and its answer are read one request at a time, authorised
by `payloads.may_read_payload` and recorded before they are returned (`ADR-0009`).
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import structlog
from fastapi import Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aira_common.access import GrantRole
from aira_common.permissions import Permission
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.reporting.common import parse_cursor, router, scope_label, visible_scope
from aira_gateway.audit import Outcome
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.db.models import PayloadAccess, RequestLog
from aira_gateway.db.models.owned import ACCESS_CONTENT, ACCESS_METADATA
from aira_gateway.payloads import (
    grant_role_in,
    is_own_request,
    may_read_payload,
    own_requests,
    payload_body,
    restricted_use_cases,
    sees_people,
    withheld_row,
)
from aira_gateway.state import sessionmaker_of, settings_of

#: What a trace row exposes — an **allow-list**: a column added to `request_logs` must not appear
#: because nobody excluded it, and the two that must never appear (`request_payload`,
#: `response_payload`) are exactly what a forgotten exclusion would leak (`FRD-502` FR-11).
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
    "reasoning_tokens",  # of the completion, how much was thinking (`FRD-135`)
    "total_tokens",
    "latency_ms",
    "cost_nanos",
    "provider",
    "region",
    "trace_id",
    "subject",
    "credential",
    "use_case",
    "tool_calls",  # what the model asked to run: names and counts, never arguments (`FRD-131`)
    "flagged",  # a pipeline step blocked or flagged the request (`FRD-505` FR-5)
)

#: Fields only an incident role sees (`FRD-502` FR-12). The source address answers "which machine",
#: the first question of an incident and personal data the rest of the time.
INCIDENT_FIELDS = ("source_ip",)

#: Rows per page by default — enough to fill a screen twice, cheap to refresh — and the most one
#: page may hold.
TRACE_PAGE = 50
MAX_TRACE_PAGE = 200

_log = structlog.get_logger(__name__)


def _out_of_scope() -> dict[str, object]:
    """The answer when the caller's scope does not cover what was asked for.

    An empty 200 rather than a 403, which would confirm the use case exists. ``in_scope: false``
    tells the caller which empty this is — about their own visibility, not the use case: membership
    comes from Keycloak groups (`FRD-102`), and a use case created in the console has none yet.
    """
    return {"traces": [], "next_cursor": None, "scope": "use_cases", "in_scope": False}


#: The label the row's name is read under, so it can never collide with a listed field.
_OWNER_NAME = "_owner_name"


def _listed(row: dict[str, Any], principal: Principal, *, withholding: bool) -> dict[str, Any]:
    """One row on the wire: the allow-listed fields, and — when the caller does not see people —
    another person's row without its name and key (`FRD-627` FR-7)."""
    name = row.pop(_OWNER_NAME)
    withheld = withholding and withheld_row(
        principal, SimpleNamespace(subject=row.get("subject"), username=name)
    )
    if withheld:
        row["subject"] = None
        row["credential"] = None
    row["person_withheld"] = withheld
    return {
        field: (value.isoformat() if isinstance(value, datetime) else value)
        for field, value in row.items()
    }


@router.get("/v1beta/traces")
async def traces(
    request: Request,
    principal: Principal = Depends(require_principal),
    use_case: str = Query("", max_length=64),
    outcome: str = Query("", max_length=32),
    refusals_only: bool = Query(False),
    # An incident's first questions: which system (`credential`, an API key's public prefix,
    # `FRD-122`), which machine, whose identity.
    credential: str = Query("", max_length=64),
    source_ip: str = Query("", max_length=64),
    subject: str = Query("", max_length=255),
    # Only the caller's own requests — offered to every role, including those that see everything.
    mine: bool = Query(False),
    # Only requests where the model asked for a function.
    tools_only: bool = Query(False),
    flagged_only: bool = Query(False),
    # One request by its id, which is where the content-read log links to (`FRD-622` FR-6). Bounded
    # by the same scope and restriction as every other filter, so an id is no way around them.
    request_id: str = Query("", max_length=36),
    limit: int = Query(TRACE_PAGE, ge=1, le=MAX_TRACE_PAGE),
    cursor: str = Query("", max_length=128),
) -> JSONResponse:
    """Request metadata, newest first (`FRD-502`).

    Not the prompt, not the response, not a snippet of either (§2). An oversight role sees every use
    case, a member their own, and anybody else an empty list rather than a refusal.
    """
    scope = visible_scope(principal, Permission.TRACE_READ_ALL)
    investigating = principal.allows(Permission.INCIDENT_INVESTIGATE)
    strict_data = settings_of(request).strict_data_mode
    # Under strict data mode another person's row is listed without its person (`FRD-627` FR-7).
    withholding = strict_data and not sees_people(principal)
    fields = _fields(investigating)
    # The name is read to decide whose row it is, and never sent: `TRACE_FIELDS` is the allow-list.
    stmt = select(
        *(getattr(RequestLog, field) for field in fields),
        RequestLog.username.label(_OWNER_NAME),
    )

    if source_ip and not investigating:
        # Refused rather than ignored, and before any early answer: a filter that silently does
        # nothing lets somebody conclude an address made no requests.
        raise GeminiHTTPError(
            403,
            "Filtering by source address needs the permission to investigate "
            "(incident.investigate).",
            "PERMISSION_DENIED",
        )
    if withholding and (subject or credential):
        # A filter by person or key would name, by its answer, whose rows these are.
        raise GeminiHTTPError(
            403,
            "This installation runs strict data mode: requests by person or by API key are "
            "visible to Global Administrators and IT Security only. 'Only my requests' shows "
            "your own.",
            "PERMISSION_DENIED",
        )
    if scope is not None:
        allowed = list(scope)
        if not allowed:
            return JSONResponse(_out_of_scope())
        stmt = stmt.where(RequestLog.use_case.in_(allowed))
    if use_case:
        if scope is not None and use_case not in scope:
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
        stmt = stmt.where(RequestLog.source_ip == source_ip)
    if mine:
        # `own_requests` is the one definition of "my requests", shared with the restriction below.
        stmt = stmt.where(own_requests(principal))
    if tools_only:
        stmt = stmt.where(RequestLog.tool_calls.is_not(None))
    if flagged_only:
        stmt = stmt.where(RequestLog.flagged.is_(True))
    if request_id:
        stmt = stmt.where(RequestLog.id == request_id)

    # A use case may show its users only their own requests (`FRD-505` FR-4) — applied to the list
    # too, since the rows alone disclose who else calls, how often and at what cost. By person, not
    # credential (`own_requests`): the console is OIDC and the traffic usually an API key.
    sessionmaker = sessionmaker_of(request)
    async with sessionmaker() as session:
        restricted = await restricted_use_cases(session, principal, strict_data=strict_data)
    if restricted:
        stmt = stmt.where(
            or_(
                # In no use case, so in none of the restricted ones: said, because SQL reads
                # `NULL NOT IN (…)` as unknown and would drop every unattributed row.
                RequestLog.use_case.is_(None),
                RequestLog.use_case.notin_(restricted),
                own_requests(principal),
            )
        )

    if cursor:
        at, row_id = parse_cursor(cursor)
        # Written out: SQLite, which the hermetic tests run on, has no tuple comparison.
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
    next_cursor = (
        f"{rows[-1]['created_at'].isoformat()}|{rows[-1]['id']}" if has_more and rows else None
    )
    return JSONResponse(
        {
            "traces": [_listed(dict(row), principal, withholding=withholding) for row in rows],
            "next_cursor": next_cursor,
            "scope": scope_label(scope),
            "in_scope": True,
        }
    )


@router.get("/v1beta/traces/{trace_row_id}")
async def trace_detail(
    request: Request,
    trace_row_id: str,
    principal: Principal = Depends(require_principal),
) -> JSONResponse:
    """One request's details — and a record that this reader opened them (`FRD-627` FR-16).

    The list says that a request happened; its details — model, tokens, cost, latency, trace — are
    what a reader opens to look at one person's request, so the opening is recorded as reading the
    content is, in the same table under `kind = metadata`. Scoped exactly as the list is, and the
    record is committed **before** the details are returned: a look that cannot be recorded does
    not happen.
    """
    scope = visible_scope(principal, Permission.TRACE_READ_ALL)
    strict_data = settings_of(request).strict_data_mode
    withholding = strict_data and not sees_people(principal)
    fields = _fields(principal.allows(Permission.INCIDENT_INVESTIGATE))
    async with sessionmaker_of(request)() as session:
        row = (
            await session.execute(select(RequestLog).where(RequestLog.id == trace_row_id))
        ).scalar_one_or_none()
        restricted = await restricted_use_cases(session, principal, strict_data=strict_data)
        hidden = (
            row is None
            or (scope is not None and row.use_case not in scope)
            or (row.use_case in restricted and not is_own_request(principal, row))
        )
        if hidden or row is None:
            # One answer for "no such row" and "not yours", as the content's route gives.
            raise GeminiHTTPError(404, "No such request.", "NOT_FOUND")
        ground = await _ground_for_details(session, principal, row)
        session.add(_access(principal, row, ground=ground, kind=ACCESS_METADATA))
        await session.commit()
        listed = {field: getattr(row, field) for field in fields}
        listed[_OWNER_NAME] = row.username
    return JSONResponse(
        {"trace": _listed(listed, principal, withholding=withholding), "recorded": True}
    )


async def _ground_for_details(session: AsyncSession, principal: Principal, row: RequestLog) -> str:
    """On what authority a reader opened a request's details — the words the content's record uses,
    and `oversight` for a role that reads every use case's requests without a grant on this one."""
    if principal.allows(Permission.INCIDENT_INVESTIGATE):
        return "incident"
    role = await grant_role_in(session, principal, row.use_case) if row.use_case else None
    if role == GrantRole.ADMIN.value:
        return "use_case_admin"
    if role is not None:
        return "use_case_member"
    return "oversight"


@router.get("/v1beta/traces/{trace_row_id}/payload")
async def trace_payload(
    request: Request,
    trace_row_id: str,
    principal: Principal = Depends(require_principal),
) -> Response:
    """The prompt and the answer, for a reader entitled to them — and a record that they looked.

    Authorised by `payloads.may_read_payload`, the one place the roles, the use case's switch, the
    storage switch and the retention clock meet. The access row is committed **before** the content
    is returned: if recording the read fails, the read does not happen (`ADR-0009`).
    """
    # Reading any use case's content includes reaching the request it belongs to (`FRD-614`
    # FR-11); the list of requests stays with `trace.read_all`.
    reach_any = principal.allows(Permission.PAYLOAD_READ_ANY)
    scope = None if reach_any else visible_scope(principal, Permission.TRACE_READ_ALL)
    async with sessionmaker_of(request)() as session:
        row = (
            await session.execute(select(RequestLog).where(RequestLog.id == trace_row_id))
        ).scalar_one_or_none()
        if row is None or (scope is not None and row.use_case not in scope):
            # One answer for "no such row" and "not yours", so nothing confirms a request exists.
            raise GeminiHTTPError(404, "No such request.", "NOT_FOUND")

        verdict = await may_read_payload(
            session, principal, row, strict_data=settings_of(request).strict_data_mode
        )
        if not verdict.allowed:
            if verdict.is_authority_refusal:
                raise GeminiHTTPError(403, verdict.message, "PERMISSION_DENIED")
            # Absent, not forbidden: a 200 naming which absence — not stored, expired, or none.
            return JSONResponse(
                {
                    "id": row.id,
                    "available": False,
                    "reason": verdict.refusal,
                    "message": verdict.message,
                }
            )

        session.add(_access(principal, row, ground=verdict.ground, kind=ACCESS_CONTENT))
        await session.commit()
        body = payload_body(row)

    _log.info(
        "payload_read",
        request_log_id=row.id,
        use_case=row.use_case,
        subject=principal.subject,
        person=principal.person,
        method=principal.method,
        credential=principal.credential,
        ground=verdict.ground,
    )
    return JSONResponse({**body, "available": True, "ground": verdict.ground})


def _fields(investigating: bool) -> tuple[str, ...]:
    """What a row carries to this reader: the allow-list, and the address to an incident role."""
    return TRACE_FIELDS + (INCIDENT_FIELDS if investigating else ())


def _access(principal: Principal, row: RequestLog, *, ground: str, kind: str) -> PayloadAccess:
    """The record of one look at one request — its content or its details (`FRD-627` FR-16)."""
    return PayloadAccess(
        request_log_id=row.id,
        use_case=row.use_case or "",
        subject=principal.subject,
        # The name too, so a read joins to the person rather than a credential (`FRD-613`).
        username=principal.username,
        ground=ground,
        # The roles held at this moment (`FRD-622` FR-3): `incident` alone does not say which role
        # read it, and today's roles are not evidence of the ones held then.
        roles=",".join(sorted(principal.roles)),
        # What read it: a key reads on its owner's grant, and its reads must not be filed as the
        # owner's own (`FRD-622` FR-6).
        auth_method=principal.method,
        credential=principal.credential,
        kind=kind,
    )
