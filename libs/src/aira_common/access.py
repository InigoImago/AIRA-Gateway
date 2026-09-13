"""The vocabulary of who may reach a use case (`FRD-209`).

One definition, read by both planes: Management writes grants, the gateway resolves them against a
token's groups.

- **The strongest role wins.** A caller granted `user` by one route and `admin` by another is an
  admin; otherwise the decision would depend on the order rows happen to be read in.
- **A group path is whatever the realm uses.** AIRA imposes no naming convention on somebody else's
  directory. The `/use-cases/<slug>` convention (`FRD-102`) still resolves, as one route among
  several.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

#: The group-path prefix that grants a use case by naming it (`FRD-102`). Kept because the dev
#: realm and the demo use it, and it needs no grant and no read-model lookup.
USE_CASE_GROUP_PREFIX = "/use-cases/"


class GrantRole(StrEnum):
    """What a grant lets its holder do inside one use case."""

    #: May call the gateway attributed to this use case, and see it and its figures.
    USER = "user"
    #: Additionally may change what happens inside it: members, keys, pipeline, budgets, limits.
    ADMIN = "admin"


class SubjectKind(StrEnum):
    """What a grant is *to*."""

    GROUP = "group"
    USER = "user"


#: Ordered weakest to strongest. `strongest` reads this rather than comparing strings, so adding a
#: middle role later is one line here and nothing anywhere else.
ROLE_ORDER: tuple[GrantRole, ...] = (GrantRole.USER, GrantRole.ADMIN)


def strongest(roles: Iterable[str]) -> str:
    """The strongest of ``roles``, or ``user`` if none is recognised.

    Unrecognised values fall to the **weakest** end: a role this version has never heard of must
    not be assumed powerful. Absence of information is not permission (`FRD-114`).
    """
    best = -1
    for role in roles:
        try:
            best = max(best, ROLE_ORDER.index(GrantRole(role)))
        except ValueError:
            continue
    return str(ROLE_ORDER[best]) if best >= 0 else str(GrantRole.USER)


def usecases_from_group_paths(groups: Iterable[str]) -> tuple[str, ...]:
    """Use-case slugs named directly by ``/use-cases/<slug>`` group paths (`FRD-102`).

    Tolerant of a trailing slash and of nesting below the prefix: Keycloak reports a subgroup
    member as carrying the parent path too, and one level deeper must not silently stop granting.
    """
    slugs: list[str] = []
    for group in groups:
        if not isinstance(group, str):
            # A signed claim is trustworthy about its issuer, not about its shape. A realm mapper
            # emitting objects is a misconfiguration that must stop authority, not authentication
            # — raising here made every request from that caller a 500.
            continue
        if group.startswith(USE_CASE_GROUP_PREFIX):
            slug = group[len(USE_CASE_GROUP_PREFIX) :].strip("/").split("/")[-1]
            if slug:
                slugs.append(slug)
    return tuple(dict.fromkeys(slugs))


def resolve(
    group_paths: Iterable[str],
    grants: Iterable[tuple[str, str, str]],
    direct: Iterable[tuple[str, str]] = (),
) -> dict[str, str]:
    """Which use cases a caller reaches, and as what.

    ``grants`` are ``(group_path, use_case, role)`` rows; ``direct`` are ``(use_case, role)`` rows
    naming the caller personally. The answer is the **union**, with the strongest role winning.
    """
    held = set(group_paths)
    roles: dict[str, list[str]] = {}

    for slug in usecases_from_group_paths(held):
        # The convention grants ordinary membership: a group path cannot express a role, which is
        # why explicit grants exist.
        roles.setdefault(slug, []).append(str(GrantRole.USER))

    for path, use_case, role in grants:
        if path in held:
            roles.setdefault(use_case, []).append(role)

    for use_case, role in direct:
        roles.setdefault(use_case, []).append(role)

    return {use_case: strongest(values) for use_case, values in roles.items()}
