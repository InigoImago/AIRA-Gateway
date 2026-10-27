"""Each plane's own database user and Kafka identity, on one Vault path (`FRD-150`).

Both planes read one `AIRA_POSTGRES_PASSWORD` and one `AIRA_KAFKA_SASL_PASSWORD`, so a Vault path
both of them read could hold one value of each — and a database and a broker set up with an
identity per plane could not be used. Asked of the real settings classes, built through the real
settings source with only Vault's read replaced, and read where each plane acts on the values: the
database connection and the broker's credentials.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from aira_management.config.app_settings import ManagementSettings
from aira_management.config.database import build_databases
from sqlalchemy.engine import make_url

from aira_common import config
from aira_common.config import PLANE_CREDENTIALS, VaultSource
from aira_gateway.config import GatewaySettings
from aira_gateway.security import unsafe_settings

#: Every name this question reads, cleared so a developer's environment is not part of it.
NAMES = [
    f"AIRA_{plane}{name.upper()}"
    for plane in ("", "GATEWAY_", "MANAGEMENT_")
    for name in PLANE_CREDENTIALS
]

#: One path holding the shared values and each plane's own beside them, as an installation whose
#: database and broker give each plane an identity would write it.
ONE_PATH = {
    "AIRA_POSTGRES_USER": "aira",
    "AIRA_POSTGRES_PASSWORD": "shared-db",
    "AIRA_GATEWAY_POSTGRES_USER": "aira_gatewayusr",
    "AIRA_GATEWAY_POSTGRES_PASSWORD": "gateway-db",
    "MANAGEMENT_POSTGRES_USER": "aira_mgmtusr",
    "management_postgres_password": "management-db",
    "AIRA_KAFKA_SASL_PASSWORD": "shared-kafka",
    "AIRA_GATEWAY_KAFKA_SASL_USERNAME": "aira-consumer",
    "AIRA_GATEWAY_KAFKA_SASL_PASSWORD": "consumer-kafka",
    "AIRA_MANAGEMENT_KAFKA_SASL_USERNAME": "aira-relay",
    "AIRA_MANAGEMENT_KAFKA_SASL_PASSWORD": "relay-kafka",
}


@pytest.fixture
def vault_holds(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[dict[str, str]], None]]:
    """Vault holds exactly what a test says; only the read is replaced, never the source."""

    def hold(stored: dict[str, str]) -> None:
        monkeypatch.setattr(config, "load_secrets", lambda: dict(stored))
        VaultSource.reset()

    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    hold({})
    yield hold
    VaultSource.reset()


def _gateway() -> GatewaySettings:
    return GatewaySettings(_env_file=None)  # type: ignore[call-arg]


def _management() -> ManagementSettings:
    return ManagementSettings(_env_file=None)  # type: ignore[call-arg]


def _connects_as(settings: GatewaySettings) -> tuple[str | None, str | None]:
    url = make_url(settings.database_url(use_sqlite=False))
    return url.username, url.password


def test_one_path_gives_each_plane_its_own_database_user(vault_holds) -> None:
    vault_holds(ONE_PATH)

    management = build_databases(_management(), use_sqlite=False)["default"]

    assert _connects_as(_gateway()) == ("aira_gatewayusr", "gateway-db")
    assert (management["USER"], management["PASSWORD"]) == ("aira_mgmtusr", "management-db")


def test_one_path_gives_each_plane_its_own_kafka_identity(vault_holds) -> None:
    """The consumer that only reads and the relay that writes, from one path."""
    vault_holds(ONE_PATH)

    consumer, relay = _gateway().kafka_security(), _management().kafka_security()

    assert (consumer.sasl_username, consumer.sasl_password) == ("aira-consumer", "consumer-kafka")
    assert (relay.sasl_username, relay.sasl_password) == ("aira-relay", "relay-kafka")


def test_without_a_planes_own_name_both_read_the_shared_one(vault_holds) -> None:
    """An installation that never writes one runs exactly as it always has."""
    vault_holds({"AIRA_POSTGRES_PASSWORD": "shared-db", "AIRA_KAFKA_SASL_PASSWORD": "shared-k"})

    gateway, management = _gateway(), _management()

    assert gateway.postgres_password == management.postgres_password == "shared-db"
    assert gateway.kafka_sasl_password == management.kafka_sasl_password == "shared-k"


def test_a_plane_never_takes_the_others_credentials(vault_holds) -> None:
    """The gateway reads no `AIRA_MANAGEMENT_*`, and Management no `AIRA_GATEWAY_*`."""
    vault_holds(
        {
            "AIRA_POSTGRES_PASSWORD": "shared-db",
            "AIRA_MANAGEMENT_POSTGRES_PASSWORD": "management-db",
            "AIRA_GATEWAY_KAFKA_SASL_PASSWORD": "consumer-kafka",
        }
    )

    assert _gateway().postgres_password == "shared-db"
    assert _management().kafka_sasl_password == ""


def test_the_planes_own_name_wins_wherever_each_was_read_from(
    vault_holds, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vault outranks the environment for each setting, but the plane's own *name* outranks the
    shared one: a shared value in Vault does not override a plane's own given to its container."""
    vault_holds({"AIRA_POSTGRES_PASSWORD": "shared-in-vault"})
    monkeypatch.setenv("AIRA_GATEWAY_POSTGRES_PASSWORD", "own-in-env")

    assert _gateway().postgres_password == "own-in-env"


def test_an_empty_own_name_is_the_shared_one(vault_holds, monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose passes an optional variable as an empty string."""
    vault_holds({"AIRA_POSTGRES_PASSWORD": "shared-db"})
    monkeypatch.setenv("AIRA_MANAGEMENT_POSTGRES_PASSWORD", "")

    assert _management().postgres_password == "shared-db"


def test_the_published_password_check_reads_the_one_in_effect(
    vault_holds, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside `local`, the development password is refused — the one the gateway connects with,
    whichever name it came under."""
    monkeypatch.setenv("AIRA_ENVIRONMENT", "production")
    vault_holds({"AIRA_GATEWAY_POSTGRES_PASSWORD": "aira-local", "AIRA_POSTGRES_PASSWORD": "real"})
    refused = unsafe_settings(_gateway())

    vault_holds({"AIRA_POSTGRES_PASSWORD": "aira-local", "AIRA_GATEWAY_POSTGRES_PASSWORD": "real"})
    accepted = unsafe_settings(_gateway())

    assert any("AIRA_POSTGRES_PASSWORD" in problem for problem in refused)
    assert not any("AIRA_POSTGRES_PASSWORD" in problem for problem in accepted)
