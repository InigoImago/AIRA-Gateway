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
    """The organisation-wide roles, and **only** those.

    `use-case-admin` and `use-case-user` were members here until 2026-08-10. `ADR-0017` abolished
    them on 2026-08-09 — administering or belonging to a use case is a grant *on that use case*,
    not a property of a person — and nothing has read them since. They stayed in the vocabulary
    anyway, the seed kept creating a Django group for each and assigning it, and the console kept
    offering them as an answer.

    That is the shape of defect this project refuses by name: **a role somebody can be given that
    does nothing.** An absent capability reads as a boundary; a present one that has no effect
    reads as a broken system, and the reader then distrusts the permissions that *do* work
    (`FRD-206`). Removing them is the whole fix — every predicate already asks the grant.
    """

    GLOBAL_ADMIN = "global-admin"
    IT_SECURITY = "it-security"
    IT_STEUERUNG = "it-steuerung"


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


# Roles that may **act** across use cases in an incident: stop a caller, lift a suspension, author
# a rule that applies everywhere.
#
# A third set, and the reason is the mistake this replaced. The gateway's kill switch was guarded by
# `OVERSIGHT_ROLES` — which is a *visibility* predicate — so IT Steuerung could stop traffic, while
# Management (correctly) refused it a global rule. PRD §154 gives that role every figure and **no
# write anywhere**; reusing "may see every use case" for "may stop every use case" is `FRD-206`'s
# mistake one level down, and a live round found it by asking the two planes the same question and
# getting different answers.
INCIDENT_ROLES: frozenset[Role] = frozenset({Role.GLOBAL_ADMIN, Role.IT_SECURITY})


def may_act_on_incidents(roles: Iterable[str]) -> bool:
    """True if any of ``roles`` may stop traffic or write a rule that applies everywhere."""
    return any(role in INCIDENT_ROLES for role in roles)


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


# ---- where a role comes from (ADR-0017) ---------------------------------------------------
#
# **Group membership, and nothing else.** Until 2026-08-09 both planes read `realm_access.roles`,
# while use-case access came from the `groups` claim (`FRD-209`) — two mechanisms answering
# "who is this", which is the shape of defect `FRD-209` was written to remove one level down.
#
# The two use-case roles are gone from this path entirely. They were never organisation-wide facts
# about a person: administering *a* use case is a relationship between a group and that use case,
# and `UseCaseGroupGrant` already holds it. What remains here are the three roles that really are
# properties of somebody within the whole installation.

#: The roles a group may confer — which, since the two use-case roles left the vocabulary
#: (2026-08-10), is every role there is.
#:
#: Kept as its own name rather than collapsed into `ALL_ROLES`, because the two are different
#: claims that merely happen to coincide: *what roles exist* and *what a group may confer*. A
#: fourth role that is granted some other way would separate them again, and a reader of
#: `AIRA_ROLE_GROUPS` should be looking at the second question.
CONFIGURABLE_ROLES: frozenset[Role] = frozenset(ALL_ROLES)


class RoleMappingError(ValueError):
    """A role mapping that cannot be honoured. Raised at startup, never at request time."""


def parse_role_groups(raw: str) -> dict[Role, tuple[str, ...]]:
    """Parse ``role=/path[,/path...][;role=...]`` into the mapping both planes read.

    Refuses rather than ignores, on every count: an unknown role name, a role that cannot be
    conferred by a group, an entry with no ``=``, and a group path that is not absolute. A typo
    here grants nothing and would do so **silently** — the failure this project has now recorded
    four times (a topic nothing created, `record_to_outbox` returning for an unknown type, the
    seed's `continue`, a filter that had been switched off by an empty answer).

    The bare realm root is refused for the same reason `FRD-209` refuses it as a grant: `/` matches
    no group path Keycloak ever emits, so it is a rule that can never fire.
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
            # The two abolished names get their own sentence, because they are the ones somebody
            # will try: they were roles until `ADR-0017`, they appear in older `.env` files, and
            # "is not an AIRA role" would leave a reader looking for a typo. There used to be a
            # separate branch for them further down; it became unreachable when they left the
            # vocabulary, and an unreachable guard is not a guard.
            retired = {"use-case-admin", "use-case-user"}
            if name.strip() in retired:
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
        # Two entries for one role are merged rather than the second silently winning: an
        # installation that lists a group twice meant both.
        mapping[role] = tuple(dict.fromkeys(mapping.get(role, ()) + cleaned))
    return mapping


def roles_from_groups(
    groups: Iterable[str], mapping: dict[Role, tuple[str, ...]]
) -> tuple[str, ...]:
    """The roles a caller holds, given the groups their token carries (`ADR-0017`).

    Exact path match, never a prefix. `/aira/global-admins-readonly` starting with
    `/aira/global-admins` must not confer anything, and a sub-group is a different group — if an
    installation wants a hierarchy to confer a role, Keycloak's own group inheritance puts the
    parent path in the token and the mapping names the parent.
    """
    held = set(groups)
    return tuple(
        str(role)
        for role in ALL_ROLES
        if role in mapping and any(path in held for path in mapping[role])
    )
