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
