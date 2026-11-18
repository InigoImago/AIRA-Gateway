import pytest
from pydantic import ValidationError

from aira_common.config import BaseAiraSettings


def test_defaults() -> None:
    settings = BaseAiraSettings()
    assert settings.app_name == "aira"
    assert settings.environment == "local"
    assert settings.log_level == "INFO"
    assert settings.log_json is True
    assert settings.demo_mode is False


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("AIRA_APP_NAME", "custom")
    monkeypatch.setenv("AIRA_DEMO_MODE", "true")
    monkeypatch.setenv("AIRA_LOG_LEVEL", "DEBUG")
    settings = BaseAiraSettings()
    assert settings.app_name == "custom"
    assert settings.demo_mode is True
    assert settings.log_level == "DEBUG"


def test_unknown_env_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("AIRA_TOTALLY_UNKNOWN", "x")
    # extra="ignore" means construction must not raise
    BaseAiraSettings()


# ---- an empty environment variable (2026-08-08) ----------------------------------------------
#
# Docker Compose passes optional variables as `${AIRA_X:-}`, which expands to an **empty string**
# when nobody set one. For a string setting that is harmless and is what the compose file already
# relies on everywhere. For a number it stopped the process with a validation error naming a
# variable the operator never touched — found by adding two timeout settings the same way every
# string setting is added, and watching the gateway refuse to boot.


def test_an_empty_number_falls_back_to_the_default(monkeypatch) -> None:
    from aira_gateway.config import GatewaySettings

    monkeypatch.setenv("AIRA_VERTEX_TIMEOUT_SECONDS", "")

    assert GatewaySettings().vertex_timeout_seconds == 120.0


def test_an_empty_boolean_falls_back_too(monkeypatch) -> None:
    from aira_gateway.config import GatewaySettings

    monkeypatch.setenv("AIRA_AUTH_REQUIRED", "")

    assert GatewaySettings().auth_required is True


def test_an_empty_string_setting_still_means_empty(monkeypatch) -> None:
    """The other half, and the reason this is not applied to every field: clearing a string is a
    real instruction. `AIRA_CORS_ORIGINS=` means *no cross-origin access*, and substituting a
    default there would hand out a permission nobody granted."""
    from aira_gateway.config import GatewaySettings

    monkeypatch.setenv("AIRA_CORS_ORIGINS", "")

    assert GatewaySettings().cors_origins == ""


def test_a_real_value_is_still_read(monkeypatch) -> None:
    from aira_gateway.config import GatewaySettings

    monkeypatch.setenv("AIRA_VERTEX_TIMEOUT_SECONDS", "45")

    assert GatewaySettings().vertex_timeout_seconds == 45.0


# ---- the retention period for traffic without a use case --------------------------------------


@pytest.mark.parametrize("days", [0, -1, 3651])
def test_a_default_retention_outside_a_real_period_refuses_to_start(days: int) -> None:
    """Below one day the sweep kept payloads for a day anyway, while the privacy notice — which
    prints this value — told people they were kept without limit. Neither plane may start on a
    value the two would read differently."""
    with pytest.raises(ValidationError, match="default_retention_days"):
        BaseAiraSettings(default_retention_days=days)


@pytest.mark.parametrize("days", [1, 7, 3650])
def test_a_default_retention_within_a_use_cases_bounds_is_read(days: int) -> None:
    assert BaseAiraSettings(default_retention_days=days).default_retention_days == days


# ---- the realm a directory account obtains its token in ------------------------------------------


def test_without_a_token_realm_the_directory_account_is_the_issuers_realms_own() -> None:
    assert BaseAiraSettings().directory_token_realm == ""


def test_a_token_realm_is_read_from_the_environment_without_its_spaces(monkeypatch) -> None:
    monkeypatch.setenv("AIRA_DIRECTORY_TOKEN_REALM", " master ")
    assert BaseAiraSettings().directory_token_realm == "master"


@pytest.mark.parametrize("value", ["a/b", "..", ".", "master?x", "mas\tter"])
def test_a_token_realm_that_is_not_a_realms_name_refuses_both_planes_at_start(
    monkeypatch, value: str
) -> None:
    """At start-up, not at the first search: a value written into the token endpoint's path that
    names no realm would carry the account's credential elsewhere, and would otherwise surface as a
    console that quietly offers fewer people."""
    from aira_management.config.app_settings import ManagementSettings

    from aira_gateway.config import GatewaySettings

    monkeypatch.setenv("AIRA_DIRECTORY_TOKEN_REALM", value)
    for plane in (GatewaySettings, ManagementSettings):
        with pytest.raises(ValidationError, match="AIRA_DIRECTORY_TOKEN_REALM"):
            plane()
