"""FastAPI auth dependency resolving a Principal for protected routes (FRD-101).

Slice A handles API keys; OIDC bearer validation is added in Slice B and plugs into the
same resolver. On failure a Gemini-shaped 401 is raised.
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
        # **Off the event loop** (`FRD-617` §3.4). `validate` verifies an RS256 signature and, on a
        # cold start or after a key rotation, fetches the JWKS — and `PyJWKClient` fetches with
        # `urllib`, synchronously. Called directly from this coroutine, a Keycloak that accepts
        # connections and does not answer stalled every concurrent request on the worker for the
        # length of that fetch, not just this one: `/readyz` included, and requests authenticating
        # with an API key included. A bounded timeout alone would only have shortened the stall.
        principal = await asyncio.to_thread(validator.validate, token)
        return await _with_group_grants(request, principal) if principal else None
    return None


async def _with_group_grants(request: Request, principal: Principal) -> Principal:
    """Add the use cases this caller has been *granted* — by group or by name (`FRD-209` §2.1).

    The union of three routes: the `/use-cases/<slug>` convention the token resolves on its own,
    the group grants in the read-model, and the grants naming this person. A caller who is a member
    twice over is a member; where the roles differ the stronger wins, because an access decision
    that depends on which row was read first is not a decision anybody can review.

    **The early return used to say `not principal.groups`**, and that was the whole defect for a
    person granted access by name: somebody in no relevant Keycloak group left before the lookup
    that would have found their membership. Reported as a use-case administrator being refused a
    dry run on a use case the console listed them as an administrator of — Management counted the
    row, this side never read it.
    """
    resolver: GroupGrantResolver | None = getattr(request.app.state, "group_grants", None)
    if resolver is None:
        return principal
    if not principal.groups and not principal.username:
        # Nothing to look anything up *by*. Not the same as "no groups": a token with a username
        # can still be named by a grant.
        return principal
    granted = await resolver.use_cases(principal.groups, principal.username)
    if not granted:
        return principal
    merged = tuple(dict.fromkeys([*principal.use_cases, *granted]))
    # **The roles travel with the slugs.** This took `granted.keys()` and dropped the values, so
    # the one thing the resolver works out that a later reader cannot — *as what* — was computed,
    # tested (`test_the_granted_role_is_carried_through`) and thrown away here. `payloads` then
    # asked `use_case_members`, where a **group** grant writes no row, and answered "user" for an
    # administrator. Carried rather than re-derived, because re-deriving it is what produced two
    # answers to one question in the first place.
    return replace(principal, use_cases=merged, grants=tuple(sorted(granted.items())))


async def require_principal(request: Request) -> Principal:
    """Dependency: attach the Principal to ``request.state`` or raise a 401."""
    # **Whether anything was offered at all**, recorded before the verdict. The predecessor's
    # vocabulary separates `NOT_AUTHENTICATED` (nothing presented) from `INVALID_TOKEN` (presented
    # and rejected), and both refusals arrive here as one `GeminiHTTPError` — so the bit has to
    # travel somewhere. On the request rather than on the error, because the alternative is
    # Google's error type carrying KIRA's vocabulary, and a shared refusal type that knows about
    # one surface is a shared refusal type until the third surface arrives.
    #
    # Only meaningful when authentication is on: the demo path returns a principal without looking.
    request.state.credential_presented = extract_token(request) is not None
    principal = await resolve_principal(request)
    if principal is None:
        # Before the 401, not after: an address that keeps failing is asked to wait. Every limit
        # `FRD-405` built is keyed by a *verified* identity, so none of them could bound a caller
        # who has none.
        await record_failed_authentication(request)
        raise _unauthenticated("Missing or invalid credentials.")
    request.state.principal = principal
    return principal


def use_case_refusal(principal: Principal, use_case: str) -> str | None:
    """Why ``principal`` may not act on ``use_case``, or ``None`` if they may.

    **A selector never grants access; it only chooses among what you already have.** That sentence
    was written on the Gemini surface and implemented on one surface only — the KIRA surface asked
    `if memberships and header not in memberships`, so an *empty* membership list meant "anything
    goes" rather than "nothing". A caller who belonged to no use case at all could name somebody
    else's, get a real answer, and have the tokens billed to that use case's budget and written
    into its audit trail. Proven against the running stack before this was written.

    So the rule lives here, once, and returns a *reason* rather than raising: the two surfaces owe
    their callers different error envelopes, and that is the only thing that should differ.

    Three cases, and the middle one is the deliberate exception:

    - **OIDC** — must be a member. An empty membership list refuses everything, which is the whole
      correction.
    - **An unbound API key** — the CLI break-glass key, minted by an operator with database access.
      Deliberately unrestricted: it exists for the moment when the control plane is unavailable.
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


#: Methods that cannot reach a model. A GET on these surfaces lists what is configured.
SPENDS_NOTHING = frozenset({"GET", "HEAD", "OPTIONS"})


def must_name_a_use_case(request: Request, principal: Principal) -> bool:
    """Whether this caller has to name a use case, or may go unattributed (`FRD-102`, `ADR-0015`).

    **One definition, both surfaces.** The rule decides whether a model call belongs to somebody,
    and `FRD-126`'s lesson is that a rule restated on a second surface is a rule that differs on
    one of them — which is exactly how the KIRA surface once read an empty membership list as
    "anything goes".

    Two exemptions, and neither is undocumented traffic:

    - **demo**, where authentication is off and there is no identity to attribute to. Bounded by
      `AIRA_DEMO_MODE`, which `security.py` refuses to leave on outside `local`.
    - the **unbound break-glass key** (`ADR-0015`), minted by an operator with database access for
      the moment the control plane is unavailable — a credential that needs a use case *from
      Management* is no use when Management is what is broken. Its row still carries the key prefix
      and the subject, so the request belongs to a credential somebody created and can revoke.

    Everybody else names one. An OIDC caller who names none belongs to nothing here, and serving
    them charges no budget, applies no use-case rate limit and consults no model release
    (`FRD-308`) — measured before this was closed: 200, 200 tokens, `use_case = NULL`.
    """
    if not request.app.state.settings.require_use_case:
        return False
    if request.method in SPENDS_NOTHING:
        # **A reading is not a model call.** `require_attribution` is mounted on the whole surface,
        # so this rule reached `GET /v1beta/models` too — and the console's "which models does the
        # gateway serve" started answering 400 for a Global Administrator, who is a member of
        # nothing by design. The requirement exists to attribute **spend**; a listing has nothing
        # to attribute, and demanding a use case for it would make reading the catalog need a
        # membership nobody needs.
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

    # An API key issued by Management is bound to exactly one use case (FRD-205): it needs no
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
