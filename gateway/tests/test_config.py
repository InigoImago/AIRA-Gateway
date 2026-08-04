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
