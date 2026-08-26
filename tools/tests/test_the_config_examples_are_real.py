"""The `config/` examples describe the product, and not a product somebody remembers.

`config/*.example.yaml` is what an integrator edits: one file naming every external system AIRA can
be connected to, rendered into the `AIRA_*` environment both planes read. That makes it the same
kind of document as `docs/CONFIGURATION.md`, and it fails the same way — *"the copy that is read
every session stays true and the copy nobody opens rots"* — except worse, because this one is not
read, it is **run**. A key that does not exist is a setting somebody believes they configured.

So both directions, as everywhere else here:

1. every key an example renders is a real settings field (or one of the handful of names that are
   read outside the settings classes, listed with the reason);
2. every settings field is named by the examples, or listed below as deliberately absent.

The second is the one that catches the interesting case: a new external system is added, the
settings class grows, and the file an integrator fills in never learns about it.

## And that no example holds a credential

The renderer refuses the names it knows. This checks the examples against that refusal from the
outside, because a rule enforced only by the code that writes the file is a rule that stops
applying the moment somebody hand-edits one.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "config").glob("*.example.yaml"))

#: Read before, or outside, the settings classes. Each with the reason it is not a field.
NOT_A_SETTING = {
    # Vault's own client configuration: read by `aira_common.secrets` before any settings object
    # exists, which is the whole point — it is how the settings get their values.
    "VAULT_ADDR",
    "VAULT_MOUNT",
    "VAULT_PATH",
    "VAULT_ROLE_ID",
    "VAULT_NAMESPACE",
    "VAULT_TIMEOUT",
}

#: Settings an installation file deliberately does not carry, each with the reason.
NOT_IN_A_CONFIG_FILE = {
    # Secrets. The renderer refuses them by name; they come from Vault (`FRD-116`).
    "AIRA_SECRET_KEY",
    "AIRA_POSTGRES_PASSWORD",
    "AIRA_VERTEX_CREDENTIALS",
    "AIRA_VERTEX_API_KEY",
    "AIRA_GOOGLE_API_KEY",
    "AIRA_FOUNDRY_API_KEY",
    "AIRA_OPENAI_API_KEY",
    "AIRA_KAFKA_SASL_PASSWORD",
    "AIRA_DIRECTORY_CLIENT_SECRET",
    # Stamped by the build, not chosen by an installation.
    "AIRA_BUILD_NUMBER",
    "AIRA_BUILD_TIME",
    "AIRA_GIT_COMMIT",
    "AIRA_GIT_BRANCH",
    # Test-only, and a configuration file that offers it invites somebody to point a running
    # installation at a test database.
    "AIRA_TEST_DATABASE",
    # A date this project publishes, not a knob (`FRD-107`).
    "AIRA_KIRA_SUNSET",
}


def _settings_fields() -> set[str]:
    """Every `AIRA_*` both planes declare. Imported, never parsed — a regex over source would
    describe the file rather than the classes, and the classes are what read the environment."""
    for path in ("gateway/src", "libs/src", "management/backend/src"):
        candidate = str(ROOT / path)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    from aira_management.config.app_settings import ManagementSettings

    from aira_gateway.config import GatewaySettings

    return {
        f"AIRA_{name}".upper()
        for settings in (GatewaySettings, ManagementSettings)
        for name in settings.model_fields
    }


def _rendered(path: Path) -> dict[str, str]:
    sys.path.insert(0, str(ROOT / "tools"))
    from config_render import load, render

    return render(load(path))


def test_there_are_examples_to_check() -> None:
    """Without this the parametrised tests below would pass by never running."""
    assert EXAMPLES, "no config/*.example.yaml — every assertion here would describe nothing"
    assert len(_settings_fields()) > 50, "the settings classes did not import"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_every_key_an_example_renders_is_a_real_setting(example: Path) -> None:
    known = _settings_fields() | NOT_A_SETTING
    invented = sorted(name for name in _rendered(example) if name not in known)

    assert invented == [], (
        f"{example.name} renders {invented}, which no settings class declares. Somebody filling "
        "this in would set them and believe it took.\n\nEither the section or the key is "
        "misspelled — a section is a prefix and a key completes it — or the setting was removed "
        "from the product and the example never heard."
    )


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_no_example_carries_a_credential(example: Path) -> None:
    """Checked from outside the renderer, which refuses these by name.

    A rule enforced only by the code that writes a file stops applying the moment somebody edits
    the file by hand — and these files exist to be edited by hand.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    from config_render import FORBIDDEN

    held = sorted(set(_rendered(example)) & FORBIDDEN)

    assert held == [], f"{example.name} carries {held}. Those belong in Vault (`FRD-116`)."


