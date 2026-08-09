"""A convenience default is a production default one missing variable away (`ADR-0007`, 2026-08-08).

Management has refused to boot with development defaults outside `local` since `ADR-0007`. The
gateway — the half that serves the traffic — read `environment` for its telemetry and acted on it
nowhere. These tests are the check, and just as importantly they are the *guarantee that nothing
was taken away*: demo mode, the published demo key and a laptop's zero-configuration start still
work, because the check is environment-shaped rather than a set of stricter defaults.
"""

from __future__ import annotations

import pytest

from aira_gateway.config import GatewaySettings
from aira_gateway.security import (
    DEV_POSTGRES_PASSWORD,
    UnsafeDeployment,
    enforce_safe_settings,
    unsafe_settings,
)


def _production(**overrides: object) -> GatewaySettings:
    base: dict[str, object] = {
        "environment": "production",
        "auth_required": True,
        "postgres_password": "a-real-secret",
        # A broker identity, because the config topics feed the read-model this gateway's
        # authorization is read from — see `KafkaSecurity`. Every "correctly configured
        # production" fixture needs one now, and a test that forgot it would be asserting that a
        # deployment nobody would ship is fine.
        "kafka_security_protocol": "SASL_SSL",
    }
    base.update(overrides)
    return GatewaySettings(**base)  # type: ignore[arg-type]


# ---- what must stop a deployment ------------------------------------------------------------


def test_open_routes_refuse_to_start_in_production() -> None:
    problems = unsafe_settings(_production(auth_required=False))

    assert any("AIRA_AUTH_REQUIRED" in problem for problem in problems)


def test_the_published_database_password_refuses_to_start() -> None:
    problems = unsafe_settings(_production(postgres_password=DEV_POSTGRES_PASSWORD))

    assert any("AIRA_POSTGRES_PASSWORD" in problem for problem in problems)


def test_oidc_without_an_audience_refuses_to_start() -> None:
    """Any token the issuer minted would otherwise be accepted — including one issued to a
    different client, for a different purpose, by a user who never chose to call this gateway."""
    problems = unsafe_settings(_production(oidc_enabled=True, oidc_audience=""))

    assert any("AIRA_OIDC_AUDIENCE" in problem for problem in problems)


def test_oidc_with_an_audience_is_fine() -> None:
    assert unsafe_settings(_production(oidc_enabled=True, oidc_audience="aira-gateway")) == []


def test_enforce_names_the_environment_and_every_reason() -> None:
    """One message listing all of them: a guard that reports the first problem turns a
    configuration review into a queue of restarts."""
    settings = _production(auth_required=False, postgres_password=DEV_POSTGRES_PASSWORD)

    with pytest.raises(UnsafeDeployment) as raised:
        enforce_safe_settings(settings)

    assert "production" in str(raised.value)
    assert "AIRA_AUTH_REQUIRED" in str(raised.value)
    assert "AIRA_POSTGRES_PASSWORD" in str(raised.value)


# ---- what must keep working -----------------------------------------------------------------


def test_a_laptop_starts_with_no_configuration_at_all() -> None:
    """The whole point of the environment shape. A hardening pass that breaks `make up` is one
    that gets reverted."""
    assert unsafe_settings(GatewaySettings()) == []
    enforce_safe_settings(GatewaySettings())


def test_demo_mode_is_an_accepted_declaration_not_an_oversight() -> None:
    """A hosted demo has to be able to exist. `AIRA_DEMO_MODE` is loud and deliberate — the same
    door `seed_demo` already opens — so it exempts, rather than being refused as unsafe."""
    settings = _production(demo_mode=True, auth_required=False)

    assert unsafe_settings(settings) == []


def test_staging_is_not_local() -> None:
    """ "Not production" is not "local". A staging system holds real tokens and real traffic."""
    problems = unsafe_settings(
        GatewaySettings(environment="staging", auth_required=False)  # type: ignore[arg-type]
    )

    assert problems != []


