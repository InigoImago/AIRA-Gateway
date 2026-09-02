"""Vault and Redis, watched at the call they actually make (`FRD-617` §3.3).

Both already spoke up when they failed, and neither ever said it had worked. *"No errors"* and
*"it answered"* are different statements — the sentence `tools/lab_status.py` was written for one
layer out — and for a counter store the second carries a number that matters on its own: a Redis
answering in 400 ms is a working store and a gateway that pays for it on every request.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from aira_common.counters import CountersUnavailable, RedisRunner
from aira_common.integration_debug import configure_integration_debug
from aira_common.logging import configure_logging
from aira_common.secrets import (
    VaultClient,
    VaultConfig,
    VaultUnavailable,
    load_secrets,
)


@pytest.fixture(autouse=True)
def _channel() -> Iterator[None]:
    configure_logging("INFO", json_output=True)
    configure_integration_debug("vault,redis")
    yield
    configure_integration_debug("")


def calls(capsys: Any) -> list[dict]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{") and '"integration_call"' in line
    ]


CONFIG = VaultConfig(address="http://vault:8200", mount="secret", path="aira", role_id="r-1")


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _vault(handler: Any) -> VaultClient:
    return VaultClient(CONFIG, client=_client(handler))


def test_an_approle_login_says_where_it_went_and_what_the_status_was(
    capsys: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("VAULT_SECRET_ID", "s-1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"auth": {"client_token": "t-1"}})

    assert _vault(handler).login() == "t-1"

    (line,) = [c for c in calls(capsys) if c["operation"] == "login"]
    assert line["outcome"] == "ok"
    assert line["target"] == "http://vault:8200"
    assert line["status"] == 200
    assert line["secret_id_source"] == "VAULT_SECRET_ID"
    # The request body carries the secret-id, so nothing from it may reach the line.
    assert "s-1" not in json.dumps(line)


def test_a_vault_that_refuses_the_role_is_a_failure_with_its_status(
    capsys: Any, monkeypatch: Any
) -> None:
    monkeypatch.setenv("VAULT_SECRET_ID", "s-1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": ["permission denied"]})

    with pytest.raises(VaultUnavailable):
        _vault(handler).login()

    (line,) = [c for c in calls(capsys) if c["operation"] == "login"]
    assert line["outcome"] == "failed"
    assert line["detail"] == "HTTP 403"


def test_a_vault_that_does_not_answer_is_a_timeout(capsys: Any, monkeypatch: Any) -> None:
    monkeypatch.setenv("VAULT_SECRET_ID", "s-1")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(VaultUnavailable):
        _vault(handler).login()

    (line,) = [c for c in calls(capsys) if c["operation"] == "login"]
    assert line["outcome"] == "timeout", "a timeout and a refusal send a reader to different places"


def test_reading_the_path_says_which_path(capsys: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"data": {"AIRA_SECRET_KEY": "shh"}}})

    assert _vault(handler).read("t-1") == {"AIRA_SECRET_KEY": "shh"}

    (line,) = [c for c in calls(capsys) if c["operation"] == "read"]
    assert line["outcome"] == "ok"
    assert (line["mount"], line["path"]) == ("secret", "aira")
    # The values are the whole point of the call and none of them belongs in a log line.
    assert "shh" not in json.dumps(line)


def test_a_missing_path_is_reported_as_the_status_vault_gave(capsys: Any) -> None:
    """ "Vault is down" and "nobody has written that key yet" call for different people."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": []})

    with pytest.raises(Exception, match="."):
        _vault(handler).read("t-1")

    (line,) = [c for c in calls(capsys) if c["operation"] == "read"]
    assert line["detail"] == "HTTP 404"


async def test_a_counter_store_that_is_not_there_is_reported_with_its_address(
    capsys: Any,
) -> None:
    runner = RedisRunner("redis://aira:s3cret@127.0.0.1:6390/0", connect_timeout=0.05)
    with pytest.raises(CountersUnavailable):
        await runner.run("return 1", keys=["k"], args=[])
    await runner.close()

    (line,) = [c for c in calls(capsys) if c["system"] == "redis"]
    assert line["operation"] == "script"
    assert line["outcome"] in {"failed", "timeout"}
    assert line["keys"] == 1
    # The password lives in the authority of a Redis URL, where `redact_url_query` cannot see it.
    assert "s3cret" not in json.dumps(line)
    assert line["target"] == "redis://aira:REDACTED@127.0.0.1:6390/0"


def test_vault_is_watched_even_though_it_runs_before_the_settings_exist(
    capsys: Any, monkeypatch: Any
) -> None:
    """The gap the live stack found (`FRD-617` §3.7).

    `VaultSource` is a settings *source*, so `load_secrets` runs inside `GatewaySettings()` — and
    every entry point configures the channel with the *finished* settings, one step later. So the
    one system whose entire life is start-up was the one the channel could not describe: a gateway
    pointed at a dead Vault port failed closed exactly as designed, with `AIRA_DEBUG_INTEGRATIONS=
    all`, and said nothing.

    Driven through `load_secrets` with the channel **off** and only the environment set, which is
    the state a real process is in at that moment.
    """
    monkeypatch.setenv("AIRA_DEBUG_INTEGRATIONS", "vault")
    monkeypatch.setenv("VAULT_TOKEN", "dev-root")
    configure_integration_debug("")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"data": {"AIRA_CURRENCY": "EUR"}}})

    load_secrets(
        VaultConfig(address="http://vault:8200", mount="secret", path="aira"), _client(handler)
    )

    operations = [line["operation"] for line in calls(capsys) if line["system"] == "vault"]
    assert "read" in operations, (
        "Vault is read while the settings are being built, so the channel has to configure itself "
        "there — nothing else has run yet"
    )


def test_a_misspelled_switch_does_not_take_the_secret_loader_down(
    capsys: Any, monkeypatch: Any
) -> None:
    """A typo in a debug switch must not be reported by the secret loader. The settings validator
    refuses the process a moment later, with the message that names the valid systems."""
    monkeypatch.setenv("AIRA_DEBUG_INTEGRATIONS", "valut")
    monkeypatch.setenv("VAULT_TOKEN", "dev-root")
    configure_integration_debug("")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"data": {"AIRA_CURRENCY": "EUR"}}})

    secrets = load_secrets(
        VaultConfig(address="http://vault:8200", mount="secret", path="aira"), _client(handler)
    )
    assert secrets == {"AIRA_CURRENCY": "EUR"}
    assert [line for line in calls(capsys) if line["system"] == "vault"] == []


def test_reading_secrets_does_not_reconfigure_logging_a_process_already_set_up(
    monkeypatch: Any,
) -> None:
    """Defaults for a process that has not got to its own `configure_logging` yet, and never an
    override of one that has. Without the guard this reconfigures structlog underneath whatever is
    already running — which is how it first showed up: two `test_secrets.py` cases that read their
    lines through `structlog.testing.capture_logs` stopped seeing any.
    """
    import structlog

    monkeypatch.setenv("VAULT_TOKEN", "dev-root")
    configure_logging("WARNING", json_output=False)
    before = structlog.get_config()["processors"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"data": {"AIRA_CURRENCY": "EUR"}}})

    load_secrets(VaultConfig(address="http://vault:8200"), _client(handler))

    assert structlog.get_config()["processors"] == before