def test_the_examples_between_them_name_every_setting() -> None:
    """The direction that catches a **new** external system.

    A settings class grows, an integrator's file never learns about it, and the omission is
    invisible: the product simply runs on a default nobody chose. Listed-and-excused is fine;
    silently missing is not.
    """
    named: set[str] = set()
    for example in EXAMPLES:
        named |= set(_rendered(example))

    missing = sorted(_settings_fields() - named - NOT_IN_A_CONFIG_FILE)

    assert missing == [], (
        f"the settings classes declare {missing}, and no example names them. Add each to a "
        "config example so an integrator can set it, or to NOT_IN_A_CONFIG_FILE with the reason "
        "an installation file should not carry it."
    )


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_an_example_renders_a_configuration_the_product_accepts(example: Path) -> None:
    """The test that makes this a working file rather than a document.

    Every assertion above is about **names**. This one builds both planes' settings objects out of
    what the example renders and lets them validate it — so a port that is not a number, a ratio
    outside its range, a role map the parser refuses, or a `security_protocol` that is spelled
    wrong fails here instead of at somebody's first boot.

    The environment is replaced rather than added to: the point is that the *file alone* describes
    a working installation. Merging the developer's own `AIRA_*` would let this pass on a machine
    that already had the missing piece set — the shape `LESSONS.md` records as *"it works on a
    machine that has already done the thing by hand"*.
    """
    rendered = _rendered(example)
    # The two credentials the file deliberately omits and the settings insist on outside `local`.
    # Supplied here, not in the file, which is exactly the division of labour it describes.
    supplied = dict(rendered)
    supplied.setdefault("AIRA_SECRET_KEY", "x" * 50)
    supplied.setdefault("AIRA_POSTGRES_PASSWORD", "not-a-real-password")

    from aira_management.config.app_settings import ManagementSettings

    from aira_gateway.config import GatewaySettings

    with mock.patch.dict(os.environ, supplied, clear=True):
        for settings_class in (GatewaySettings, ManagementSettings):
            # `_env_file=None`, or a developer's own `.env` is read on top and the example is no
            # longer what is being measured.
            settings = settings_class(_env_file=None)
            assert settings.environment == rendered["AIRA_ENVIRONMENT"]


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_a_rendered_file_reads_back_as_what_was_rendered(example: Path) -> None:
    """The `.env` form is what Compose consumes, and quoting is where it goes wrong.

    A role map is `;`-separated and a CORS origin holds a `//`: both mean something else to a shell
    unquoted. Read back with a parser rather than by eye, because "it looked right" is how a
    quoting bug ships.
    """
    sys.path.insert(0, str(ROOT / "tools"))
    from config_render import as_env_file

    values = _rendered(example)
    parsed: dict[str, str] = {}
    for line in as_env_file(values, example).splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, raw = line.partition("=")
        parsed[name] = raw[1:-1] if len(raw) > 1 and raw[0] == raw[-1] == '"' else raw

    assert parsed == values


#: Read by `aira_common.secrets.VaultConfig.from_env`, before any settings object exists.
SECRETS_LOADER = ROOT / "libs" / "src" / "aira_common" / "secrets.py"
#: The two Vault names an installation file must **not** carry: the secret-id is what authenticates
#: *to* Vault, and a dev token is the escape hatch `ADR-0007` allows for `local` only.
VAULT_CREDENTIALS = {"VAULT_SECRET_ID", "VAULT_SECRET_ID_FILE", "VAULT_TOKEN"}


def _vault_names_the_loader_reads() -> set[str]:
    """Every `VAULT_*` the secrets loader looks for, read out of the loader.

    Not a list kept here. A hand-written copy of what another module reads is a second definition,
    and this whole file exists because those disagree — see the settings-class import above, which
    is the same argument one module along.
    """
    text = SECRETS_LOADER.read_text(encoding="utf-8")
    return set(re.findall(r'"(VAULT_[A-Z_]+)"', text)) - VAULT_CREDENTIALS


def test_the_secrets_loader_is_where_this_expects_it() -> None:
    assert SECRETS_LOADER.is_file(), SECRETS_LOADER
    found = _vault_names_the_loader_reads()
    assert {"VAULT_ADDR", "VAULT_PATH"} <= found, sorted(found)


def test_the_examples_describe_the_secret_store_the_loader_reads() -> None:
    """**The hole a mutation sweep found**, and the reason it was invisible.

    `VAULT_*` is exempt from the reality check above because those names are read *before* the
    settings exist — correctly, and the exemption then made the whole section unguarded. Measured
    by deleting one key at a time from both examples and re-running this file: 179 of 186 mutations
    were caught, and the seven that were not were every key of `vault:`. The section that points at
    the secret store could have vanished from both files with nothing to say so.

    Checked against the loader itself, so a name it stops reading, or starts reading, shows up here
    rather than as an installation whose credentials silently came from the environment.
    """
    named: set[str] = set()
    for example in EXAMPLES:
        named |= set(_rendered(example))

    missing = sorted(_vault_names_the_loader_reads() - named)

    assert missing == [], (
        f"the secrets loader reads {missing} and no example names them. An integrator filling in "
        "one of these files would configure everything except where the credentials come from."
    )


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_an_example_describes_a_vault_the_loader_can_use(example: Path) -> None:
    """And that the values parse.

    `VaultConfig.from_env` turns `VAULT_TIMEOUT` into a float, outside any settings class — so a
    timeout that is not a number is a `ValueError` at start-up rather than a validation message.
    The same sweep found this: breaking that value was the seventh mutation nothing noticed.
    """
    for path in ("libs/src",):
        candidate = str(ROOT / path)
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    from aira_common.secrets import VaultConfig

    rendered = {k: v for k, v in _rendered(example).items() if k.startswith("VAULT_")}
    config = VaultConfig.from_env(rendered)

    assert config.address, f"{example.name} names no Vault address"
    assert config.timeout > 0, f"{example.name}: VAULT_TIMEOUT is not a usable number"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_no_example_carries_the_credential_that_authenticates_to_vault(example: Path) -> None:
    """The one secret Vault cannot supply, because it is how you reach Vault. It belongs in an
    environment variable or a projected volume — never in a file somebody copies around."""
    held = sorted(set(_rendered(example)) & VAULT_CREDENTIALS)

    assert held == [], (
        f"{example.name} carries {held}. That is what authenticates *to* Vault: an environment "
        "variable in simple deployments, `VAULT_SECRET_ID_FILE` on Kubernetes."
    )