def test_a_plaintext_identity_provider_refuses_to_start() -> None:
    """**The one misconfiguration that defeats authentication outright.**

    The JWKS is where signing keys come from. Fetched over plaintext, anyone on the path
    substitutes a key set of their own and mints tokens that verify — every role, every use case,
    every audit identity. Nothing checked it until 2026-08-09.
    """
    problems = unsafe_settings(
        GatewaySettings(
            environment="production",
            auth_required=True,
            postgres_password="a-real-secret",
            kafka_security_protocol="SASL_SSL",
            oidc_enabled=True,
            oidc_issuer="http://keycloak.example.com/realms/aira",
            oidc_audience="aira-gateway",
        )
    )

    assert any("AIRA_OIDC_ISSUER" in problem for problem in problems)
    # The derived JWKS URI is checked too — it is the URL the keys actually come from, and an
    # issuer that is https with a jwks_uri override that is not would otherwise pass.
    assert any("AIRA_OIDC_JWKS_URI" in problem for problem in problems)


def test_a_plaintext_jwks_override_is_caught_even_behind_an_https_issuer() -> None:
    problems = unsafe_settings(
        GatewaySettings(
            environment="production",
            auth_required=True,
            postgres_password="a-real-secret",
            kafka_security_protocol="SASL_SSL",
            oidc_enabled=True,
            oidc_issuer="https://keycloak.example.com/realms/aira",
            oidc_jwks_uri="http://keycloak.internal/realms/aira/protocol/openid-connect/certs",
            oidc_audience="aira-gateway",
        )
    )

    assert any("AIRA_OIDC_JWKS_URI" in problem for problem in problems)


def test_a_plaintext_vault_address_refuses_to_start(monkeypatch) -> None:
    """The AppRole login and every secret read cross that address (`FRD-116`)."""
    monkeypatch.setenv("VAULT_ADDR", "http://vault.example:8200")

    problems = unsafe_settings(
        GatewaySettings(
            environment="production",
            auth_required=True,
            postgres_password="a-real-secret",
            kafka_security_protocol="SASL_SSL",
            oidc_audience="aira-gateway",
        )
    )

    assert any("VAULT_ADDR" in problem for problem in problems)


def test_a_loopback_identity_provider_is_accepted(monkeypatch) -> None:
    """A sidecar terminating TLS on loopback is a normal deployment. Refusing it would push
    operators to `AIRA_ENVIRONMENT=local`, which switches every other check off as well."""
    monkeypatch.delenv("VAULT_ADDR", raising=False)

    problems = unsafe_settings(
        GatewaySettings(
            environment="production",
            auth_required=True,
            postgres_password="a-real-secret",
            kafka_security_protocol="SASL_SSL",
            oidc_enabled=True,
            oidc_issuer="http://127.0.0.1:8080/realms/aira",
            oidc_audience="aira-gateway",
        )
    )

    assert problems == []


def test_an_unauthenticated_event_bus_refuses_to_start() -> None:
    """**The bus is a trust boundary, and it had none.**

    `apply_event` writes whatever arrives on the config topics straight into the read-model this
    gateway derives authorization from. On a plaintext broker anybody who can reach it publishes
    `api_key.created` with a hash of their choosing, or `use_case_group.granted` naming a group
    they are in, and holds administrator access to any use case — with no credential presented and
    no audit row written, because from this side nothing unusual happened: configuration arrived,
    exactly as configuration does.

    Applying events without question is right *if* the bus is authenticated. There was simply no
    setting that could make it true, which is why this is a refusal and not a warning.
    """
    problems = unsafe_settings(_production(kafka_security_protocol="PLAINTEXT"))

    assert any("AIRA_KAFKA_SECURITY_PROTOCOL" in problem for problem in problems)


def test_a_deployment_with_no_broker_at_all_is_not_asked_for_one() -> None:
    """A single-node install with Kafka switched off has no bus to protect, and demanding a
    credential for a connection nobody makes is the kind of rule operators route around."""
    problems = unsafe_settings(
        _production(kafka_bootstrap_servers="", kafka_security_protocol="PLAINTEXT")
    )

    assert not any("KAFKA" in problem for problem in problems)
