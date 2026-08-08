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
