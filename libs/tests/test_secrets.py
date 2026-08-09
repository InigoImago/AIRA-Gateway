"""Secrets come from Vault, and only from Vault (FRD-116).

Hermetic throughout: an ``httpx.MockTransport`` stands in for Vault, so the AppRole exchange, the
KV read, the precedence and every failure mode are exercised without a server. The integration
layer points the same code at the real one.

Three properties carry the feature, and each is tested for what it *prevents* rather than for what
it does:

- **Fail closed.** A configured Vault that cannot be read must stop the process. Falling back to
  the environment turns a broken secret store into a service that starts, looks healthy, and runs
  on a stale value.
- **No silent third source.** Vault wins, the environment fills the rest, and a key in neither is
  absent — never an empty string, which authenticates as nobody and reads as a permissions problem.
- **Values never surface.** The one test everything else is in service of.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from structlog.testing import capture_logs

from aira_common.secrets import (
    DEV_TOKEN_ENV,
    SECRET_ID_ENV,
    SECRET_ID_FILE_ENV,
    SecretMissing,
    VaultClient,
    VaultConfig,
    VaultUnavailable,
    load_secrets,
    resolve,
)

Handler = Callable[[httpx.Request], httpx.Response]

ADDRESS = "https://vault.internal:8200"
SECRET = "s.super-secret-token-value"
STORED = {"GOOGLE_API_KEY": "google-key-9f2", "POSTGRES_PASSWORD": "db-pw-7c1"}


def _config(**over: object) -> VaultConfig:
    fields: dict[str, object] = {"address": ADDRESS, "role_id": "role-1"}
    fields.update(over)
    return VaultConfig(**fields)  # type: ignore[arg-type]


def _vault(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _happy(data: dict[str, str] | None = None) -> Handler:
    """A Vault that authenticates and answers with ``data``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/approle/login"):
            return httpx.Response(200, json={"auth": {"client_token": "s.client-token"}})
        return httpx.Response(200, json={"data": {"data": data if data is not None else STORED}})

    return handler


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient Vault configuration. A test that passed because the developer's shell had a
    token set would be testing the shell."""
    for name in (SECRET_ID_ENV, SECRET_ID_FILE_ENV, DEV_TOKEN_ENV, "VAULT_ADDR", "VAULT_ROLE_ID"):
        monkeypatch.delenv(name, raising=False)


# == the happy path ==============================================================================


def test_secrets_are_read_through_an_approle_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)
    assert load_secrets(_config(), _vault(_happy())) == STORED


def test_the_secret_id_is_sent_but_the_role_id_alone_is_not_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"auth": {"client_token": "s.tok"}})
        seen["token"] = request.headers.get("x-vault-token")
        return httpx.Response(200, json={"data": {"data": STORED}})

    load_secrets(_config(), _vault(handler))

    assert seen["body"] == {"role_id": "role-1", "secret_id": SECRET}
    # The client token from the login, not the secret-id, is what reads the path.
    assert seen["token"] == "s.tok"


def test_the_namespace_is_sent_when_one_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vault Enterprise puts a tenant in a header. Omitting it reads the wrong namespace's
    secrets, or none, and the error says "not found" either way."""
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-vault-namespace", ""))
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"auth": {"client_token": "s.tok"}})
        return httpx.Response(200, json={"data": {"data": STORED}})

    load_secrets(_config(namespace="team-a"), _vault(handler))
    assert seen == ["team-a", "team-a"]


def test_the_mount_and_path_are_where_the_read_goes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"auth": {"client_token": "s.tok"}})
        return httpx.Response(200, json={"data": {"data": STORED}})

    load_secrets(_config(mount="kv", path="aira/gateway"), _vault(handler))
    assert "/v1/kv/data/aira/gateway" in seen[-1]


def test_a_value_that_is_not_a_string_is_coerced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every consumer is a settings field parsed from text. Letting a JSON number through would
    make the same key behave differently depending on how somebody typed it into Vault."""
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)
    handler = _happy({"PORT": 5432, "FLAG": True})  # type: ignore[dict-item]
    assert load_secrets(_config(), _vault(handler)) == {"PORT": "5432", "FLAG": "True"}


def test_a_null_value_is_dropped_rather_than_becoming_the_string_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`"None"` is a perfectly valid password, so a null must not become one — a key written as
    null is a key nobody has set, and it has to stay absent so the fallback applies."""
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)
    handler = _happy({"SET": "value", "UNSET": None})  # type: ignore[dict-item]
    assert load_secrets(_config(), _vault(handler)) == {"SET": "value"}


# == where the secret-id comes from ==============================================================


