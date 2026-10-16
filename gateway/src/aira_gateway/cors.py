"""Cross-origin access: an explicit allow-list, empty by default (`FRD-117` §5.4).

Empty because the SPA is served from the same origin through the proxy, so a deployment that needs
cross-origin access is making a deliberate choice.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
from starlette.types import Message, Receive, Scope, Send

from aira_gateway.api.kira.headers import UNMODELLED_HEADER
from aira_gateway.config import GatewaySettings
from aira_gateway.explaining import (
    MODEL_HEADER,
    PIPELINE_HEADER,
    REASON_HEADER,
    REQUEST_ID_HEADER,
)

#: What a cross-origin caller may send, and which response headers it may read.
ALLOWED_METHODS = ("GET", "POST", "OPTIONS")
ALLOWED_HEADERS = ("authorization", "content-type", "x-goog-api-key", "x-aira-use-case")
#: The `X-AIRA-*` headers that say what happened (`FRD-628`) are among them: a browser client that
#: cannot read them is told nothing the others are.
EXPOSED_HEADERS = (
    "x-trace-id",
    "retry-after",
    "deprecation",
    "sunset",
    "warning",
    REQUEST_ID_HEADER,
    REASON_HEADER,
    MODEL_HEADER,
    PIPELINE_HEADER,
    UNMODELLED_HEADER.lower(),
)


class CorsMisconfigured(Exception):
    """A CORS policy that would disable the protection it looks like it is providing."""


async def _no_app(_scope: Scope, _receive: Receive, _send: Send) -> None:
    """The policy kept for `apply_cors` decides headers only; it never forwards a request."""


def configure_cors(app: FastAPI, settings: GatewaySettings) -> None:
    """Mount `CORSMiddleware` for the configured origins, if any.

    ``*`` **with** credentials is refused at startup: browsers reject the combination, and a server
    that implements it by reflecting the origin lets any site a user visits call this API with
    their credentials. At startup, because a misconfiguration only a browser shows is one that
    ships.
    """
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    if not origins:
        return
    if "*" in origins and settings.cors_allow_credentials:
        raise CorsMisconfigured(
            "AIRA_CORS_ORIGINS='*' together with credentials would let any site call this API "
            "with a user's credentials. Name the origins instead."
        )
    policy: dict[str, Any] = {
        "allow_origins": origins,
        "allow_credentials": settings.cors_allow_credentials,
        "allow_methods": ALLOWED_METHODS,
        "allow_headers": ALLOWED_HEADERS,
        "expose_headers": EXPOSED_HEADERS,
    }
    app.add_middleware(CORSMiddleware, **policy)
    # The same policy once more, for the one response the middleware never sees (`apply_cors`).
    app.state.cors = CORSMiddleware(_no_app, **policy)


async def apply_cors(request: Request, response: Response) -> None:
    """Give ``response`` the headers the CORS middleware gives every other response.

    For Starlette's answer to an unhandled error, which is written outside every middleware: without
    them a cross-origin console sees a network error where the status and message are. Decided by
    the middleware's own code (`CORSMiddleware.send`), so the rule is not restated here.
    """
    policy: CORSMiddleware | None = getattr(request.app.state, "cors", None)
    if policy is None or "origin" not in request.headers:
        return
    start: Message = {
        "type": "http.response.start",
        "status": response.status_code,
        "headers": list(response.raw_headers),
    }

    async def _keep(_message: Message) -> None:
        return None

    await policy.send(start, _keep, request.headers)
    response.raw_headers = start["headers"]
