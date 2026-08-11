"""Use-case selection and request attribution (FRD-102).

The use case is chosen by the client (header overrides path) and, for OIDC, authorized
against the caller's Keycloak group membership. Membership groups live under
``/use-cases/<slug>``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import Request

USE_CASE_GROUP_PREFIX = "/use-cases/"
USE_CASE_HEADER = "x-aira-use-case"
USE_CASE_PATH_KEY = "aira_use_case_path"

# A client-supplied selector must look like a Management use-case slug (same charset and length
# as ``UseCase.slug``). Rejecting anything else keeps unvalidated client input out of the audit
# log, the read-model lookups, and the trace attributes (ADR-0007).
_SLUG = re.compile(r"^[a-z0-9-]{1,64}$")


def is_valid_use_case(slug: str) -> bool:
    """True if ``slug`` is a syntactically valid use-case identifier."""
    return bool(_SLUG.match(slug))


@dataclass(frozen=True, slots=True)
class Attribution:
    """What a request is attributed to: identity + selected use case."""

    subject: str
    method: str
    use_case: str | None
    #: The calling system's credential identity, carried through to the audit row (FRD-122 FR-5).
    credential: str | None = None
    #: The name this subject is known by, where the credential carries one. **Not** an identity
    #: and never written to the audit row — `subject` is what a row describes. It exists so a
    #: rule an administrator wrote about a person by name can find them whichever credential they
    #: used; see :meth:`aira_gateway.scopes.Scope.applying`.
    username: str | None = None


def usecases_from_groups(groups: Iterable[str]) -> tuple[str, ...]:
    """Extract use-case slugs from Keycloak group paths (``/use-cases/<slug>``)."""
    slugs: list[str] = []
    for group in groups:
        if group.startswith(USE_CASE_GROUP_PREFIX):
            slug = group[len(USE_CASE_GROUP_PREFIX) :].strip("/").split("/")[-1]
            if slug:
                slugs.append(slug)
    return tuple(dict.fromkeys(slugs))  # de-duplicate, preserve order


def realm_roles(claims: Mapping[str, Any]) -> tuple[str, ...]:
    """The realm roles a token carries, from Keycloak's ``realm_access.roles`` (ADR-0009).

    Anything malformed yields no roles rather than an error: a token whose claim is the wrong
    shape is a token with no oversight, which is the safe reading. Failing authentication over it
    would let a realm misconfiguration lock everyone out of the data plane.
    """
    access = claims.get("realm_access")
    if not isinstance(access, dict):
        return ()
    roles = access.get("roles")
    if not isinstance(roles, list):
        return ()
    return tuple(dict.fromkeys(str(role) for role in roles if isinstance(role, str)))


def resolve_use_case(request: Request) -> str | None:
    """Resolve the target use case: ``X-AIRA-Use-Case`` header overrides the ``/uc/<slug>`` path."""
    header = request.headers.get(USE_CASE_HEADER)
    if header and header.strip():
        return header.strip()
    path_slug = request.scope.get(USE_CASE_PATH_KEY)
    return path_slug if isinstance(path_slug, str) else None
