"""Each plane its own database user, from one Vault path — against the stack's Vault and Postgres
(`FRD-150`).

The hermetic suite proves which value each settings class ends up with. What only shows up here is
whether those values are the ones a **real Postgres** accepts and refuses: two roles, one database
each, connect revoked from everybody else, and one real Vault path holding a wrong shared password
beside each plane's own. The gateway's migrations then run as the gateway's user and leave tables
it owns; Management connects as its own; each crossing is refused by Postgres, not by us.

Roles, databases and the Vault path are created per run and removed afterwards, named
`aira_plane_check_…`, so a crashed run leaves an orphan that says what it is. Kafka SASL is not
here: the stack's broker is `PLAINTEXT`, and a broker with SASL is infrastructure this suite does
not start.
"""

from __future__ import annotations

import pathlib
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
import sqlalchemy as sa
import stack_addresses
from aira_management.config.app_settings import ManagementSettings
from aira_management.config.database import build_databases
from alembic import command
from alembic.config import Config

from aira_common.config import VaultSource
from aira_common.secrets import SECRET_ID_ENV
from aira_gateway.config import GatewaySettings

pytestmark = pytest.mark.integration

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROOT_TOKEN = stack_addresses.setting("VAULT_DEV_ROOT_TOKEN_ID")
VAULT_CONTAINER = stack_addresses.container("vault")

#: Every name the two planes read for these credentials, cleared so the stack's own `.env` values
#: are not part of the question — only the host and port of the stack's Postgres are.
CREDENTIAL_NAMES = [
    f"AIRA_{plane}{name}"
    for plane in ("", "GATEWAY_", "MANAGEMENT_")
    for name in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "KAFKA_SASL_USERNAME",
        "KAFKA_SASL_PASSWORD",
    )
]


@dataclass(frozen=True, slots=True)
class Planes:
    """What one run created: a role and a database per plane, and the Vault path naming them."""

    gateway_user: str
    gateway_password: str
    gateway_db: str
    management_user: str
    management_password: str
    management_db: str


