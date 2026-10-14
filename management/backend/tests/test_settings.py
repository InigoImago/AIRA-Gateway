import pytest
from aira_management.config.app_settings import ManagementSettings
from pydantic import ValidationError


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


def test_every_broker_is_read_not_only_the_first() -> None:
    settings = ManagementSettings(kafka_bootstrap_servers="broker:9092,other:9093")
    assert settings.kafka_brokers == (("broker", 9092), ("other", 9093))


def test_a_broker_without_a_port_takes_kafkas() -> None:
    settings = ManagementSettings(kafka_bootstrap_servers="hostonly")
    assert settings.kafka_brokers == (("hostonly", 9092),)


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


def test_an_ipv6_broker_is_dialled_without_its_brackets() -> None:
    """`[::1]:9092` is how Kafka spells an IPv6 broker; the brackets are not part of the host, and
    a probe of `[::1]` can never connect."""
    settings = ManagementSettings(kafka_bootstrap_servers="[::1]:9093,other:9092")
    assert settings.kafka_brokers[0] == ("::1", 9093)


def test_no_broker_is_no_broker_rather_than_localhost() -> None:
    """An empty list used to become `localhost:9092`, a broker an installation without Kafka never
    has — and readiness then said `not_ready` for good."""
    assert ManagementSettings(kafka_bootstrap_servers="").kafka_brokers == ()


@pytest.mark.parametrize("value", ["kafka:9092x", "kafka:0", "[::1", "::1:9092", ":9092"])
def test_a_broker_nothing_can_dial_refuses_the_process(value: str) -> None:
    """Read only by the readiness probe and the relay, an undiallable entry was a `500` on every
    probe of `/readyz`. Refused where it is configured, naming the variable."""
    with pytest.raises(ValidationError, match="AIRA_KAFKA_BOOTSTRAP_SERVERS"):
        ManagementSettings(kafka_bootstrap_servers=value)
