"""Deployment-safety checks for the gateway (`ADR-0007`, `ADR-0015`).

The gateway refuses to start outside `local` with settings that are conveniences on a laptop and
holes in production: authentication off, the published Postgres password, OIDC without an audience
or an issuer, attribution not required, Kafka or a JWKS over plaintext. The check is
environment-shaped rather than a set of stricter defaults, so demo mode and a zero-configuration
local start keep working.

`AIRA_DEMO_MODE` waives **a list** of checks (`WAIVED_BY_A_DEMO`), never all of them: a demo needs
the published Compose credentials and a dev realm; it does not need its port served to anybody.
"""

from __future__ import annotations

import os

from aira_common.transport_security import plaintext_problems
from aira_gateway.config import GatewaySettings

LOCAL_ENVIRONMENT = "local"

#: The published local development password. Kept here rather than imported so that changing the
#: Compose default cannot silently switch this check off.
DEV_POSTGRES_PASSWORD = "aira-local"

#: What declaring `AIRA_DEMO_MODE` waives, one by one: a demo runs the shipped Compose stack (its
#: Postgres password is published here) against a dev realm with no audience mapper, over a broker
#: and a Keycloak on a private network without TLS. **`auth_required` and `require_use_case` are
#: not on this list**: the shipped demo uses neither concession, and waiving them turned one
#: environment variable into an open port serving models to anybody.
WAIVED_BY_A_DEMO = frozenset(
    {
        "AIRA_POSTGRES_PASSWORD",
        "AIRA_OIDC_AUDIENCE",
        "AIRA_KAFKA_SECURITY_PROTOCOL",
        "AIRA_OIDC_ISSUER",
        "AIRA_OIDC_JWKS_URI",
        "VAULT_ADDR",
    }
)


class UnsafeDeployment(Exception):
    """Settings that are safe locally and dangerous in the environment they were given."""


def is_local_environment(settings: GatewaySettings) -> bool:
    """True only for `environment=local`. A demo is a *deployment*, however it is seeded."""
    return settings.environment.strip().lower() == LOCAL_ENVIRONMENT


def is_local(settings: GatewaySettings) -> bool:
    """True locally, or in a deployment that has declared itself a demo.

    Read by `/readyz` to decide whether to show the whole body. Not what `unsafe_settings` waives
    on — a demo waives `WAIVED_BY_A_DEMO`, not every check.
    """
    return is_local_environment(settings) or settings.demo_mode


def unsafe_settings(settings: GatewaySettings) -> list[str]:
    """Why ``settings`` must not be used in their declared environment. Empty means fine.

    Local development waives everything; a demo waives `WAIVED_BY_A_DEMO`.
    """
    if is_local_environment(settings):
        return []
    waived = WAIVED_BY_A_DEMO if settings.demo_mode else frozenset[str]()
    problems: list[str] = []
    if not settings.auth_required:
        problems.append(
            "AIRA_AUTH_REQUIRED is off — every route is served to anyone who can reach the port. "
            "Leave it on outside local development."
        )
    if (
        "AIRA_POSTGRES_PASSWORD" not in waived
        and settings.postgres_password == DEV_POSTGRES_PASSWORD
    ):
        problems.append(
            "AIRA_POSTGRES_PASSWORD is still the published development default — "
            "set a unique value (from Vault) per deployment."
        )
    # Every configured issuer (`FRD-118`), not the single setting, which a multi-realm deployment
    # leaves empty — reading only that would pass vacuously.
    unnamed = [name for name, audience, _ in settings.issuers() if not audience.strip()]
    if "AIRA_OIDC_AUDIENCE" not in waived and settings.oidc_enabled and unnamed:
        problems.append(
            f"AIRA_OIDC_AUDIENCE is unset for {', '.join(unnamed)} — any token those issuers "
            "minted would be accepted, including one issued to a different client. Name the "
            "audience this gateway answers to, for every issuer."
        )
    if settings.oidc_enabled and not settings.issuers():
        # No validator is built, so every bearer token is refused while the configuration reads as
        # though authentication were configured (`FRD-125`).
        problems.append(
            "AIRA_OIDC_ENABLED is on and no issuer is configured (AIRA_OIDC_ISSUER or "
            "AIRA_OIDC_ISSUERS). No OIDC token can be validated, and every bearer credential is "
            "refused, while the configuration reads as though single sign-on were working."
        )
    if not settings.require_use_case:
        problems.append(
            "AIRA_REQUIRE_USE_CASE is off — an authenticated caller who belongs to no use case can "
            "name none, and the gateway serves them: the request is charged to no budget, bounded "
            "by no use-case rate limit, outside the model release (FRD-308) entirely, and its "
            "audit row names nobody. Measured: 200, 200 tokens, `use_case = NULL`. Every model "
            "call belongs to a use case or to a key issued for one."
        )
    if (
        "AIRA_KAFKA_SECURITY_PROTOCOL" not in waived
        and settings.kafka_bootstrap_servers.strip()
        and settings.kafka_security().is_plaintext
    ):
        problems.append(
            "AIRA_KAFKA_SECURITY_PROTOCOL is PLAINTEXT — the config topics are applied straight "
            "into the read-model this gateway's authorization is derived from, so anyone who can "
            "reach the broker can grant themselves access to any use case, with no credential and "
            "no audit row. Use SASL_SSL (or SSL) and give this service a broker identity."
        )
    # The JWKS is where signing keys come from: over plaintext, anyone on the path substitutes a
    # key set and mints tokens that verify. Checked here rather than in the verifier so a laptop
    # keeps working against a local Keycloak (`ADR-0015`). Pairs rather than a dict, because both
    # names repeat once per configured realm and a dict would keep only the last.
    problems.extend(
        plaintext_problems(
            [
                (name, value)
                for name, value in (
                    *(("AIRA_OIDC_ISSUER", issuer) for issuer, _, _ in settings.issuers()),
                    *(("AIRA_OIDC_JWKS_URI", uri) for _, _, uri in settings.issuers()),
                    ("VAULT_ADDR", os.environ.get("VAULT_ADDR", "")),
                )
                if name not in waived
            ]
        )
    )
    return problems


def enforce_safe_settings(settings: GatewaySettings) -> None:
    """Refuse to start rather than serve a deployment that is unsafe for its environment.

    At **startup**: a per-request check would leave a service that is up, passes its health probe
    and answers wrongly.
    """
    problems = unsafe_settings(settings)
    if problems:
        raise UnsafeDeployment(
            f"Unsafe settings for environment '{settings.environment}': " + " ".join(problems)
        )
