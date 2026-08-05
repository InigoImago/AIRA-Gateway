from aira_gateway.config import GatewaySettings


def test_defaults() -> None:
    settings = GatewaySettings()
    assert settings.app_name == "aira-gateway"
    assert settings.postgres_host == "localhost"
    assert settings.postgres_port == 5432
    assert settings.kafka_bootstrap_servers == "localhost:29092"


def test_kafka_host_port_parsing() -> None:
    settings = GatewaySettings(kafka_bootstrap_servers="broker-a:9092,broker-b:9092")
    assert settings.kafka_host_port == ("broker-a", 9092)


def test_kafka_host_port_without_port_defaults() -> None:
    settings = GatewaySettings(kafka_bootstrap_servers="justhost")
    assert settings.kafka_host_port == ("justhost", 9092)


def test_env_prefix(monkeypatch) -> None:
    monkeypatch.setenv("AIRA_POSTGRES_PORT", "6543")
    assert GatewaySettings().postgres_port == 6543


def test_database_url_sqlite() -> None:
    assert GatewaySettings().database_url(use_sqlite=True).startswith("sqlite+aiosqlite")


def test_database_url_postgres() -> None:
    settings = GatewaySettings(
        postgres_user="u",
        postgres_password="p",
        postgres_host="h",
        postgres_port=6,
        postgres_db="d",
    )
    assert settings.database_url(use_sqlite=False) == "postgresql+psycopg://u:p@h:6/d"


def test_jwks_uri_derived_from_issuer() -> None:
    settings = GatewaySettings(oidc_issuer="https://kc/realms/aira/")
    assert settings.jwks_uri() == "https://kc/realms/aira/protocol/openid-connect/certs"


def test_jwks_uri_explicit_override() -> None:
    assert GatewaySettings(oidc_jwks_uri="https://x/certs").jwks_uri() == "https://x/certs"


def test_whole_row_deletion_is_off_by_default() -> None:
    """FRD-404 FR-5: 0 means keep forever, and that is the default on purpose.

    The spend reporting reads `request_logs`, so a non-zero default would silently give every
    installation a reporting horizon nobody chose. A default is exactly the kind of value that
    drifts without anything going red, so it is pinned here rather than assumed.
    """
    assert GatewaySettings().log_retention_days == 0


def test_payload_retention_defaults_to_a_week() -> None:
    """The other half of FRD-404: payloads do not keep forever, and the promised period is a
    week. An installation that upgrades without configuring anything gets this."""
    assert GatewaySettings().default_retention_days == 7
