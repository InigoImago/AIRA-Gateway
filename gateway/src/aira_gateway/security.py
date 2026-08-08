"""Deployment-safety checks for the gateway (`ADR-0007`, extended 2026-08-08).

Management has refused to boot outside `local` with development defaults since `ADR-0007`. The
gateway — the half that actually serves traffic — read `environment` for its telemetry and acted
on it nowhere, so every convenience default was a production default waiting for one missing
environment variable:

- `AIRA_AUTH_REQUIRED=false` serves every route to anybody who can reach the port.
- `AIRA_DEMO_MODE=true` seeds a key whose plaintext is **in this repository**.
- the local Postgres password is likewise published here.
- OIDC with no audience accepts any token the issuer minted, including one issued to a different
  client for a different purpose.

None of those is a bug locally; all of them are the same bug in production. So the check is
*environment-shaped* rather than a set of stricter defaults — that keeps demo mode, the demo key
and a laptop's zero-configuration start exactly as they were, which is the whole point: a
hardening pass that removes the demo is a hardening pass nobody runs.

`AIRA_DEMO_MODE` is an accepted escape hatch, because "this deployment is a demo" is a deliberate,
loud declaration — the same door `seed_demo` already uses, and refusing it would mean a hosted
demo could not exist.
"""

from __future__ import annotations

from aira_gateway.config import GatewaySettings

LOCAL_ENVIRONMENT = "local"

#: The published local development password. Kept here rather than imported so that changing the
#: Compose default cannot silently switch this check off.
DEV_POSTGRES_PASSWORD = "aira-local"


class UnsafeDeployment(Exception):
    """Settings that are safe locally and dangerous in the environment they were given."""


def is_local(settings: GatewaySettings) -> bool:
    """True locally, or in a deployment that has declared itself a demo."""
    return settings.environment.strip().lower() == LOCAL_ENVIRONMENT or settings.demo_mode


def unsafe_settings(settings: GatewaySettings) -> list[str]:
    """Why ``settings`` must not be used in their declared environment. Empty means fine."""
    if is_local(settings):
        return []
    problems: list[str] = []
    if not settings.auth_required:
        problems.append(
            "AIRA_AUTH_REQUIRED is off — every route is served to anyone who can reach the port. "
            "Leave it on outside local development."
        )
    if settings.postgres_password == DEV_POSTGRES_PASSWORD:
        problems.append(
            "AIRA_POSTGRES_PASSWORD is still the published development default — "
            "set a unique value (from Vault) per deployment."
        )
    if settings.oidc_enabled and not settings.oidc_audience.strip():
        problems.append(
            "AIRA_OIDC_AUDIENCE is unset — any token this issuer minted would be accepted, "
            "including one issued to a different client. Name the audience this gateway answers to."
        )
    return problems


def enforce_safe_settings(settings: GatewaySettings) -> None:
    """Refuse to start rather than serve a deployment that is unsafe for its environment.

    Refusing at **startup**: a check that fires per request produces a service that is up, passes
    its health probe and answers wrongly — which is how the CORS misconfiguration below was
    decided too, and for the same reason.
    """
    problems = unsafe_settings(settings)
    if problems:
        raise UnsafeDeployment(
            f"Unsafe settings for environment '{settings.environment}': " + " ".join(problems)
        )
