"""FastAPI dependencies resolving the caller and the use case a request is attributed to.

Authentication (`FRD-101`) accepts an API key or an OIDC bearer; attribution (`FRD-102`) decides
which use case the request belongs to. Refusals here are Gemini-shaped; the KIRA surface applies
the same rules (`use_case_refusal`, `must_name_a_use_case`) with its own envelope.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from fastapi import Depends, Request

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.attempts import record_failed_authentication
from aira_gateway.auth.attribution import (
    USE_CASE_HEADER_NAME,
    USE_CASE_PATH_FORM,
    Attribution,
    attribute,
    is_valid_use_case,
    resolve_use_case,
)
from aira_gateway.auth.credentials import extract_token
from aira_gateway.auth.grants import GroupGrantResolver
from aira_gateway.auth.keys import is_aira_key
from aira_gateway.auth.oidc import OidcValidator
from aira_gateway.auth.principal import Principal
from aira_gateway.auth.service import ApiKeyService
from aira_gateway.state import sessionmaker_of

_DEMO_PRINCIPAL = Principal(subject="demo", method="demo")

#: Methods that cannot reach a model. A GET on these surfaces lists what is configured.
SPENDS_NOTHING = frozenset({"GET", "HEAD", "OPTIONS"})


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
        async with sessionmaker_of(request)() as session:
            return await ApiKeyService(session).verify(token)

    # Otherwise treat it as an OIDC bearer (JWT), if OIDC is configured.
    validator: OidcValidator | None = request.app.state.oidc_validator
    if validator is not None:
        # **Off the event loop** (`FRD-617` §3.4): validation may fetch the JWKS synchronously, and
        # a Keycloak that accepts connections and does not answer would stall every concurrent
        # request on the worker, API-key callers and `/readyz` included.
        principal = await asyncio.to_thread(validator.validate, token)
        return await _with_group_grants(request, principal) if principal else None
    return None


async def _with_group_grants(request: Request, principal: Principal) -> Principal:
    """Add the use cases this caller has been *granted* — by group or by name (`FRD-209` §2.1).

    The union of three routes: the `/use-cases/<slug>` convention the token resolves on its own,
    the group grants in the read-model, and the grants naming this person. Where the roles differ
    the stronger wins, so no decision depends on which row was read first.
    """
    resolver: GroupGrantResolver | None = getattr(request.app.state, "group_grants", None)
    if resolver is None:
        return principal
    if not principal.groups and not principal.username:
        # Nothing to look anything up *by* — a token with a username but no groups can still be
        # named by a grant.
        return principal
    granted = await resolver.use_cases(principal.groups, principal.username)
    if not granted:
        return principal
    merged = tuple(dict.fromkeys([*principal.use_cases, *granted]))
    # The roles travel with the slugs: a later reader cannot re-derive *as what* for a group grant,
    # which writes no member row (`payloads.grant_role_in`).
    return replace(principal, use_cases=merged, grants=tuple(sorted(granted.items())))


async def require_principal(request: Request) -> Principal:
    """Dependency: attach the Principal to ``request.state`` or raise a 401."""
    # Whether anything was offered at all, recorded before the verdict: KIRA separates
    # `NOT_AUTHENTICATED` from `INVALID_TOKEN`, and keeping the bit on the request keeps KIRA's
    # vocabulary out of the shared refusal type. Only meaningful when authentication is on.
    request.state.credential_presented = extract_token(request) is not None
    principal = await resolve_principal(request)
    if principal is None:
        # Before the 401: every `FRD-405` limit is keyed by a verified identity, so none of them
        # bounds a caller who has none.
        await record_failed_authentication(request)
        raise _unauthenticated("Missing or invalid credentials.")
    request.state.principal = principal
    return principal


def use_case_refusal(principal: Principal, use_case: str) -> str | None:
    """Why ``principal`` may not act on ``use_case``, or ``None`` if they may.

    **A selector never grants access; it only chooses among what you already have.** One rule for
    both surfaces, returning a reason rather than raising, because only the error envelope differs:

    - **OIDC** — must be a member. An empty membership list refuses everything.
    - **An unbound API key** — the CLI break-glass key, minted by an operator with database access
      for when the control plane is unavailable. Deliberately unrestricted (`ADR-0015`).
    - **A bound API key** — issued by Management for exactly one use case, and may touch only that.
    """
    if principal.method == "oidc" and use_case not in principal.use_cases:
        return f"Not a member of use case '{use_case}'."
    if principal.method == "api_key" and principal.use_cases and use_case != principal.use_cases[0]:
        return f"API key is bound to use case '{principal.use_cases[0]}'."
    return None


def authorize_use_case(principal: Principal, use_case: str) -> None:
    """Raise a Gemini-shaped 403 unless ``principal`` may act on ``use_case``."""
    reason = use_case_refusal(principal, use_case)
    if reason is not None:
        raise GeminiHTTPError(403, reason, "PERMISSION_DENIED")


def require_valid_use_case(use_case: str) -> str:
    """Return ``use_case`` if it is a syntactically valid slug, else raise a 400."""
    if not is_valid_use_case(use_case):
        raise GeminiHTTPError(400, "Invalid use case identifier.", "INVALID_ARGUMENT")
    return use_case


def must_name_a_use_case(request: Request, principal: Principal) -> bool:
    """Whether this caller has to name a use case, or may go unattributed (`FRD-102`, `ADR-0015`).

    One definition for both surfaces. Two exemptions:

    - **demo**, where authentication is off and there is no identity — bounded by `AIRA_DEMO_MODE`,
      which `security.py` refuses outside `local`;
    - the **unbound break-glass key** (`ADR-0015`), for when Management is what is broken. Its row
      still carries the key prefix and subject, so the request belongs to a revocable credential.

    Everybody else names one: an unattributed OIDC call charges no budget, applies no use-case rate
    limit and consults no model release (`FRD-308`).
    """
    if not request.app.state.settings.require_use_case:
        return False
    if request.method in SPENDS_NOTHING:
        # **A reading is not a model call.** The requirement attributes spend; a listing has none,
        # and a Global Administrator (a member of nothing) must be able to read the catalogue.
        return False
    if principal.method == "demo":
        return False
    return not (principal.method == "api_key" and not principal.use_cases)


async def require_attribution(
    request: Request, principal: Principal = Depends(require_principal)
) -> Attribution:
    """Resolve + authorize the use case and attach an Attribution to ``request.state``."""
    use_case = resolve_use_case(request)
    if use_case is not None:
        require_valid_use_case(use_case)

    # An API key issued by Management is bound to exactly one use case (`FRD-205`) and needs no
    # selector. Unbound keys (demo/CLI break-glass) fall through to the selector-based path.
    if principal.method == "api_key" and principal.use_cases and use_case is None:
        use_case = principal.use_cases[0]

    if use_case is None:
        if must_name_a_use_case(request, principal):
            raise GeminiHTTPError(
                400,
                f"Missing use case ({USE_CASE_HEADER_NAME} header or {USE_CASE_PATH_FORM} path).",
                "INVALID_ARGUMENT",
            )
    else:
        authorize_use_case(principal, use_case)

    attribution = Attribution(
        subject=principal.subject,
        method=principal.method,
        username=principal.username,
        use_case=use_case,
        credential=principal.credential,
        issuer=principal.issuer,
    )
    return attribute(request, attribution)
