import pytest
from pydantic import ValidationError

from aira_gateway.config import GatewaySettings


def test_defaults() -> None:
    settings = GatewaySettings()
    assert settings.app_name == "aira-gateway"
    assert settings.postgres_host == "localhost"
    assert settings.postgres_port == 5432
    assert settings.kafka_bootstrap_servers == "localhost:29092"


def test_every_broker_is_read_not_only_the_first() -> None:
    settings = GatewaySettings(kafka_bootstrap_servers="broker-a:9092,broker-b:9093")
    assert settings.kafka_brokers == (("broker-a", 9092), ("broker-b", 9093))


def test_a_broker_without_a_port_takes_kafkas() -> None:
    settings = GatewaySettings(kafka_bootstrap_servers="justhost")
    assert settings.kafka_brokers == (("justhost", 9092),)


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


# ---- the broker list and the database URL are read the way a client reads them --------------


def test_an_ipv6_broker_is_dialled_without_its_brackets() -> None:
    """`[host]:port` is how Kafka spells an IPv6 broker; the brackets are not part of the host."""
    assert GatewaySettings(kafka_bootstrap_servers="[::1]:9092").kafka_brokers == (("::1", 9092),)
    assert GatewaySettings(kafka_bootstrap_servers="[2001:db8::1]").kafka_brokers == (
        ("2001:db8::1", 9092),
    )


def test_no_broker_is_no_broker_to_check() -> None:
    """A single-node install with Kafka switched off is one `security.py` accepts."""
    assert GatewaySettings(kafka_bootstrap_servers="").kafka_brokers == ()


@pytest.mark.parametrize(
    "value",
    [
        "kafka:9092x",
        "kafka:",
        "kafka:0",
        "kafka:70000",
        ":9092",
        "2001:db8::1:9092",
        "[::1:9092",
        "bro ker:9092",
        "[not-an-address]:9092",
        "kafka/1:9092",
    ],
)
def test_a_broker_list_nothing_could_dial_refuses_to_start(value: str) -> None:
    """At startup, by name: read per probe, `kafka:9092x` was a 500 on every readiness check."""
    with pytest.raises(ValidationError, match="AIRA_KAFKA_BOOTSTRAP_SERVERS"):
        GatewaySettings(kafka_bootstrap_servers=value)


@pytest.mark.parametrize(
    ("user", "password", "host"),
    [
        ("aira", "p@ss", "db.internal"),
        ("aira", "a/b:c", "db"),
        ("aira", "pa%40ss", "db"),
        ("u@x", "p w#y?z", "db"),
        ("aira", "secret", "::1"),
    ],
    ids=["at-sign", "slash-and-colon", "percent", "user-and-space", "ipv6-host"],
)
def test_the_database_url_carries_the_credentials_exactly(
    user: str, password: str, host: str
) -> None:
    """Interpolated, `p@ss` named a host `ss@db.internal` and `pa%40ss` arrived as `pa@ss` — a DNS
    error and a refused password for a correct secret. Read back the way SQLAlchemy reads it."""
    from sqlalchemy import make_url

    settings = GatewaySettings(postgres_user=user, postgres_password=password, postgres_host=host)
    url = make_url(settings.database_url(use_sqlite=False))

    assert (url.username, url.password, url.host) == (user, password, host)
    assert (url.port, url.database) == (settings.postgres_port, settings.postgres_db)


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("max_request_bytes", -1),
        ("max_request_bytes", 0),
        ("log_queue_size", -1),
        ("anomaly_interval_seconds", 0),
        ("anomaly_interval_seconds", float("nan")),
        ("max_embedding_batch", 0),
        ("max_response_schema_bytes", 0),
        ("vertex_timeout_seconds", 0),
    ],
)
def test_a_size_or_interval_that_could_never_work_refuses_to_start(
    setting: str, value: float
) -> None:
    """`AIRA_MAX_REQUEST_BYTES=-1`, a common way to write "unlimited", made every request a 413 —
    the liveness probe included, so the container restarted in a loop."""
    with pytest.raises(ValidationError, match=setting):
        GatewaySettings(**{setting: value})


def test_zero_keeps_the_meaning_it_is_documented_with() -> None:
    """`0` writes every audit row on the request path, and switches the auth bound off."""
    settings = GatewaySettings(log_queue_size=0, max_auth_failures_per_minute=0)
    assert (settings.log_queue_size, settings.max_auth_failures_per_minute) == (0, 0)


def test_a_container_s_service_name_is_a_broker_too() -> None:
    """The host-name check refuses what nothing could dial, and only that: a Compose service
    name may carry an underscore, which a strict host-name rule would refuse."""
    assert GatewaySettings(kafka_bootstrap_servers="aira_kafka:9092").kafka_brokers == (
        ("aira_kafka", 9092),
    )


@pytest.mark.parametrize("setting", ["oidc_clock_skew_seconds", "oidc_expiry_leeway_seconds"])
@pytest.mark.parametrize("value", [400.0, -1.0, float("nan")], ids=str)
def test_a_token_tolerance_out_of_bounds_refuses_to_start_with_oidc_off(
    setting: str, value: float
) -> None:
    """`FRD-134` FR-3: refused above 300 seconds at startup. The bound was checked where a
    verifier is built, so with OIDC off a gateway started on any value — and refused to start the
    day somebody switched OIDC on, for a setting nobody had touched since."""
    with pytest.raises(ValidationError, match=setting.upper()):
        GatewaySettings(oidc_issuer="", **{setting: value})


def test_a_negative_proxy_count_refuses_to_start() -> None:
    """A count of proxies; 0 when the gateway is reached directly. Management refused a negative
    one, and the gateway read it as 0 without saying so — two planes, two answers to one setting."""
    with pytest.raises(ValidationError, match="trusted_proxy_hops"):
        GatewaySettings(trusted_proxy_hops=-1)
    assert GatewaySettings(trusted_proxy_hops=0).trusted_proxy_hops == 0
