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

from aira_management.config.app_settings import DEV_SECRET_KEY, ManagementSettings

LOCAL_ENVIRONMENT = "local"


def is_local(settings: ManagementSettings) -> bool:
    """True for the local development environment (where dev defaults are acceptable)."""
    return settings.environment.strip().lower() == LOCAL_ENVIRONMENT


def unsafe_settings(settings: ManagementSettings) -> list[str]:
    """Return human-readable reasons why ``settings`` must not be used outside local dev."""
    if is_local(settings):
        return []
    problems: list[str] = []
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
