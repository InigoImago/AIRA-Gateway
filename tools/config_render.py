"""Turn an installation's YAML into the environment both planes already read.

## Why a renderer and not a settings source

AIRA's configuration contract is `AIRA_*` environment variables, with Vault ranked above them
(`FRD-116`). A YAML file could have been a fourth settings source — and would then be a *second*
answer to "where does this value come from", read at a different moment on each plane. The whole
point of this file is to be the one place an integrator edits, so it renders **into** the existing
contract rather than beside it:

    uv run python tools/config_render.py config/my-installation.yaml -o deploy/compose/.env

## The shape

Each section is a prefix; each key completes it. `postgres.host` is `AIRA_POSTGRES_HOST`. Two
sections are special and both are documented in the example:

- `core:` — keys used unprefixed, for settings that belong to no system (`AIRA_CURRENCY`).
- `vault:` — emitted as `VAULT_*`, because those are read before any settings object exists.

Nothing here maps names by hand. A hand-written table between YAML and settings is a third place
to forget a field, and `test_the_config_examples_are_real.py` checks the result against the real
settings classes in both directions instead.

## Secrets are refused, not requested

A value that authenticates belongs in Vault. Asking for that in a comment is what `LESSONS.md`
calls a written-down danger; this raises instead. The list is the same one `tools/vault_setup.py`
knows, plus the two Vault credentials themselves.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

#: Emitted under their own names because they are read before the settings exist.
VAULT_SECTION = "vault"
#: Its keys are settings that belong to no external system.
CORE_SECTION = "core"
#: Prose, not configuration: a list of what this file deliberately does not hold.
DESCRIPTIVE_SECTIONS = {"secrets"}

#: Refused outright. Every one of these is a credential, and the file that holds them is Vault.
FORBIDDEN = {
    "AIRA_SECRET_KEY",
    "AIRA_POSTGRES_PASSWORD",
    "AIRA_VERTEX_SERVICE_ACCOUNT_JSON",
    "AIRA_VERTEX_CREDENTIALS",
    "AIRA_VERTEX_API_KEY",
    "AIRA_GOOGLE_API_KEY",
    "AIRA_AZURE_API_KEY",
    "AIRA_FOUNDRY_API_KEY",
    "AIRA_OPENAI_API_KEY",
    "AIRA_KAFKA_SASL_PASSWORD",
    "AIRA_DIRECTORY_CLIENT_SECRET",
    "VAULT_SECRET_ID",
    "VAULT_TOKEN",
}


class ConfigError(Exception):
    """The file describes something that cannot be rendered. Always names the key."""


def _scalar(value: Any) -> str:
    """One YAML value as one environment value.

    `True` becomes `true`, not `True`: these are read by Pydantic on one plane and by Django on the
    other, and only the lower-case spelling is understood by both. A list becomes a comma-joined
    string, which is the form every list-shaped setting here already parses.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(_scalar(item) for item in value)
    return str(value)


def render(document: dict[str, Any]) -> dict[str, str]:
    """The environment this document describes, as `{NAME: value}`."""
    if not isinstance(document, dict):
        raise ConfigError("the file's top level must be a mapping of sections")

    out: dict[str, str] = {}
    for section, body in document.items():
        if section in DESCRIPTIVE_SECTIONS:
            continue
        if body is None:
            continue
        if not isinstance(body, dict):
            raise ConfigError(f"section '{section}' must be a mapping, not {type(body).__name__}")
        for key, value in body.items():
            if isinstance(value, dict):
                raise ConfigError(
                    f"'{section}.{key}' is a mapping. This file is two levels deep: a section, "
                    "then its settings."
                )
            if section == VAULT_SECTION:
                name = f"VAULT_{key}".upper()
            elif section == CORE_SECTION:
                name = f"AIRA_{key}".upper()
            else:
                name = f"AIRA_{section}_{key}".upper()
            if name in FORBIDDEN:
                raise ConfigError(
                    f"'{section}.{key}' would set {name}, which is a credential. It belongs in "
                    "HashiCorp Vault (`FRD-116`) — see the `secrets:` section of the examples."
                )
            out[name] = _scalar(value)
    return out


def load(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    return document or {}


def as_env_file(values: dict[str, str], source: Path) -> str:
    """The rendered environment, in the form Compose and `.env` readers take.

    Values are quoted whenever they are not a bare word, because a `;`-separated role map and a
    URL with a `#` in it both mean something else unquoted — and the file this writes is read by
    Docker Compose, which has its own opinions about both.
    """
    lines = [
        f"# Generated by tools/config_render.py from {source.name}. Do not edit by hand.",
        "#",
        "# Secrets are deliberately absent: they come from HashiCorp Vault (`FRD-116`), or — for",
        "# the one credential Vault cannot supply — from VAULT_SECRET_ID / VAULT_SECRET_ID_FILE.",
        "",
    ]
    for name in sorted(values):
        value = values[name]
        needs_quotes = value == "" or any(ch in value for ch in " #\"'$;,|")
        lines.append(f"{name}=" + (f'"{value}"' if needs_quotes else value))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("config", type=Path, help="The installation's YAML file.")
    parser.add_argument("-o", "--out", type=Path, help="Write here instead of to stdout.")
    args = parser.parse_args(argv)

    try:
        values = render(load(args.config))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    body = as_env_file(values, args.config)
    if args.out:
        args.out.write_text(body, encoding="utf-8")
        print(f"{len(values)} setting(s) -> {args.out}")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
