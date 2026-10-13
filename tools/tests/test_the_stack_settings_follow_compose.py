"""A setting that is not an address follows Compose's resolution, like an address does.

`stack_addresses.setting` answers for the values a client must agree with the stack about besides a
port — a database password, the Keycloak administrator, the prefix of every container name. Each
one written down at a call site is right until somebody sets the variable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import stack_addresses

COMPOSE = """
services:
  postgres:
    container_name: ${AIRA_STACK:-aira}-postgres
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-aira-local}
      AIRA_POSTGRES_USER: ${AIRA_POSTGRES_USER:-${POSTGRES_USER:-aira}}
      AIRA_KAFKA_STAGE: ${AIRA_KAFKA_STAGE:-}
    ports:
      - "${AIRA_BIND_HOST:-127.0.0.1}:${AIRA_PUBLISH_POSTGRES_PORT:-5432}:5432"
"""


@pytest.fixture
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(COMPOSE)
    env = tmp_path / ".env"
    monkeypatch.setattr(stack_addresses, "COMPOSE_FILES", [compose])
    monkeypatch.setattr(stack_addresses, "ENV_FILE", env)
    for name in ("AIRA_STACK", "POSTGRES_PASSWORD", "POSTGRES_USER", "AIRA_POSTGRES_USER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("AIRA_PUBLISH_POSTGRES_PORT", raising=False)
    monkeypatch.delenv("AIRA_BIND_HOST", raising=False)
    return env


def test_the_default_is_the_one_compose_writes_even_mid_line(stack: Path) -> None:
    """`${AIRA_STACK:-aira}-postgres`: the default ends at its brace, not at the line's end."""
    assert stack_addresses.setting("AIRA_STACK") == "aira"
    assert stack_addresses.container("gateway") == "aira-gateway"


def test_the_environment_wins_then_the_env_file(
    stack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack.write_text("AIRA_STACK=from-file\nPOSTGRES_PASSWORD=p@ss/word\n")
    assert stack_addresses.container("kafka") == "from-file-kafka"

    monkeypatch.setenv("AIRA_STACK", "from-env")
    assert stack_addresses.container("kafka") == "from-env-kafka"
    # A password may hold any character; the URL must not be split on it.
    assert ":p%40ss%2Fword@" in stack_addresses.postgres_url("aira_mgmt")


def test_a_nested_default_is_resolved_by_the_same_rule(
    stack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert stack_addresses.setting("AIRA_POSTGRES_USER") == "aira"
    monkeypatch.setenv("POSTGRES_USER", "someone")
    assert stack_addresses.setting("AIRA_POSTGRES_USER") == "someone"


def test_the_url_follows_the_published_port(stack: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIRA_PUBLISH_POSTGRES_PORT", "15432")
    assert stack_addresses.postgres_url("aira_gateway").endswith("@localhost:15432/aira_gateway")


def test_an_empty_default_is_empty_and_an_unknown_name_is_refused(stack: Path) -> None:
    assert stack_addresses.setting("AIRA_KAFKA_STAGE") == ""
    with pytest.raises(KeyError, match="NOT_A_SETTING"):
        stack_addresses.setting("NOT_A_SETTING")
