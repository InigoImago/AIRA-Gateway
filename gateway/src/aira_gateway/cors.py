"""Cross-origin access: an explicit allow-list, empty by default (`FRD-117` §5.4).

Empty because the SPA is served from the same origin through the proxy, so a deployment that needs
cross-origin access is making a deliberate choice.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from aira_gateway.config import GatewaySettings

#: What a cross-origin caller may send, and which response headers it may read.
ALLOWED_METHODS = ("GET", "POST", "OPTIONS")
ALLOWED_HEADERS = ("authorization", "content-type", "x-goog-api-key", "x-aira-use-case")
EXPOSED_HEADERS = ("x-trace-id", "retry-after", "deprecation", "sunset", "warning")


class CorsMisconfigured(Exception):
    """A CORS policy that would disable the protection it looks like it is providing."""


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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
        expose_headers=EXPOSED_HEADERS,
    )
