"""The vocabulary of who may reach a use case (`FRD-209`).

One definition, read by both planes. Management writes grants; the gateway resolves them against a
token's groups. A slug typed twice is a slug that disagrees with itself eventually — this project
has already paid for that between the two planes once, over a role predicate.

Two things worth stating here rather than in either plane:

**The strongest role wins.** A caller granted `user` by one route and `admin` by another is an
admin. The alternative is an access decision that depends on the order rows happen to be read in,
which is not a decision anybody can review.

**A group path is whatever the realm uses.** `/ai/kundenservice`, `/abteilungen/vertrieb/nord` —
AIRA does not impose a naming convention on somebody else's directory. The historical
`/use-cases/<slug>` convention (`FRD-102`) still resolves, as one route among several.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

#: The group-path prefix that grants a use case by naming it (`FRD-102`).
#:
#: Kept because the dev realm and the demo use it, and because it is a perfectly good way to run a
#: small installation — no grant to write, no read-model lookup, resolvable from the token alone.
#: It is now one way in rather than the only one.
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

    Unrecognised values fall to the **weakest** end rather than being ignored: a role this version
    has never heard of must not be assumed to be powerful. "Absence of information is not
    permission" — the same rule the model catalog keeps (`FRD-114`).
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

    Deliberately tolerant of a trailing slash and of nesting below the prefix: Keycloak reports a
    subgroup member as carrying the parent path too, and a realm that nests one level deeper should
    not silently stop granting.
    """
    slugs: list[str] = []
    for group in groups:
        if not isinstance(group, str):
            # **A claim is not a type.** A signed token is trustworthy about who issued it and
            # says nothing about the shape of `groups`; a realm mapper that emits an object or a
            # number per group is a misconfiguration, and this used to answer it with
            # `AttributeError` — raised inside token validation, so **every request from that
            # caller was a 500**. Management's `_token_groups` has filtered since it was written
            # and says why: *"a realm misconfiguration must not stop authentication, it must stop
            # authority."* The same rule, on the plane that had not inherited it.
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
    naming the caller personally. The answer is the **union** with the strongest role winning
    (§2.1) — being a member twice over is being a member, and which row was read first is not a
    thing an access decision may depend on.
    """
    held = set(group_paths)
    roles: dict[str, list[str]] = {}

    for slug in usecases_from_group_paths(held):
        # The convention grants ordinary membership. A group path cannot express a role, which is
        # exactly why explicit grants exist.
        roles.setdefault(slug, []).append(str(GrantRole.USER))

    for path, use_case, role in grants:
        if path in held:
            roles.setdefault(use_case, []).append(role)

    for use_case, role in direct:
        roles.setdefault(use_case, []).append(role)

    return {use_case: strongest(values) for use_case, values in roles.items()}
