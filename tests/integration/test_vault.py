"""Secrets, against the Vault in the stack (FRD-116).

The hermetic suite proves the loader. What only shows up here is whether it agrees with a **real
Vault** about the things a mock cannot disagree about: the KV-v2 envelope's double `data`, the
AppRole endpoint's actual shape, what a token that lacks the policy really answers, and the fact
that the settings classes pick the value up at all.

The AppRole cases configure the auth method themselves and tear it down, so the suite is
re-runnable and does not depend on a fixture somebody set up by hand months ago.
"""

from __future__ import annotations

import json
import subprocess
import uuid

import pytest
import stack_addresses

from aira_common.config import BaseAiraSettings, VaultSource
from aira_common.secrets import (
    SECRET_ID_ENV,
    SecretMissing,
    VaultClient,
    VaultConfig,
    VaultUnavailable,
    load_secrets,
)

pytestmark = pytest.mark.integration

VAULT_ADDR = stack_addresses.url("vault")
ROOT_TOKEN = "root"


def _vault(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the Vault CLI inside its container — the same way an operator would."""
    return subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            f"VAULT_ADDR={stack_addresses.url('vault')}",
            "-e",
            f"VAULT_TOKEN={ROOT_TOKEN}",
            "aira-vault",
            "vault",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture(autouse=True)
def _reachable() -> None:
    result = _vault("status")
    if result.returncode not in (0, 2):  # 2 = sealed, still a reachable Vault
        pytest.skip("no Vault in the stack — run `make up`")


@pytest.fixture(autouse=True)
def _no_cached_secrets() -> None:
    """The settings source caches deliberately (`FRD-116` FR-5). A test that inherited another
    test's cache would pass while reading nothing."""
    VaultSource.reset()
    yield
    VaultSource.reset()


@pytest.fixture
def secret_path(monkeypatch: pytest.MonkeyPatch):
    """A unique KV path, written and removed, so the suite leaves the store as it found it."""
    path = f"aira-itest-{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("VAULT_ADDR", VAULT_ADDR)
    monkeypatch.setenv("VAULT_TOKEN", ROOT_TOKEN)
    monkeypatch.setenv("VAULT_PATH", path)
    monkeypatch.delenv("VAULT_ROLE_ID", raising=False)
    monkeypatch.delenv(SECRET_ID_ENV, raising=False)
    yield path
    _vault("kv", "metadata", "delete", f"secret/{path}")


# == the envelope a mock cannot get wrong ========================================================


def test_a_secret_written_by_the_cli_is_read_by_the_loader(secret_path: str) -> None:
    """KV-v2 nests the payload under `data.data`, and a mock returns whatever it was told to.
    Only a real Vault can confirm the loader unwraps the *actual* envelope."""
    _vault("kv", "put", f"secret/{secret_path}", "GOOGLE_API_KEY=live-value", "OTHER=second")

    secrets = load_secrets()

    assert secrets["GOOGLE_API_KEY"] == "live-value"
    assert secrets["OTHER"] == "second"


def test_a_path_that_does_not_exist_is_a_missing_secret_not_an_outage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Vault is down" and "nobody wrote that key" call for different actions by different people.
    Against a real server this distinction rests on a 404 rather than on our own mock."""
    monkeypatch.setenv("VAULT_ADDR", VAULT_ADDR)
    monkeypatch.setenv("VAULT_TOKEN", ROOT_TOKEN)
    monkeypatch.setenv("VAULT_PATH", f"nothing-here-{uuid.uuid4().hex[:8]}")
    monkeypatch.delenv("VAULT_ROLE_ID", raising=False)

    with pytest.raises(SecretMissing):
        load_secrets()


def test_an_address_with_nothing_listening_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property the whole feature rests on, against a real socket rather than a raised
    exception we chose: a configured Vault that cannot be reached stops the process."""
    monkeypatch.setenv("VAULT_ADDR", "http://127.0.0.1:1")
    monkeypatch.setenv("VAULT_TOKEN", ROOT_TOKEN)
    monkeypatch.delenv("VAULT_ROLE_ID", raising=False)

    with pytest.raises(VaultUnavailable):
        load_secrets()


def test_a_token_without_the_policy_is_refused_rather_than_reading_nothing(
    secret_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A permissions failure must not look like an empty secret. Vault answers 403 here, and a
    loader that treated any non-200 as "no keys" would start the service with none of them."""
    _vault("kv", "put", f"secret/{secret_path}", "KEY=value")
    created = _vault("token", "create", "-policy=default", "-ttl=5m", "-format=json")
    if created.returncode != 0:
        pytest.skip(f"could not mint a restricted token: {created.stderr[:120]}")
    token = json.loads(created.stdout)["auth"]["client_token"]

    monkeypatch.setenv("VAULT_TOKEN", token)
    with pytest.raises((VaultUnavailable, SecretMissing)):
        load_secrets()


# == AppRole, which is how a deployment actually authenticates ===================================


@pytest.fixture
def approle(secret_path: str, monkeypatch: pytest.MonkeyPatch):
    """A real AppRole with a policy scoped to this test's path, removed afterwards.

    Worth setting up rather than skipping: the dev-token path is the one every developer uses and
    the AppRole path is the one every deployment uses, so testing only the first would leave the
    mechanism that matters covered by unit tests alone.
    """
    role = f"aira-itest-{uuid.uuid4().hex[:8]}"
    policy = f"""path "secret/data/{secret_path}" {{ capabilities = ["read"] }}"""

    written = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"VAULT_ADDR={stack_addresses.url('vault')}",
            "-e",
            f"VAULT_TOKEN={ROOT_TOKEN}",
            "aira-vault",
            "vault",
            "policy",
            "write",
            role,
            "-",
        ],
        input=policy,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if written.returncode != 0:
        pytest.skip(f"could not write a policy: {written.stderr[:160]}")

    if _vault("auth", "enable", "approle").returncode != 0:
        # Already enabled by an earlier run — fine, and not a reason to skip.
        pass
    _vault("write", f"auth/approle/role/{role}", f"token_policies={role}", "token_ttl=10m")

    role_id = json.loads(
        _vault("read", "-format=json", f"auth/approle/role/{role}/role-id").stdout
    )["data"]["role_id"]
    secret_id = json.loads(
        _vault("write", "-force", "-format=json", f"auth/approle/role/{role}/secret-id").stdout
    )["data"]["secret_id"]

    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    monkeypatch.setenv("VAULT_ROLE_ID", role_id)
    yield role, role_id, secret_id

    _vault("delete", f"auth/approle/role/{role}")
    _vault("policy", "delete", role)


def test_an_approle_login_reads_the_secret(
    secret_path: str, approle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path every deployment takes. The unit tests prove the request shape; this proves Vault
    agrees with it."""
    _role, _role_id, secret_id = approle
    _vault("kv", "put", f"secret/{secret_path}", "GOOGLE_API_KEY=via-approle")
    monkeypatch.setenv(SECRET_ID_ENV, secret_id)

    assert load_secrets()["GOOGLE_API_KEY"] == "via-approle"


def test_a_secret_id_from_a_mounted_file_works_the_same(
    secret_path: str, approle, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A Kubernetes projected volume is a file on a tmpfs. The credential arriving with a trailing
    newline is the normal case, not an edge one."""
    _role, _role_id, secret_id = approle
    _vault("kv", "put", f"secret/{secret_path}", "GOOGLE_API_KEY=via-file")
    file = tmp_path / "secret-id"
    file.write_text(f"{secret_id}\n")
    monkeypatch.setenv("VAULT_SECRET_ID_FILE", str(file))

    assert load_secrets()["GOOGLE_API_KEY"] == "via-file"


def test_a_wrong_secret_id_stops_the_process_without_echoing_it(
    secret_path: str, approle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vault's own error body echoes the request. Only the status is reported — and the assertion
    is on the *message*, because that is what reaches a log aggregator."""
    wrong = "00000000-0000-0000-0000-000000000000"
    monkeypatch.setenv(SECRET_ID_ENV, wrong)

    with pytest.raises(VaultUnavailable) as caught:
        load_secrets()
    assert wrong not in str(caught.value)


def test_an_approle_may_not_read_a_path_outside_its_policy(
    secret_path: str, approle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The policy is what makes least privilege real rather than intended: the same credential
    that reads this test's path must fail on another one."""
    _role, _role_id, secret_id = approle
    monkeypatch.setenv(SECRET_ID_ENV, secret_id)
    monkeypatch.setenv("VAULT_PATH", "some-other-application")

    with pytest.raises((VaultUnavailable, SecretMissing)):
        load_secrets()


# == the settings classes actually use it ========================================================


class _Probe(BaseAiraSettings):
    """A settings class with one field, so the source can be tested without a whole service."""

    google_api_key: str = "from-default"


def test_a_settings_field_takes_its_value_from_vault(
    secret_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loader working and the *settings* using it are two different claims. This is the
    second one, and it is the one a deployment depends on."""
    _vault("kv", "put", f"secret/{secret_path}", "GOOGLE_API_KEY=chosen-by-vault")
    monkeypatch.setenv("AIRA_GOOGLE_API_KEY", "chosen-by-env")

    assert _Probe().google_api_key == "chosen-by-vault"


def test_the_environment_still_fills_what_vault_does_not_hold(
    secret_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _vault("kv", "put", f"secret/{secret_path}", "SOMETHING_ELSE=x")
    monkeypatch.setenv("AIRA_GOOGLE_API_KEY", "chosen-by-env")

    assert _Probe().google_api_key == "chosen-by-env"


def test_an_explicit_argument_still_wins_over_both(
    secret_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Init arguments stay first, or every test that constructs settings would be testing the
    deployment instead of the code."""
    _vault("kv", "put", f"secret/{secret_path}", "GOOGLE_API_KEY=chosen-by-vault")
    assert _Probe(google_api_key="explicit").google_api_key == "explicit"


def test_vault_is_read_once_no_matter_how_many_settings_objects_exist(
    secret_path: str,
) -> None:
    """FR-5: Vault is a startup dependency and never a request-path one. If the number of reads
    tracked the number of settings objects, a service that constructs one per request would put an
    availability dependency on Vault exactly where the FRD says it must not be."""
    _vault("kv", "put", f"secret/{secret_path}", "GOOGLE_API_KEY=once")

    reads = 0
    real = VaultClient.read

    def counting(self, token):  # noqa: ANN001, ANN202
        nonlocal reads
        reads += 1
        return real(self, token)

    VaultClient.read = counting  # type: ignore[method-assign]
    try:
        for _ in range(5):
            assert _Probe().google_api_key == "once"
    finally:
        VaultClient.read = real  # type: ignore[method-assign]

    assert reads == 1, f"Vault was read {reads} times for five settings objects"


def test_no_vault_configured_leaves_everything_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-7. A laptop and the hermetic suite must behave exactly as before this feature existed —
    which is also why every other test in this file sets `VAULT_ADDR` explicitly."""
    for name in ("VAULT_ADDR", "VAULT_TOKEN", "VAULT_ROLE_ID", SECRET_ID_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AIRA_GOOGLE_API_KEY", "from-env-only")

    assert load_secrets(VaultConfig.from_env()) == {}
    assert _Probe().google_api_key == "from-env-only"
