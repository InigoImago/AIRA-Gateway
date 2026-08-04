from aira_management.config.app_settings import ManagementSettings
from aira_management.config.observability import setup_observability


def test_setup_observability_disabled() -> None:
    assert setup_observability(ManagementSettings(otel_enabled=False)) is False


def test_setup_observability_enabled_instruments_django() -> None:
    settings = ManagementSettings(otel_enabled=True, otel_endpoint="http://localhost:4318")
    assert setup_observability(settings) is True
