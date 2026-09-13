"""The canonical AIRA roles, shared by both services (`ADR-0009`, amended by `ADR-0017`).

**Three roles, conferred by group membership.** Keycloak is the source of truth and neither service
stores a role decision: both read the `groups` claim and resolve it through `AIRA_ROLE_GROUPS`
(:func:`roles_from_groups`). `realm_access.roles` is read by neither plane, so a realm role assigned
directly grants nothing — the guarantee `ADR-0017` was written to obtain. (This docstring once said
the opposite; `docs/ROLES.md` records the correction.)

Shared rather than defined in Management because the gateway asks the same questions (is this
caller governance, may it act on incidents), and two independent answers drift. What each plane
*does* with a role stays in that plane — Management maps roles to Django groups and object
permissions, the gateway compares the resolved set — so the data plane never depends on
``django-guardian``.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum


class Role(StrEnum):
    """The organisation-wide roles, and **only** those.

    Administering or belonging to a use case is a grant *on that use case* (`ADR-0017`), not a
    role. A role that can be given and does nothing reads as a broken system (`FRD-206`), so none is
    kept here.
    """

    GLOBAL_ADMIN = "global-admin"
    IT_SECURITY = "it-security"
    IT_STEUERUNG = "it-steuerung"


ALL_ROLES: tuple[Role, ...] = tuple(Role)

#: Roles that oversee the whole installation rather than one use case. Read-only by design: a
#: governance role sees every use case and acts inside none, which is why it is not a membership
#: (`ADR-0007`).
GOVERNANCE_ROLES: frozenset[Role] = frozenset({Role.GLOBAL_ADMIN, Role.IT_STEUERUNG})

#: Roles that may **see** every use case — wider than the roles that see every figure. IT Security's
#: view is restricted on content and spend (PRD §154), not on which use cases exist and how they are
#: configured, which is the security-relevant metadata it oversees.
OVERSIGHT_ROLES: frozenset[Role] = GOVERNANCE_ROLES | frozenset({Role.IT_SECURITY})

#: Roles that may **act** across use cases in an incident: stop a caller, lift a suspension, author
#: a rule that applies everywhere. Not `OVERSIGHT_ROLES`: IT Steuerung sees every figure and writes
#: nothing anywhere (PRD §154), and "may see every use case" is not "may stop every use case".
INCIDENT_ROLES: frozenset[Role] = frozenset({Role.GLOBAL_ADMIN, Role.IT_SECURITY})

#: Roles that may write the model catalog — declare, price and release a model (`FRD-307`). Here
#: rather than in Management because the gateway asks the same question.
CATALOG_ROLES: frozenset[Role] = frozenset({Role.GLOBAL_ADMIN})

#: The roles a group may confer — today every role. Kept apart from `ALL_ROLES` because "what roles
#: exist" and "what a group may confer" are different questions that happen to coincide.
CONFIGURABLE_ROLES: frozenset[Role] = frozenset(ALL_ROLES)

#: Role names abolished by `ADR-0017`, answered with their own message because older `.env` files
#: still carry them.
_RETIRED_ROLES = frozenset({"use-case-admin", "use-case-user"})


def may_catalogue(roles: Iterable[str]) -> bool:
    """True if any of ``roles`` may declare a model and release it for use."""
    return any(role in CATALOG_ROLES for role in roles)


def may_act_on_incidents(roles: Iterable[str]) -> bool:
    """True if any of ``roles`` may stop traffic or write a rule that applies everywhere."""
    return any(role in INCIDENT_ROLES for role in roles)


def has_oversight(roles: Iterable[str]) -> bool:
    """True if any of ``roles`` may see every use case (not necessarily every figure)."""
    return any(role in OVERSIGHT_ROLES for role in roles)


def is_governance(roles: Iterable[str]) -> bool:
    """True if any of ``roles`` oversees the whole installation.

    Takes plain strings, as a token claim does: an unknown role name is simply not governance, so a
    realm that grows a role the code has never heard of does not break authentication.
    """
    return any(role in GOVERNANCE_ROLES for role in roles)


class RoleMappingError(ValueError):
    """A role mapping that cannot be honoured. Raised at startup, never at request time."""


def parse_role_groups(raw: str) -> dict[Role, tuple[str, ...]]:
    """Parse ``role=/path[,/path...][;role=...]`` into the mapping both planes read.

    Refuses rather than ignores — an unknown role name, an entry with no ``=``, a group path that is
    not absolute — because a typo here grants nothing, silently. The bare realm root is refused as
    well: `/` matches no group path Keycloak emits (`FRD-209`).
    """
    mapping: dict[Role, tuple[str, ...]] = {}
    for entry in (part.strip() for part in raw.split(";")):
        if not entry:
            continue
        name, sep, paths = entry.partition("=")
        if not sep:
            raise RoleMappingError(f"'{entry}' is not a 'role=/group/path' pair.")
        try:
            role = Role(name.strip())
        except ValueError as exc:
            allowed = ", ".join(sorted(str(r) for r in CONFIGURABLE_ROLES))
            if name.strip() in _RETIRED_ROLES:
                raise RoleMappingError(
                    f"'{name.strip()}' is not a role any more. Administering or belonging to a "
                    "use case is a grant **on that use case** (FRD-209), not a property of a "
                    "person — mapping it to a group would confer every use case at once. Remove "
                    "it from AIRA_ROLE_GROUPS and grant the group on the use case instead."
                ) from exc
            raise RoleMappingError(
                f"'{name.strip()}' is not an AIRA role. Expected: {allowed}."
            ) from exc
        cleaned = tuple(p.strip() for p in paths.split(",") if p.strip())
        for path in cleaned:
            if not path.startswith("/") or path == "/":
                raise RoleMappingError(
                    f"'{path}' is not a group path. Keycloak emits full paths such as "
                    "'/aira/global-admins', and the realm root matches nothing."
                )
        if not cleaned:
            raise RoleMappingError(f"'{role}' names no group.")
        # Two entries for one role merge rather than the second winning: listing a group twice
        # meant both.
        mapping[role] = tuple(dict.fromkeys(mapping.get(role, ()) + cleaned))
    return mapping


def roles_from_groups(
    groups: Iterable[str], mapping: dict[Role, tuple[str, ...]]
) -> tuple[str, ...]:
    """The roles a caller holds, given the groups their token carries (`ADR-0017`).

    Exact path match, never a prefix: `/aira/global-admins-readonly` must not confer what
    `/aira/global-admins` does. A hierarchy that should confer a role does so through Keycloak's
    group inheritance, which puts the parent path in the token.
    """
    held = set(groups)
    return tuple(
        str(role)
        for role in ALL_ROLES
        if role in mapping and any(path in held for path in mapping[role])
    )
