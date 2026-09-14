"""Who read which stored content, and on what authority (`FRD-622`).

`reporting.traces.trace_payload` writes a row before it hands content over (`FRD-505` FR-6); this
reads the rows back. Metadata only, so every platform role may read it, IT Steuerung included, which
reads no content. Bounded by role rather than by use case: the log crosses every use case by design.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import ColumnElement, and_, func, or_, select

from aira_common.permissions import Permission
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.incidents.common import router
from aira_gateway.api.reporting.common import parse_cursor
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.db.models import PayloadAccess
from aira_gateway.state import sessionmaker_of

#: What a row exposes — an allow-list, so a column added to `payload_access` does not appear because
#: nobody excluded it.
READ_FIELDS = (
    "id",
    "created_at",
    "request_log_id",
    "use_case",
    "subject",
    "username",
    "ground",
    "roles",
)

#: Rows per page by default, and the most one page may hold.
READ_PAGE = 50
MAX_READ_PAGE = 200


def _row(row: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in READ_FIELDS:
        value = row[field]
        if field == "roles":
            # NULL is a read recorded before roles were kept: unknown, which is not "none".
            value = None if value is None else [role for role in value.split(",") if role]
        elif isinstance(value, datetime):
            value = value.isoformat()
        out[field] = value
    return out


@router.get("/v1beta/content-reads")
async def content_reads(
    request: Request,
    principal: Principal = Depends(require_principal),
    use_case: str = Query("", max_length=64),
    reader: str = Query("", max_length=255),
    limit: int = Query(READ_PAGE, ge=1, le=MAX_READ_PAGE),
    cursor: str = Query("", max_length=128),
) -> JSONResponse:
    """Content reads across every use case, newest first (`FRD-622` FR-4).

    For the platform roles only: the log says who looked at whose requests, which a member of one
    use case is not entitled to know.
    """
    if not (principal.allows(Permission.CONTENT_READ_READ) or principal.method == "demo"):
        raise GeminiHTTPError(
            403,
            "The content-read log needs the permission to read it (content_read.read).",
            "PERMISSION_DENIED",
        )

    # The filters without the cursor, so the total counts every page rather than what is left.
    conditions: list[ColumnElement[bool]] = []
    if use_case:
        conditions.append(PayloadAccess.use_case == use_case)
    if reader:
        conditions.append(or_(PayloadAccess.username == reader, PayloadAccess.subject == reader))
    stmt = select(*(getattr(PayloadAccess, field) for field in READ_FIELDS)).where(*conditions)
    if cursor:
        at, row_id = parse_cursor(cursor)
        # Written out: SQLite, which the hermetic tests run on, has no tuple comparison.
        stmt = stmt.where(
            or_(
                PayloadAccess.created_at < at,
                and_(PayloadAccess.created_at == at, PayloadAccess.id < row_id),
            )
        )
    stmt = stmt.order_by(PayloadAccess.created_at.desc(), PayloadAccess.id.desc()).limit(limit + 1)

    async with sessionmaker_of(request)() as session:
        rows = list((await session.execute(stmt)).mappings().all())
        total = (
            await session.execute(
                select(func.count()).select_from(PayloadAccess).where(*conditions)
            )
        ).scalar_one()

    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        f"{rows[-1]['created_at'].isoformat()}|{rows[-1]['id']}" if has_more and rows else None
    )
    # One page and the total, never the log: a reader pages through it at the server.
    return JSONResponse(
        {"reads": [_row(row) for row in rows], "next_cursor": next_cursor, "count": total}
    )
