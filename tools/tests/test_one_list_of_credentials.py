"""Every list of "the secrets AIRA reads from Vault" is the settings classes' own answer.

Four hand-written lists used to say it, and they disagreed: `vault_setup.py --from-env` skipped
five real credentials — the Vertex service account, its API key, Foundry's key, the Kafka SASL
password and the directory client secret — so an operator who followed the runbook and then removed
the plaintext variables, as it told them to, took those integrations down. Three of the names the
lists did carry exist nowhere (`AIRA_VERTEX_SERVICE_ACCOUNT_JSON`, `AIRA_AZURE_API_KEY`,
`AIRA_OPENAI_API_KEY`), and the examples told integrators to store the Vertex JSON under the first
of them, where nothing reads it.

`tools/credential_settings.py` answers by shape from the settings classes. Everything else is
checked against it here, both ways, so a new credential is copied, refused, provisioned and
documented the day it is added.
"""

from __future__ import annotations

import re
from pathlib import Path

import config_render
import credential_settings
import pytest
import vault_setup
import yaml

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "config").glob("*.example.yaml"))
SHOWCASE = ROOT / "deploy" / "compose" / "docker-compose.showcase.yml"


def test_the_shape_finds_the_credentials_and_only_them() -> None:
    """A guard on the guard, and the one switch that only *mentions* credentials."""
    found = credential_settings.credentials()

    assert {"AIRA_SECRET_KEY", "AIRA_VERTEX_CREDENTIALS", "AIRA_DIRECTORY_CLIENT_SECRET"} <= found
    assert len(found) >= 8, sorted(found)
    assert "AIRA_CORS_ALLOW_CREDENTIALS" not in found, "a boolean is not a credential"
    assert found <= credential_settings.settings_names()


def test_vault_setup_copies_every_credential() -> None:
    assert set(vault_setup.KNOWN_SECRETS) == credential_settings.credentials()


def test_the_renderer_refuses_every_credential_and_the_two_that_open_vault() -> None:
    assert (
        credential_settings.credentials() | credential_settings.VAULT_CREDENTIALS
    ) == config_render.FORBIDDEN


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("vertex", "credentials"),
        ("vertex", "service_account_json"),
        ("openai", "api_key"),
        ("redis", "sentinel_password"),
        ("vault", "secret_id"),
    ],
)
def test_a_credential_is_refused_under_any_name_it_might_be_written_as(
    section: str, key: str
) -> None:
    """Including a name no setting declares: a key under a misremembered name is still a key."""
    with pytest.raises(config_render.ConfigError, match="credential"):
        config_render.render({section: {key: "s3cret"}})


def test_a_switch_that_mentions_credentials_is_still_rendered() -> None:
    assert config_render.render({"cors": {"allow_credentials": True}}) == {
        "AIRA_CORS_ALLOW_CREDENTIALS": "true"
    }


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_every_example_lists_exactly_the_credentials_vault_holds(example: Path) -> None:
    """The examples are what an integrator stores credentials *by*, so a wrong name in them is a
    secret put where nothing looks for it."""
    listed = set(yaml.safe_load(example.read_text())["secrets"]["in_vault"])

    assert listed == credential_settings.credentials(), (
        f"{example.name} lists {sorted(listed - credential_settings.credentials())} that are no "
        f"credential setting, and omits {sorted(credential_settings.credentials() - listed)}"
    )


def test_the_showcase_provisions_every_credential_it_is_given() -> None:
    """`vault-init` is shell inside a Compose file, so it cannot import the list — it is checked
    against it instead: the loop's names, and that each is actually passed into the container."""
    service = yaml.safe_load(SHOWCASE.read_text())["services"]["vault-init"]
    script = " ".join(service["command"])
    loop = re.search(r"for name in (.*?); do", script, re.S)
    assert loop, "the vault-init loop over the credential names is gone"
    looped = set(loop.group(1).replace("\\", " ").split())

    assert looped == credential_settings.credentials()
    assert credential_settings.credentials() <= set(service["environment"]), (
        "named in the loop and not passed into the container, so the loop always finds it empty"
    )
