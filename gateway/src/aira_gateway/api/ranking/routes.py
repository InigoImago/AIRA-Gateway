"""``POST /v1/projects/{project}/locations/{location}/rankingConfigs/{config}:rank`` (`FRD-147`).

Google's own ranking path, so Google's client library works against this gateway with only its
endpoint changed. Ranking is not a Gemini method, so it is not under `/v1beta/models`.

**The path selects nothing here.** Google's client puts its own project, location and ranking
configuration in it; this gateway calls Google in **its** project and at the locations the catalogue
gives the model. The use case selects, as on every route, and the body's ``model`` the catalogue
entry. Everything after parsing is the shared sequence (`api.serving`).
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.gemini.refusals import refusal_response
from aira_gateway.api.ranking import schemas
from aira_gateway.api.serving import (
    REFUSALS,
    accounting,
    ensure_body_is_encodable,
    json_body,
    prepare_for_dispatch,
    rank,
    record_refusal,
)
from aira_gateway.audit import AuditTrail
from aira_gateway.core.canonical import CanonicalRerankRequest, RerankRecord

router = APIRouter(tags=["ranking"])


@router.post("/v1/projects/{project}/locations/{location}/rankingConfigs/{config}:rank")
async def rank_records(project: str, location: str, config: str, request: Request) -> Response:
    """Rank, and record the request whether or not it was served."""
    del project, location, config  # accepted and not used: see the module docstring
    trail = AuditTrail(operation="rerank", api="gemini")
    started = time.monotonic()
    try:
        body = await _read_body(request)
        trail.body = body
        parsed = _validated(body)
        trail.requested_model = parsed.model
        prepared = await prepare_for_dispatch(
            request, trail, method="rerank", rerank=_canonical(parsed)
        )
        assert prepared.rerank is not None
        async with accounting(
            request, trail, prepared, api="gemini", operation="rerank", started=started
        ) as acct:
            ranked = await rank(request, prepared)
            payload = schemas.RankResponse(
                records=[
                    schemas.RankedRecordOut(
                        id=record.id,
                        score=record.score,
                        title=None if parsed.ignoreRecordDetailsInResponse else record.title,
                        content=None if parsed.ignoreRecordDetailsInResponse else record.content,
                    )
                    for record in ranked
                ]
            ).model_dump(exclude_none=True)
            acct.ranked(parsed.model, units=prepared.rerank.units, records=ranked)
        return JSONResponse(payload)
    except REFUSALS as exc:
        response = refusal_response(exc, request)
        await record_refusal(request, trail, exc, status=response.status_code, started=started)
        return response


def _canonical(parsed: schemas.RankRequest) -> CanonicalRerankRequest:
    return CanonicalRerankRequest(
        model=parsed.model,
        query=parsed.query,
        records=[
            RerankRecord(id=record.id, title=record.title, content=record.content)
            for record in parsed.records
        ],
        top_n=parsed.topN,
        ids_only=parsed.ignoreRecordDetailsInResponse,
    )


async def _read_body(request: Request) -> dict[str, Any]:
    try:
        body = await json_body(request)
        ensure_body_is_encodable(body)
    except ValueError:
        raise GeminiHTTPError(400, "Request body is not valid JSON.", "INVALID_ARGUMENT") from None
    if not isinstance(body, dict):
        raise GeminiHTTPError(400, "Send one JSON object.", "INVALID_ARGUMENT")
    return body


def _validated(body: dict[str, Any]) -> schemas.RankRequest:
    try:
        return schemas.RankRequest.model_validate(body)
    except ValidationError as exc:
        first = exc.errors(include_url=False, include_context=False, include_input=False)[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        raise GeminiHTTPError(
            400, f"{location}: {first.get('msg', 'invalid')}".strip(": "), "INVALID_ARGUMENT"
        ) from exc
