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

**It is not a blanket one, and it was.** `is_local()` returned true for a demo and
`unsafe_settings` began with `if is_local(settings): return []`, so one environment variable
switched off *every* check below at once — including the first line of this docstring. A demo needs
the published Compose password and a realm with no audience mapper; it does not need its port
served to anybody who can reach it. Those two are not the same kind of concession, and collapsing
them meant a typo in one deployment variable produced a production gateway with a published
credential and no authentication at all.

So the waiver is a **list** now, per check, and each entry says what a demo actually needs. The two
that are never waived are the two the demo itself does not use: the shipped stack runs with
`AIRA_AUTH_REQUIRED` at its default (on) and attributes every request to one of the use cases it
seeds. A concession nobody asked for is not a concession, it is a hole.
"""

from __future__ import annotations

import os

from aira_common.transport_security import plaintext_problems
from aira_gateway.config import GatewaySettings

LOCAL_ENVIRONMENT = "local"

#: The published local development password. Kept here rather than imported so that changing the
#: Compose default cannot silently switch this check off.
DEV_POSTGRES_PASSWORD = "aira-local"


class UnsafeDeployment(Exception):
    """Settings that are safe locally and dangerous in the environment they were given."""


def is_local(settings: GatewaySettings) -> bool:
    """True locally, or in a deployment that has declared itself a demo.

    Read by `/readyz` to decide whether to show the whole body, and by `unsafe_settings` below to
    decide which checks a demo waives — **which ones, not whether any**. See `WAIVED_BY_A_DEMO`.
    """
    return settings.environment.strip().lower() == LOCAL_ENVIRONMENT or settings.demo_mode


def is_local_environment(settings: GatewaySettings) -> bool:
    """True only for `environment=local`. A demo is a *deployment*, however it is seeded."""
    return settings.environment.strip().lower() == LOCAL_ENVIRONMENT


#: What declaring `AIRA_DEMO_MODE` waives, named one by one.
#:
#: Each of these is something a demo genuinely needs: it runs the shipped Compose stack, whose
#: Postgres password is published in this repository, against a dev realm that has no audience
#: mapper, over a broker and a Keycloak on a private network with no TLS in front of them.
#:
#: **`auth_required` and `require_use_case` are not on this list, and that is the point.** The
#: shipped demo does not use either concession — it runs with authentication on and a published API
#: key, and it seeds the use cases its traffic is attributed to. A demo that switched them off would
#: not be demonstrating this product. Waiving them anyway turned one environment variable into an
#: open port serving models to anybody who found it.
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


def unsafe_settings(settings: GatewaySettings) -> list[str]:
    """Why ``settings`` must not be used in their declared environment. Empty means fine.

    Local development waives everything. A **demo** waives `WAIVED_BY_A_DEMO`, which is a list and
    not a `return []` — see this module's docstring for what the blanket cost.
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
    # **Every configured issuer**, not the single setting. `AIRA_OIDC_ISSUERS` (`FRD-118`) leaves
    # `oidc_audience` empty, so a check that read only that pair would have passed vacuously for a
    # multi-realm deployment — the shape where a hardening check silently stops applying because
    # the thing it guards moved.
    unnamed = [name for name, audience, _ in settings.issuers() if not audience.strip()]
    if "AIRA_OIDC_AUDIENCE" not in waived and settings.oidc_enabled and unnamed:
        problems.append(
            f"AIRA_OIDC_AUDIENCE is unset for {', '.join(unnamed)} — any token those issuers "
            "minted would be accepted, including one issued to a different client. Name the "
            "audience this gateway answers to, for every issuer."
        )
    if settings.oidc_enabled and not settings.issuers():
        # OIDC declared on and no issuer to validate against: the validator is never built, so
        # every bearer token is refused while the configuration says authentication is configured.
        # A control that reads as on and is absent — the shape `FRD-125` is named for.
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
    # keeps working against a local Keycloak (`ADR-0015`).
    problems.extend(
        plaintext_problems(
            {
                name: value
                for name, value in (
                    # Every issuer and every key set, for the reason above: the JWKS is where
                    # signing keys come from, and a second realm reached over plaintext is the
                    # same hole as the first.
                    *(("AIRA_OIDC_ISSUER", issuer) for issuer, _, _ in settings.issuers()),
                    *(("AIRA_OIDC_JWKS_URI", uri) for _, _, uri in settings.issuers()),
                    ("VAULT_ADDR", os.environ.get("VAULT_ADDR", "")),
                )
                if name not in waived
            }
        )
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
