"""Who may do what across the installation — one catalogue, one decision (`FRD-614`, `ADR-0025`).

**Keycloak decides who holds a role; AIRA decides what a role may do.** A role is a set of
:class:`Permission` values bound to Keycloak group paths. A caller's permissions are the union over
every role whose group their token carries (:func:`effective`), and every installation-wide check
in the gateway, Management and the console asks :func:`allows`.

Two roles are fixed here and never read from a database, so a broken role table cannot lock anybody
out: `global-admin` holds every permission by construction, and `it-security` holds a fixed set.
Every other role — `it-steuerung` and the roles an installation defines — is data.

Installation-wide only. What somebody may do *inside* one use case is a grant on that use case
(`admin` / `user`, `FRD-209`), decided in `aira_common.access` and not here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from aira_common.roles import Role


class Permission(StrEnum):
    """Every installation-wide permission. A closed vocabulary: both planes and the console read it,
    and a name that is not here grants nothing."""

    USECASE_CREATE = "usecase.create"
    USECASE_READ_ALL = "usecase.read_all"
    USECASE_MANAGE_ALL = "usecase.manage_all"
    USECASE_READ_RETIRED = "usecase.read_retired"
    USECASE_PURGE = "usecase.purge"
    CATALOG_WRITE = "catalog.write"
    BUDGET_INSTALLATION_WRITE = "budget.installation.write"
    REPORT_READ_ALL = "report.read_all"
    TRACE_READ_ALL = "trace.read_all"
    ANOMALY_READ_ALL = "anomaly.read_all"
    ANOMALY_RULE_GLOBAL_WRITE = "anomaly.rule.global.write"
    INCIDENT_SUSPEND = "incident.suspend"
    INCIDENT_INVESTIGATE = "incident.investigate"
    PAYLOAD_READ_ANY = "payload.read_any"
    CONTENT_READ_READ = "content_read.read"
    OPERATIONS_DIAGNOSE = "operations.diagnose"
    SMOKETEST_AUTHOR = "smoketest.author"
    SMOKETEST_RUN_ANY = "smoketest.run_any"
    DIRECTORY_SEARCH = "directory.search"
    ROLE_READ = "role.read"
    ROLE_MANAGE = "role.manage"


@dataclass(frozen=True, slots=True)
class PermissionInfo:
    """What the console shows beside a checkbox. Kept with the permission so no screen restates it
    (`FRD-614` FR-10)."""

    area: str
    label: str
    #: Marked in the console: the permission reaches content, stops traffic, changes what every use
    #: case may call, or cannot be undone.
    sensitive: bool = False
    #: Held only by the Global Administrator and never offered as a checkbox (`ADR-0025` §6).
    reserved: bool = False


#: In the order the console lists them, grouped by area.
CATALOGUE: dict[Permission, PermissionInfo] = {
    Permission.USECASE_CREATE: PermissionInfo("Use cases", "Create a use case"),
    Permission.USECASE_READ_ALL: PermissionInfo(
        "Use cases", "See every use case and its configuration"
    ),
    Permission.USECASE_MANAGE_ALL: PermissionInfo(
        "Use cases", "Administer every use case as if it were its administrator", sensitive=True
    ),
    Permission.USECASE_READ_RETIRED: PermissionInfo("Use cases", "See retired use cases"),
    Permission.USECASE_PURGE: PermissionInfo(
        "Use cases", "Purge a retired use case for good", sensitive=True
    ),
    Permission.CATALOG_WRITE: PermissionInfo(
        "Models",
        "Declare, price and release models; read the providers' offerings",
        sensitive=True,
    ),
    Permission.REPORT_READ_ALL: PermissionInfo(
        "Figures and requests",
        "Every figure: reporting, the register, usage, the installation budget",
    ),
    Permission.BUDGET_INSTALLATION_WRITE: PermissionInfo(
        "Figures and requests", "Set the installation's own budget"
    ),
    Permission.TRACE_READ_ALL: PermissionInfo(
        "Figures and requests", "The request list of every use case (metadata, never content)"
    ),
    Permission.CONTENT_READ_READ: PermissionInfo(
        "Figures and requests", "The log of who read stored content"
    ),
    Permission.PAYLOAD_READ_ANY: PermissionInfo(
        "Security and incidents", "Read the stored content of any use case", sensitive=True
    ),
    Permission.ANOMALY_READ_ALL: PermissionInfo(
        "Security and incidents", "The security console and the findings of every use case"
    ),
    Permission.ANOMALY_RULE_GLOBAL_WRITE: PermissionInfo(
        "Security and incidents", "Author anomaly rules that apply everywhere"
    ),
    Permission.INCIDENT_SUSPEND: PermissionInfo(
        "Security and incidents",
        "Stop and resume callers, credentials and use cases",
        sensitive=True,
    ),
    Permission.INCIDENT_INVESTIGATE: PermissionInfo(
        "Security and incidents",
        "The Requests screen, source addresses, and requests a use case restricts to their makers",
        sensitive=True,
    ),
    Permission.OPERATIONS_DIAGNOSE: PermissionInfo(
        "Operations", "Check whether a model answers; the detail of the readiness probe"
    ),
    Permission.SMOKETEST_AUTHOR: PermissionInfo("Operations", "Write the question catalogue"),
    Permission.SMOKETEST_RUN_ANY: PermissionInfo(
        "Operations", "Run the question catalogue on any use case this person may call"
    ),
    Permission.DIRECTORY_SEARCH: PermissionInfo(
        "Platform", "Search people and groups in the directory"
    ),
    Permission.ROLE_READ: PermissionInfo("Platform", "See the roles and what each may do"),
    Permission.ROLE_MANAGE: PermissionInfo(
        "Platform", "Create, change and delete roles", sensitive=True, reserved=True
    ),
}

ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)

#: What a stored role may hold. Everything except the reserved: managing roles stays with the
#: Global Administrator, so no role can raise itself.
GRANTABLE: frozenset[Permission] = frozenset(
    permission for permission, info in CATALOGUE.items() if not info.reserved
)

#: IT Security's fixed set: it sees every use case, investigates and stops, reads content, and
#: writes the question catalogue. It changes no use case and no model.
IT_SECURITY_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.USECASE_READ_ALL,
        Permission.REPORT_READ_ALL,
        Permission.TRACE_READ_ALL,
        Permission.ANOMALY_READ_ALL,
        Permission.ANOMALY_RULE_GLOBAL_WRITE,
        Permission.INCIDENT_SUSPEND,
        Permission.INCIDENT_INVESTIGATE,
        Permission.PAYLOAD_READ_ANY,
        Permission.CONTENT_READ_READ,
        Permission.OPERATIONS_DIAGNOSE,
        Permission.SMOKETEST_AUTHOR,
        Permission.SMOKETEST_RUN_ANY,
        Permission.ROLE_READ,
    }
)

#: IT Steuerung's set until an installation changes it: every use case and every figure, and no
#: write anywhere and no content (PRD §3).
IT_STEUERUNG_DEFAULT: frozenset[Permission] = frozenset(
    {
        Permission.USECASE_READ_ALL,
        Permission.USECASE_READ_RETIRED,
        Permission.REPORT_READ_ALL,
        Permission.TRACE_READ_ALL,
        Permission.ANOMALY_READ_ALL,
        Permission.CONTENT_READ_READ,
        Permission.ROLE_READ,
    }
)

#: The labels of the built-in roles, served to the console so it restates none of them.
BUILTIN_LABELS: dict[Role, str] = {
    Role.GLOBAL_ADMIN: "Global administrator",
    Role.IT_SECURITY: "IT Security",
    Role.IT_STEUERUNG: "IT Steuerung",
}

#: The built-in roles nothing may change. The Global Administrator because it is the lock-out
#: guard; IT Security because an incident role an installation can narrow is one an attacker who
#: reached the console can switch off (`ADR-0025` §3).
FIXED_ROLES: frozenset[Role] = frozenset({Role.GLOBAL_ADMIN, Role.IT_SECURITY})


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    """One role: what it is called, which groups confer it, and what it may do."""

    slug: str
    label: str
    group_paths: tuple[str, ...]
    permissions: frozenset[Permission]
    #: A built-in role cannot be deleted.
    builtin: bool = False
    #: A fixed role cannot be changed at all.
    fixed: bool = False


def parse_permissions(values: Iterable[str]) -> frozenset[Permission]:
    """The permissions a stored role names, as a set a role may hold.

    A name the catalogue no longer has grants nothing, and a reserved permission is dropped: a row
    that was written by hand, or before a permission became reserved, must not confer it.
    """
    known = {str(permission) for permission in Permission}
    return frozenset(Permission(value) for value in values if value in known) & GRANTABLE


def builtin_roles(
    mapping: dict[Role, tuple[str, ...]],
    it_steuerung: frozenset[Permission] | None = None,
) -> tuple[RoleDefinition, ...]:
    """The three built-in roles, conferred by the groups `AIRA_ROLE_GROUPS` names.

    ``it_steuerung`` is IT Steuerung's stored set, or ``None`` where none is stored yet — then it
    holds its default. A stored set is read through :func:`parse_permissions`, so it can never hold
    the reserved permission.
    """
    steuerung = IT_STEUERUNG_DEFAULT if it_steuerung is None else it_steuerung & GRANTABLE
    sets = {
        # By construction, not a stored list: a permission added in a later release is held
        # without anybody ticking it, and no table can take it away.
        Role.GLOBAL_ADMIN: ALL_PERMISSIONS,
        Role.IT_SECURITY: IT_SECURITY_PERMISSIONS,
        Role.IT_STEUERUNG: steuerung,
    }
    return tuple(
        RoleDefinition(
            slug=str(role),
            label=BUILTIN_LABELS[role],
            group_paths=mapping.get(role, ()),
            permissions=sets[role],
            builtin=True,
            fixed=role in FIXED_ROLES,
        )
        for role in Role
    )


def held_roles(
    groups: Iterable[str], roles: Iterable[RoleDefinition]
) -> tuple[RoleDefinition, ...]:
    """The roles whose group the caller's token carries.

    An exact path match, never a prefix: `/aira/global-admins-readonly` must not confer what
    `/aira/global-admins` does (`FRD-605` FR-3).
    """
    held = set(groups)
    return tuple(role for role in roles if any(path in held for path in role.group_paths))


def effective(groups: Iterable[str], roles: Iterable[RoleDefinition]) -> frozenset[Permission]:
    """Everything the caller may do across the installation: the union over their roles.

    A union and never a subtraction (`ADR-0025` §8): a person in two groups holds what both confer.
    """
    permissions: frozenset[Permission] = frozenset()
    for role in held_roles(groups, roles):
        permissions |= role.permissions
    return permissions


def allows(permissions: Iterable[Permission], wanted: Permission) -> bool:
    """The one decision every installation-wide check asks."""
    return wanted in frozenset(permissions)


def builtin_permissions(
    role_slugs: Iterable[str], it_steuerung: frozenset[Permission] | None = None
) -> frozenset[Permission]:
    """What the built-in roles among ``role_slugs`` may do, without any group lookup.

    For a caller whose roles are already resolved — the gateway's `Principal`, Management's Django
    groups. An unknown slug confers nothing.
    """
    held = set(role_slugs)
    granted: frozenset[Permission] = frozenset()
    for role in builtin_roles({}, it_steuerung):
        if role.slug in held:
            granted |= role.permissions
    return granted
