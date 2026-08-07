"""The canonical AIRA roles, shared by both services (ADR-0009).

These five roles are Keycloak realm roles. Keycloak is the source of truth; neither service
stores a role decision of its own, and both read the same `realm_access.roles` claim from the
same token.

They live in the shared library rather than in Management because the gateway needs the same
answer to one question — *is this caller governance* — for reporting that spans use cases. Two
services deciding that independently is exactly how a role gets added in one place, missed in the
other, and quietly grants or withholds access for months.

What deliberately stays out of here is what each service *does* with a role. Management maps them
to Django groups and object permissions; the gateway compares a claim on a request. Those are
different mechanisms answering different questions, and pulling them together would make the data
plane depend on ``django-guardian``.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class Role(StrEnum):
    GLOBAL_ADMIN = "global-admin"
    IT_SECURITY = "it-security"
    IT_STEUERUNG = "it-steuerung"
    USE_CASE_ADMIN = "use-case-admin"
    USE_CASE_USER = "use-case-user"


ALL_ROLES: tuple[Role, ...] = tuple(Role)

# Roles that oversee the whole installation rather than one use case.
#
# Oversight is read-only by design: a governance role sees every use case and may act inside none
# of them, which is why it is deliberately *not* a membership (ADR-0007). Reporting is the first
# thing in the data plane to need this distinction.
GOVERNANCE_ROLES: frozenset[Role] = frozenset({Role.GLOBAL_ADMIN, Role.IT_STEUERUNG})

# Roles that may **see** every use case, which is a wider set than the ones that may see every
# figure. PRD §154 is explicit about the difference: IT Security has "security oversight (restricted
# view) … cross-use-case anomaly visibility … **cannot** see all business content by default".
#
# The two were one set until somebody logged in as `itsec` and found an empty console. A role that
# sees nothing is not a restricted view, it is an absent one — and the restriction the PRD asks for
# is on *content and spend*, not on knowing which use cases exist and how they are configured.
# Retention, payload storage, filters and limits are exactly the security-relevant metadata that
# role is there to oversee.
OVERSIGHT_ROLES: frozenset[Role] = GOVERNANCE_ROLES | frozenset({Role.IT_SECURITY})


def has_oversight(roles: Iterable[str]) -> bool:
    """True if any of ``roles`` may see every use case (not necessarily every figure)."""
    return any(role in OVERSIGHT_ROLES for role in roles)


def is_governance(roles: Iterable[str]) -> bool:
    """True if any of ``roles`` oversees the whole installation.

    Takes plain strings because that is what a token claim contains — an unknown role name is
    simply not governance, rather than an error, so a realm that grows a role the code has never
    heard of does not break authentication.
    """
    return any(role in GOVERNANCE_ROLES for role in roles)