def _vault(*args: str) -> subprocess.CompletedProcess[str]:
    """The Vault CLI inside its container, as an operator would run it."""
    return subprocess.run(
        ["docker", "exec", "-e", f"VAULT_TOKEN={ROOT_TOKEN}", VAULT_CONTAINER, "vault", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _admin_url(database: str) -> str:
    """The stack's own administrative connection, read before this test clears its names."""
    url = sa.engine.make_url(GatewaySettings().database_url(use_sqlite=False))
    return url.set(drivername="postgresql+psycopg", database=database).render_as_string(
        hide_password=False
    )


@pytest.fixture
def planes(monkeypatch: pytest.MonkeyPatch) -> Iterator[Planes]:
    if _vault("status").returncode not in (0, 2):
        pytest.skip("no Vault in the stack — run `make up`")
    tag = uuid.uuid4().hex[:8]
    made = Planes(
        gateway_user=f"aira_plane_check_gw_{tag}",
        gateway_password=f"gw-{uuid.uuid4().hex}",
        gateway_db=f"aira_plane_check_gw_{tag}",
        management_user=f"aira_plane_check_mg_{tag}",
        management_password=f"mg-{uuid.uuid4().hex}",
        management_db=f"aira_plane_check_mg_{tag}",
    )
    admin = sa.create_engine(_admin_url("postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        for user, password, database in (
            (made.gateway_user, made.gateway_password, made.gateway_db),
            (made.management_user, made.management_password, made.management_db),
        ):
            connection.execute(sa.text(f"CREATE ROLE \"{user}\" LOGIN PASSWORD '{password}'"))
            connection.execute(sa.text(f'CREATE DATABASE "{database}" OWNER "{user}"'))
            connection.execute(sa.text(f'REVOKE CONNECT ON DATABASE "{database}" FROM PUBLIC'))

    path = f"aira-plane-check-{tag}"
    written = _vault(
        "kv",
        "put",
        f"secret/{path}",
        "AIRA_POSTGRES_PASSWORD=the-shared-one-is-wrong",
        f"AIRA_GATEWAY_POSTGRES_PASSWORD={made.gateway_password}",
        f"management_postgres_password={made.management_password}",
    )
    assert written.returncode == 0, written.stderr

    for name in CREDENTIAL_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AIRA_POSTGRES_USER", "aira_plane_check_nobody")
    monkeypatch.setenv("VAULT_ADDR", stack_addresses.url("vault"))
    monkeypatch.setenv("VAULT_TOKEN", ROOT_TOKEN)
    monkeypatch.setenv("VAULT_PATH", path)
    monkeypatch.delenv("VAULT_ROLE_ID", raising=False)
    monkeypatch.delenv(SECRET_ID_ENV, raising=False)
    VaultSource.reset()
    try:
        yield made
    finally:
        VaultSource.reset()
        _vault("kv", "metadata", "delete", f"secret/{path}")
        with admin.connect() as connection:
            for user, database in (
                (made.gateway_user, made.gateway_db),
                (made.management_user, made.management_db),
            ):
                connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
                connection.execute(sa.text(f'DROP ROLE IF EXISTS "{user}"'))
        admin.dispose()


def _gateway_as(monkeypatch: pytest.MonkeyPatch, user: str | None, database: str) -> str:
    """The gateway's own user, from the environment, and its database — the password from Vault."""
    if user is None:
        monkeypatch.delenv("AIRA_GATEWAY_POSTGRES_USER", raising=False)
    else:
        monkeypatch.setenv("AIRA_GATEWAY_POSTGRES_USER", user)
    monkeypatch.setenv("AIRA_POSTGRES_DB", database)
    VaultSource.reset()
    url = sa.engine.make_url(GatewaySettings().database_url(use_sqlite=False))
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def _current_user(url: str) -> str:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            return str(connection.execute(sa.text("SELECT current_user")).scalar_one())
    finally:
        engine.dispose()


def test_the_gateway_migrates_its_database_as_its_own_user(
    planes: Planes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real migrations, as the real settings resolve them from one Vault path — and every
    table they leave is owned by the gateway's user, not by whoever the shared names say."""
    url = _gateway_as(monkeypatch, planes.gateway_user, planes.gateway_db)
    assert _current_user(url) == planes.gateway_user

    config = Config(str(ROOT / "gateway" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "gateway" / "migrations"))
    command.upgrade(config, "head")

    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            owners = connection.execute(
                sa.text("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname = 'public'")
            ).scalars()
            owned = set(owners)
            tables = sa.inspect(connection).get_table_names()
    finally:
        engine.dispose()
    assert len(tables) > 5, f"the migrations ran somewhere else: {tables}"
    assert owned == {planes.gateway_user}


def test_management_connects_to_its_database_as_its_own_user(
    planes: Planes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AIRA_MANAGEMENT_POSTGRES_USER", planes.management_user)
    monkeypatch.setenv("AIRA_POSTGRES_DB", planes.management_db)
    VaultSource.reset()
    database = build_databases(ManagementSettings(), use_sqlite=False)["default"]

    with psycopg.connect(
        host=database["HOST"],
        port=database["PORT"],
        dbname=database["NAME"],
        user=database["USER"],
        password=database["PASSWORD"],
    ) as connection:
        user = connection.execute("SELECT current_user").fetchone()

    assert user == (planes.management_user,)


def test_with_the_shared_names_alone_postgres_refuses_the_gateway(
    planes: Planes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the plane's own user, the shared user and password are the ones sent — and they
    belong to nobody. Proves the own names above were what made the difference."""
    url = _gateway_as(monkeypatch, None, planes.gateway_db)

    with pytest.raises(sa.exc.OperationalError, match="aira_plane_check_nobody"):
        _current_user(url)


def test_the_gateway_cannot_use_managements_user(
    planes: Planes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway never reads Management's password, so naming Management's user gives it the
    gateway's password for somebody else — and Postgres refuses it."""
    url = _gateway_as(monkeypatch, planes.management_user, planes.management_db)

    with pytest.raises(sa.exc.OperationalError, match="password authentication failed"):
        _current_user(url)


def test_each_plane_is_kept_out_of_the_others_database(
    planes: Planes, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The separation the database was set up for, held by Postgres: Management's user, with its
    own correct password, may not connect to the gateway's database."""
    monkeypatch.setenv("AIRA_MANAGEMENT_POSTGRES_USER", planes.management_user)
    monkeypatch.setenv("AIRA_POSTGRES_DB", planes.gateway_db)
    VaultSource.reset()
    database = build_databases(ManagementSettings(), use_sqlite=False)["default"]

    with pytest.raises(psycopg.OperationalError, match="permission denied"):
        psycopg.connect(
            host=database["HOST"],
            port=database["PORT"],
            dbname=database["NAME"],
            user=database["USER"],
            password=database["PASSWORD"],
        ).close()
