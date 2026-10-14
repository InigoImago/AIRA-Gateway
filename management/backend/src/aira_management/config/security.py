"""Deployment-safety checks for the management backend (ADR-0007).

The development defaults (a well-known ``SECRET_KEY``, ``DEBUG``, ``ALLOWED_HOSTS=*``) are
convenient locally and dangerous anywhere else: a leaked signing key forges sessions and signed
values, ``DEBUG`` renders tracebacks containing settings and environment, and a wildcard host
list opens the door to Host-header poisoning.

Rather than hoping every deployment remembers to override them, the settings module refuses to
start outside ``environment=local`` while any of them is still in place — and in every environment,
local included, on a value that cannot be read at all (`misconfigured_settings`).
"""

from __future__ import annotations

import os

from django.core.exceptions import ImproperlyConfigured

from aira_common.roles import Role, RoleMappingError, parse_role_groups
from aira_common.transport_security import plaintext_problems
from aira_management.config.app_settings import DEV_SECRET_KEY, ManagementSettings
from aira_management.config.rates import bound_rate_problem, drf_rate_problem

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


def misconfigured_settings(settings: ManagementSettings) -> list[str]:
    """Values no environment can run on, local included.

    Each of these is otherwise read at request time, where a typo becomes a `500` on every request
    (a throttle rate DRF cannot parse, a role mapping) or a key that outlives the maximum this
    installation states. Reported, not raised, for the same reason as the rest of this module: a
    review should see every problem at once (`ADR-0015`).
    """
    problems: list[str] = []
    try:
        parse_role_groups(settings.role_groups)
    except RoleMappingError as exc:
        problems.append(f"AIRA_ROLE_GROUPS is malformed: {exc}")
    user_rate = drf_rate_problem(settings.throttle_user)
    if user_rate is not None:
        problems.append(f"AIRA_THROTTLE_USER {user_rate}.")
    failure_rate = bound_rate_problem(settings.throttle_auth_failures)
    if failure_rate is not None:
        problems.append(f"AIRA_THROTTLE_AUTH_FAILURES {failure_rate}.")
    if settings.api_key_max_days < 1:
        problems.append(
            f"AIRA_API_KEY_MAX_DAYS is {settings.api_key_max_days} — a key lives at least a day."
        )
    elif not 1 <= settings.api_key_default_days <= settings.api_key_max_days:
        problems.append(
            f"AIRA_API_KEY_DEFAULT_DAYS is {settings.api_key_default_days} — it must lie between "
            f"1 and AIRA_API_KEY_MAX_DAYS ({settings.api_key_max_days}), or a key issued without a "
            "lifetime would be already expired, or outlive the maximum."
        )
    if settings.trusted_proxy_hops < 0:
        problems.append(
            f"AIRA_TRUSTED_PROXY_HOPS is {settings.trusted_proxy_hops} — a count of proxies, 0 "
            "when this service is reached directly."
        )
    return problems


def proxy_ssl_header(settings: ManagementSettings) -> tuple[str, str] | None:
    """`SECURE_PROXY_SSL_HEADER`: believe a proxy's `X-Forwarded-Proto` when one is declared.

    Django sends HSTS only on a request it considers secure, and behind a proxy that terminates
    TLS every request reaches it as plain HTTP — so without this, HSTS is configured and never
    sent. Believed only with a proxy in front (`AIRA_TRUSTED_PROXY_HOPS` ≥ 1): reached directly,
    the header is the caller's. Believing it changes nothing but that header here — this API sets
    no cookie and redirects nowhere.
    """
    return ("HTTP_X_FORWARDED_PROTO", "https") if settings.trusted_proxy_hops > 0 else None


def unsafe_settings(settings: ManagementSettings) -> list[str]:
    """Human-readable reasons why ``settings`` must not be used: a malformed value anywhere
    (`misconfigured_settings`), a development default outside local."""
    problems = misconfigured_settings(settings)
    if is_local(settings):
        return problems
    if settings.test_database:
        problems.append(
            "AIRA_TEST_DATABASE is on — this service would run on an empty in-memory database "
            "and lose everything it holds on restart."
        )
    if not settings.oidc_issuer.strip():
        problems.append(
            "AIRA_OIDC_ISSUER is unset — no token could be verified, so nobody could sign in to "
            "the console. Name the realm, e.g. https://keycloak.example.com/realms/aira."
        )
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
    # Art. 13(1)(a) DSGVO: the notice every console user must acknowledge names the controller.
    # Printed without one it is a form nobody filled in, acknowledged by everybody (`FRD-625`).
    if not settings.privacy_controller.strip():
        problems.append(
            "AIRA_PRIVACY_CONTROLLER is unset — the privacy notice every console user is shown "
            "would name no controller. Set the organisation's name, address and a contact."
        )
    if settings.kafka_bootstrap_servers.strip() and settings.kafka_security().is_plaintext:
        problems.append(
            "AIRA_KAFKA_SECURITY_PROTOCOL is PLAINTEXT — every configuration change this service "
            "publishes crosses the broker in the clear and anyone who can reach it can publish "
            "their own. Use SASL_SSL with a broker identity of this service's own (SSL alone "
            "carries none)."
        )
    problems.extend(
        plaintext_problems(
            [
                ("AIRA_OIDC_ISSUER", settings.oidc_issuer),
                ("AIRA_OIDC_JWKS_URI", settings.jwks_uri() if settings.oidc_issuer else ""),
                ("VAULT_ADDR", os.environ.get("VAULT_ADDR", "")),
            ]
        )
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
