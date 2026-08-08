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
