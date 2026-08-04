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
