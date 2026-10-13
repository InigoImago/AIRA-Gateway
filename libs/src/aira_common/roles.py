"""The canonical AIRA roles, shared by both services (`ADR-0009`, amended by `ADR-0017`).

**Three roles, conferred by group membership.** Keycloak is the source of truth and neither service
stores a role decision: both read the `groups` claim and resolve it through `AIRA_ROLE_GROUPS`
(:func:`roles_from_groups`). `realm_access.roles` is read by neither plane, so a realm role assigned
directly grants nothing — the guarantee `ADR-0017` was written to obtain. (This docstring once said
the opposite; `docs/ROLES.md` records the correction.)

Shared rather than defined in Management because both planes resolve the same claim, and two
independent answers drift. **What a role may do is not here.** Every check asks one permission
(`aira_common.permissions`, `ADR-0025`), never a role: a predicate over role names would hard-wire
an answer an installation can change, since IT Steuerung's permissions are data.
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

#: The roles a group may confer — today every role. Kept apart from `ALL_ROLES` because "what roles
#: exist" and "what a group may confer" are different questions that happen to coincide.
CONFIGURABLE_ROLES: frozenset[Role] = frozenset(ALL_ROLES)

#: Role names abolished by `ADR-0017`, answered with their own message because older `.env` files
#: still carry them.
_RETIRED_ROLES = frozenset({"use-case-admin", "use-case-user"})


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
    group inheritance, which puts the parent path in the token. A group that is not a string
    confers nothing, rather than raising on whoever carries it.
    """
    held = {group for group in groups if isinstance(group, str)}
    return tuple(
        str(role)
        for role in ALL_ROLES
        if role in mapping and any(path in held for path in mapping[role])
    )
