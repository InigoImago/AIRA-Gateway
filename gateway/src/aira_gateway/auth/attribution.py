"""Use-case selection and request attribution (FRD-102).

The use case is chosen by the client (header overrides path) and, for OIDC, authorized
against the caller's Keycloak group membership. Membership groups live under
``/use-cases/<slug>``.

**Which group paths grant which use cases is not decided here.** That rule belongs to both planes
— Management answers it for `/api/v1/me`, the gateway for a bearer token — and it lives once, in
`aira_common.access`, whose own docstring says so: *"A slug typed twice is a slug that disagrees
with itself eventually — this project has already paid for that between the two planes once."* It
was nevertheless written out a second time here, character for character, prefix constant and all.
Nothing had drifted yet, which is the only reason it was still true; `FRD-209` is the record of
what it costs when it stops being.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import Request

from aira_gateway.scopes import person as person_key

USE_CASE_HEADER = "x-aira-use-case"
#: The same header, spelled the way a caller writes it.
#:
#: `USE_CASE_HEADER` is lowercase because that is how a header is matched; a *message* naming it has
#: to be the documented form, or the reader searches their client's configuration for a string that
#: is not there. The two surfaces had drifted — one message said `X-AIRA-Use-Case` and the other
#: `x-aira-use-case`, from the same header — so the human spelling gets a definition of its own.
USE_CASE_HEADER_NAME = "X-AIRA-Use-Case"
#: How the path selector is written, for the same reason: it appears in refusals on both surfaces.
USE_CASE_PATH_FORM = "/uc/<use-case>"
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
    #: The name this subject is known by, where the credential carries one.
    #:
    #: **Not an identity**: `subject` is what this attribution is *about*, and it is what the audit
    #: row's `subject` column keeps. The name is written beside it (`FRD-606`) and is what
    #: allowances are counted against, because the two credentials disagree about the subject and
    #: agree about the name — see :func:`aira_gateway.scopes.person`.
    username: str | None = None
    #: Which Keycloak realm minted the token (`FRD-118`). `None` for an API key or demo mode, and
    #: the same value on every row where one realm is configured — it earns its place during a
    #: migration between realms, when "which directory was this decided on" has two answers.
    issuer: str | None = None

    @property
    def person(self) -> str | None:
        """Who allowances are counted against — one human, whichever credential they used."""
        return person_key(self.subject, self.username)


# `realm_roles(claims)` lived here until 2026-08-11 and had been unreachable since `ADR-0017`
# (2026-08-09) made group membership the single source of a role — `realm_access.roles` is not
# read by either plane any more. Only its own tests still called it, and they asserted on
# `use-case-user`, a role abolished the day before.
#
# Removed rather than kept, for the reason this project already applied to `_injection_verdict`:
# **an unreachable helper is a rule the code claims and does not have.** A reader who finds it
# concludes the gateway still honours a realm role, and one who copies its defensive shape
# reintroduces the second mechanism `ADR-0017` existed to remove. The rule that replaced it is
# `aira_common.roles.roles_from_groups`, and it is the only one.


def resolve_use_case(request: Request) -> str | None:
    """Resolve the target use case: ``X-AIRA-Use-Case`` header overrides the ``/uc/<slug>`` path."""
    header = request.headers.get(USE_CASE_HEADER)
    if header and header.strip():
        return header.strip()
    path_slug = request.scope.get(USE_CASE_PATH_KEY)
    return path_slug if isinstance(path_slug, str) else None
