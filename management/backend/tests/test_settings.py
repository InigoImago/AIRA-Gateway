from aira_management.config.app_settings import ManagementSettings


def test_defaults() -> None:
    settings = ManagementSettings()
    assert settings.app_name == "aira-management"
    assert settings.postgres_db == "aira_mgmt"
    assert settings.debug is True


def test_allowed_hosts_list() -> None:
    settings = ManagementSettings(allowed_hosts="a.example, b.example ,")
    assert settings.allowed_hosts_list == ["a.example", "b.example"]


def test_jwks_uri_derived_from_issuer() -> None:
    settings = ManagementSettings(oidc_issuer="https://kc/realms/aira/")
    assert settings.jwks_uri() == "https://kc/realms/aira/protocol/openid-connect/certs"


def test_jwks_uri_explicit_override() -> None:
    assert ManagementSettings(oidc_jwks_uri="https://x/certs").jwks_uri() == "https://x/certs"


def test_kafka_host_port() -> None:
    settings = ManagementSettings(kafka_bootstrap_servers="broker:9092,other:9092")
    assert settings.kafka_host_port == ("broker", 9092)


def test_kafka_host_port_without_port() -> None:
    settings = ManagementSettings(kafka_bootstrap_servers="hostonly")
    assert settings.kafka_host_port == ("hostonly", 9092)


def test_env_override(monkeypatch) -> None:
    monkeypatch.setenv("AIRA_POSTGRES_DB", "custom_db")
    assert ManagementSettings().postgres_db == "custom_db"


def test_django_settings_wire_up() -> None:
    from django.conf import settings as django_settings

    assert "aira_management.apps.health" in django_settings.INSTALLED_APPS
    assert "aira_management.apps.seed" in django_settings.INSTALLED_APPS
    assert django_settings.AIRA.app_name == "aira-management"
    # tests run with an in-memory SQLite DB (AIRA_TEST_DATABASE=1 via repo-root conftest)
    assert django_settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