def test_the_environment_variable_is_tried_first(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    file = tmp_path / "secret-id"
    file.write_text("from-file")
    monkeypatch.setenv(SECRET_ID_ENV, "from-env")
    monkeypatch.setenv(SECRET_ID_FILE_ENV, str(file))

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            seen["secret_id"] = json.loads(request.content)["secret_id"]
            return httpx.Response(200, json={"auth": {"client_token": "s.tok"}})
        return httpx.Response(200, json={"data": {"data": STORED}})

    load_secrets(_config(), _vault(handler))
    assert seen["secret_id"] == "from-env"


def test_a_mounted_file_is_read_and_stripped(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A Kubernetes projected volume puts the credential on a tmpfs rather than in a manifest —
    and files written by a controller routinely end with a newline."""
    file = tmp_path / "secret-id"
    file.write_text("  from-file\n")
    monkeypatch.setenv(SECRET_ID_FILE_ENV, str(file))

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            seen["secret_id"] = json.loads(request.content)["secret_id"]
            return httpx.Response(200, json={"auth": {"client_token": "s.tok"}})
        return httpx.Response(200, json={"data": {"data": STORED}})

    load_secrets(_config(), _vault(handler))
    assert seen["secret_id"] == "from-file"


def test_a_file_that_cannot_be_read_is_named_rather_than_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A projected volume that failed to mount is a deployment problem. Falling through to the
    next source would start the service with the *wrong* credential rather than with none."""
    monkeypatch.setenv(SECRET_ID_FILE_ENV, str(tmp_path / "does-not-exist"))

    with pytest.raises(VaultUnavailable) as caught:
        load_secrets(_config(), _vault(_happy()))

    # Matching on the variable *name* alone was not enough, and the mutation harness said so: the
    # "no secret-id anywhere" message names it too, so the assertion passed against a version that
    # fell through silently. It has to match what distinguishes the two — that the file was found
    # and could not be read, which is a deployment problem rather than a missing configuration.
    assert "cannot be read" in str(caught.value)
    assert "does-not-exist" in str(caught.value), "the message does not name the path it tried"


def test_no_secret_id_at_all_refuses_rather_than_trying_anonymously() -> None:
    with pytest.raises(VaultUnavailable, match="secret-id"):
        load_secrets(_config(), _vault(_happy()))


def test_a_development_token_is_accepted_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """`make up` runs Vault in dev mode with a root token, so this path has to exist. It warns,
    which is what makes a deployment that reached production on one *visible* rather than merely
    possible."""
    monkeypatch.setenv(DEV_TOKEN_ENV, "root")
    with capture_logs() as entries:
        assert load_secrets(_config(role_id=""), _vault(_happy())) == STORED
    assert any(entry["event"] == "vault_dev_token_used" for entry in entries)


# == fail closed =================================================================================


def test_an_unreachable_vault_stops_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point. Falling back to the environment turns a broken secret store into a service
    that starts, looks healthy, and runs on whatever stale value happens to be set."""
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(VaultUnavailable, match="unreachable"):
        load_secrets(_config(), _vault(handler))


@pytest.mark.parametrize("status", [400, 403, 500, 503])
def test_a_refused_login_stops_the_process(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"errors": ["denied"]})

    with pytest.raises(VaultUnavailable, match=str(status)):
        load_secrets(_config(), _vault(handler))


def test_a_login_that_returns_no_token_stops_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 with an empty body would otherwise produce an empty token, and every subsequent read
    would fail with a permissions error pointing at the wrong thing."""
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"auth": {}})

    with pytest.raises(VaultUnavailable, match="no client token"):
        load_secrets(_config(), _vault(handler))


