"""What every incident endpoint shares: the router, and reading the caller's body."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.api.serving import json_body

#: One router for the package. Each module registers on it rather than on a router of its own,
#: because an included router is nested and would hide its routes from `router.routes`.
router = APIRouter(tags=["incidents"])


async def _body_of(request: Request, *, optional: bool = False) -> dict[str, Any]:
    """The JSON object a caller sent, or a **400** naming what is wrong with it — never a 500.

    ``optional`` lets an empty body through as ``{}``, for an endpoint that says itself what is
    missing.
    """
    raw = await request.body()
    if not raw and optional:
        return {}
    try:
        body = await json_body(request)
    except ValueError as exc:
        raise GeminiHTTPError(400, "Request body is not valid JSON.", "INVALID_ARGUMENT") from exc
    if not isinstance(body, dict):
        raise GeminiHTTPError(400, "Send one JSON object.", "INVALID_ARGUMENT")
    return body
