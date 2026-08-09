"""Deployment-safety checks for the management backend (ADR-0007).

The development defaults (a well-known ``SECRET_KEY``, ``DEBUG``, ``ALLOWED_HOSTS=*``) are
convenient locally and dangerous anywhere else: a leaked signing key forges sessions and signed
values, ``DEBUG`` renders tracebacks containing settings and environment, and a wildcard host
list opens the door to Host-header poisoning.

Rather than hoping every deployment remembers to override them, the settings module refuses to
start outside ``environment=local`` while any of them is still in place.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from aira_common.roles import Role, RoleMappingError, parse_role_groups
from aira_management.config.app_settings import DEV_SECRET_KEY, ManagementSettings

LOCAL_ENVIRONMENT = "local"


def is_local(settings: ManagementSettings) -> bool:
    """True for the local development environment (where dev defaults are acceptable)."""
    return settings.environment.strip().lower() == LOCAL_ENVIRONMENT


def _role_groups(settings: ManagementSettings) -> dict[Role, tuple[str, ...]]:
    """The parsed mapping, or nothing when it is malformed.

    A malformed mapping is reported by its own check below rather than raised from here: a
    configuration review should list *every* problem at once, which is why `unsafe_settings`
    collects reasons instead of failing on the first (`ADR-0015`).
    """
    try:
        return parse_role_groups(settings.role_groups)
    except RoleMappingError:
        return {}


def unsafe_settings(settings: ManagementSettings) -> list[str]:
    """Return human-readable reasons why ``settings`` must not be used outside local dev."""
    if is_local(settings):
        return []
    problems: list[str] = []
    try:
        parse_role_groups(settings.role_groups)
    except RoleMappingError as exc:
        problems.append(f"AIRA_ROLE_GROUPS is malformed: {exc}")
    if settings.secret_key == DEV_SECRET_KEY or not settings.secret_key:
        problems.append(
            "AIRA_SECRET_KEY is unset or still the development default — "
            "set a unique, secret value (from Vault) per deployment."
        )
    if "*" in settings.allowed_hosts_list:
        problems.append(
            "AIRA_ALLOWED_HOSTS is a wildcard — list the hostnames this service is served under."
        )
    if settings.debug:
        problems.append("AIRA_DEBUG must be off outside local development.")
    # Nobody can administer an installation whose global-admin group is unnamed (`ADR-0017`).
    # Roles come from group membership and nothing else, so an empty mapping is not a permissive
    # default — it is a console with no administrator, discovered hours later as "nobody can log
    # in properly". Local is exempt for `ADR-0015`'s reason: the demo must start on a fresh
    # checkout, and there the console states the mapping instead.
    if Role.GLOBAL_ADMIN not in _role_groups(settings):
        problems.append(
            "AIRA_ROLE_GROUPS names no group for 'global-admin' — nobody would be able to "
            "administer this installation. Set e.g. "
            "AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;it-security=/aira/it-security;"
            "it-steuerung=/aira/it-steuerung"
        )
    if settings.oidc_issuer.strip() and not settings.oidc_audience.strip():
        problems.append(
            "AIRA_OIDC_AUDIENCE is unset — any token this issuer minted would be accepted, "
            "including one issued to a different client. Name the audience this service "
            "answers to."
        )
    return problems


def enforce_safe_settings(settings: ManagementSettings) -> None:
    """Raise :class:`ImproperlyConfigured` if ``settings`` are unsafe for their environment."""
    problems = unsafe_settings(settings)
    if problems:
        raise ImproperlyConfigured(
            f"Unsafe settings for environment '{settings.environment}': " + " ".join(problems)
        )


def effective_debug(settings: ManagementSettings) -> bool:
    """``DEBUG`` is only ever honoured locally, whatever the environment asks for."""
    return settings.debug and is_local(settings)
