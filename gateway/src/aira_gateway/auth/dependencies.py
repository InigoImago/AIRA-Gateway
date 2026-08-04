"""FastAPI auth dependency resolving a Principal for protected routes (FRD-101).

Slice A handles API keys; OIDC bearer validation is added in Slice B and plugs into the
same resolver. On failure a Gemini-shaped 401 is raised.
"""

from __future__ import annotations

from fastapi import Depends, Request

from aira_common.observability import set_span_attributes
from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.attribution import Attribution, resolve_use_case
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


async def require_attribution(
    request: Request, principal: Principal = Depends(require_principal)
) -> Attribution:
    """Resolve + authorize the use case and attach an Attribution to ``request.state``."""
    use_case = resolve_use_case(request)

    if use_case is None:
        if request.app.state.settings.require_use_case and principal.method != "demo":
            raise GeminiHTTPError(
                400,
                "Missing use case (X-AIRA-Use-Case header or /uc/<use-case> path).",
                "INVALID_ARGUMENT",
            )
    elif principal.method == "oidc" and use_case not in principal.use_cases:
        raise GeminiHTTPError(403, f"Not a member of use case '{use_case}'.", "PERMISSION_DENIED")

    attribution = Attribution(subject=principal.subject, method=principal.method, use_case=use_case)
    request.state.attribution = attribution
    set_span_attributes(
        {
            "aira.subject": attribution.subject,
            "aira.auth_method": attribution.method,
            "aira.use_case": attribution.use_case,
        }
    )
    return attribution
