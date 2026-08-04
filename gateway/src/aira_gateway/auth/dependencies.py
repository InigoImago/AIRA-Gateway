"""FastAPI auth dependency resolving a Principal for protected routes (FRD-101).

Slice A handles API keys; OIDC bearer validation is added in Slice B and plugs into the
same resolver. On failure a Gemini-shaped 401 is raised.
"""

from __future__ import annotations

from fastapi import Request

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.credentials import extract_token
from aira_gateway.auth.keys import is_aira_key
from aira_gateway.auth.oidc import OidcValidator
from aira_gateway.auth.principal import Principal
from aira_gateway.auth.service import ApiKeyService

_DEMO_PRINCIPAL = Principal(subject="demo", method="demo")


def _unauthenticated(message: str) -> GeminiHTTPError:
    return GeminiHTTPError(401, message, "UNAUTHENTICATED")


async def resolve_principal(request: Request) -> Principal | None:
    """Resolve the caller to a Principal, or None if the credential is invalid/absent."""
    if not request.app.state.settings.auth_required:
        return _DEMO_PRINCIPAL

    token = extract_token(request)
    if token is None:
        return None

    if is_aira_key(token):
        sessionmaker = request.app.state.db_sessionmaker
        async with sessionmaker() as session:
            return await ApiKeyService(session).verify(token)

    # Otherwise treat it as an OIDC bearer (JWT), if OIDC is configured.
    validator: OidcValidator | None = request.app.state.oidc_validator
    if validator is not None:
        return validator.validate(token)
    return None


async def require_principal(request: Request) -> Principal:
    """Dependency: attach the Principal to ``request.state`` or raise a 401."""
    principal = await resolve_principal(request)
    if principal is None:
        raise _unauthenticated("Missing or invalid credentials.")
    request.state.principal = principal
    return principal