def test_a_missing_path_is_a_different_exception_from_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Vault is down" and "nobody has written that key yet" call for different actions by
    different people. One message covering both sends the reader to the wrong one."""
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"auth": {"client_token": "s.tok"}})
        return httpx.Response(404, json={"errors": []})

    with pytest.raises(SecretMissing, match="not an outage"):
        load_secrets(_config(), _vault(handler))


def test_a_read_that_fails_after_a_good_login_still_stops_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"auth": {"client_token": "s.tok"}})
        raise httpx.ReadTimeout("slow")

    with pytest.raises(VaultUnavailable):
        load_secrets(_config(), _vault(handler))


# == no Vault configured (FR-7) ==================================================================


def test_no_address_means_no_vault_and_no_error() -> None:
    """A laptop and the hermetic suite behave exactly as before. This is the *only* path that
    returns an empty mapping — every other outcome raises."""
    assert load_secrets(VaultConfig(), _vault(_happy())) == {}


def test_an_address_with_no_credential_is_not_configured() -> None:
    """Half a configuration is not a configuration. Trying anonymously would produce a 403 that
    reads as a permissions problem rather than as a missing role id."""
    assert load_secrets(VaultConfig(address=ADDRESS), _vault(_happy())) == {}


def test_the_vault_settings_themselves_come_from_the_environment() -> None:
    config = VaultConfig.from_env(
        {"VAULT_ADDR": ADDRESS, "VAULT_ROLE_ID": "r", "VAULT_MOUNT": "kv", "VAULT_PATH": "p"}
    )
    assert (config.address, config.role_id, config.mount, config.path) == (ADDRESS, "r", "kv", "p")


def test_an_empty_mount_falls_back_to_the_default_rather_than_an_empty_path() -> None:
    """`VAULT_MOUNT=` in a `.env` is a common way to "unset" something, and an empty mount would
    build `/v1//data/...` — a 404 that reads as a missing secret."""
    config = VaultConfig.from_env({"VAULT_ADDR": ADDRESS, "VAULT_MOUNT": "", "VAULT_PATH": ""})
    assert (config.mount, config.path) == ("secret", "aira")


# == precedence (FR-3) ===========================================================================


def test_vault_wins_over_the_environment() -> None:
    merged = resolve({"GOOGLE_API_KEY": "from-vault"}, env={"AIRA_GOOGLE_API_KEY": "from-env"})
    assert merged["AIRA_GOOGLE_API_KEY"] == "from-vault"


def test_the_environment_fills_what_vault_does_not_have() -> None:
    merged = resolve({"GOOGLE_API_KEY": "from-vault"}, env={"AIRA_POSTGRES_HOST": "db.internal"})
    assert merged["AIRA_POSTGRES_HOST"] == "db.internal"


@pytest.mark.parametrize("written", ["google_api_key", "GOOGLE_API_KEY", "AIRA_GOOGLE_API_KEY"])
def test_a_key_is_found_however_a_human_spelled_it(written: str) -> None:
    """A person writing keys into Vault uses whichever form they read in the documentation, and a
    secret that is *present but spelled differently* is indistinguishable from one that is
    missing — which is the most expensive kind of absent."""
    merged = resolve({written: "value"}, env={})
    assert merged["AIRA_GOOGLE_API_KEY"] == "value"


def test_a_key_in_neither_is_absent_rather_than_empty() -> None:
    """An empty credential authenticates as nobody and reads as a permissions problem. Absent is
    the honest state, and it is what lets a settings default apply."""
    merged = resolve({}, env={})
    assert "AIRA_GOOGLE_API_KEY" not in merged


# == values never surface (FR-6) =================================================================


def test_nothing_a_vault_read_logs_contains_a_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test the rest of this file is in service of. A log line is the single most likely place
    for a secret to escape, and the loader is deliberately chatty about *names*.

    Captured through structlog rather than `caplog`: these logs never reach the stdlib handler, so
    a `caplog` assertion would pass against a loader that printed every value — the emptiest kind
    of green.
    """
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)
    with capture_logs() as entries:
        load_secrets(_config(), _vault(_happy()))

    rendered = repr(entries)
    for value in (*STORED.values(), SECRET):
        assert value not in rendered

    # And it is chatty about the right things, or nobody could tell a rotation had taken effect.
    assert "GOOGLE_API_KEY" in rendered
    assert any(entry["event"] == "vault_secrets_loaded" for entry in entries)


def test_a_failure_message_does_not_echo_the_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vault's own error bodies can echo the request. Only the status is reported."""
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)

    def handler(request: httpx.Request) -> httpx.Response:
        # Vault's own error bodies can echo the request; this one does, on purpose.
        return httpx.Response(403, json={"errors": [f"invalid secret_id {SECRET}"]})

    with pytest.raises(VaultUnavailable) as caught:
        load_secrets(_config(), _vault(handler))
    assert SECRET not in str(caught.value)


def test_the_config_object_never_holds_the_secret_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is read where it is used and never stored, so it cannot reach a repr, a log line, or a
    settings object somebody pickles."""
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)
    config = VaultConfig.from_env({"VAULT_ADDR": ADDRESS, "VAULT_ROLE_ID": "r"})
    assert SECRET not in repr(config)


def test_the_client_token_does_not_reach_a_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ID_ENV, SECRET)
    with capture_logs() as entries:
        VaultClient(_config(), _vault(_happy())).login()
    assert "s.client-token" not in repr(entries)


def test_an_empty_variable_means_unset_rather_than_a_parse_error() -> None:
    """`${VAULT_TIMEOUT:-}` in a compose file is an empty string, not an absent one, and
    `float("")` raises — which took the whole stack down the first time these variables were
    passed through. The same trap `BaseAiraSettings._empty_means_unset` exists for."""
    config = VaultConfig.from_env(
        {
            "VAULT_ADDR": "http://vault:8200",
            "VAULT_TIMEOUT": "",
            "VAULT_MOUNT": "",
            "VAULT_PATH": "",
        }
    )

    assert config.timeout > 0
    # And the string fields keep their defaults rather than becoming empty.
    assert config.mount == "secret"
    assert config.path == "aira"
