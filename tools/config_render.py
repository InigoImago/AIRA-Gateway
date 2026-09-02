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
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

import compose_files
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


#: Written into the rendered file so drift from the source is detectable without a running stack.
STAMP_SOURCE = "# aira-config-source:"
STAMP_DIGEST = "# aira-config-sha256:"


def digest_of(path: Path) -> str:
    """A hash of the source, so an edited `.env` or a stale render is a fact rather than a guess."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def as_env_file(values: dict[str, str], source: Path) -> str:
    """The rendered environment, in the form Compose and `.env` readers take.

    Values are quoted whenever they are not a bare word, because a `;`-separated role map and a
    URL with a `#` in it both mean something else unquoted — and the file this writes is read by
    Docker Compose, which has its own opinions about both.

    **Stamped with where it came from and a hash of it.** The danger this answers is the one an
    integrator cannot see: a `.env` edited by hand after rendering, or a source edited without
    re-rendering, and the deployment quietly running on something nobody chose. `--verify` reads
    the stamp back.
    """
    lines = [
        f"# Generated by tools/config_render.py from {source.name}. Do not edit by hand:",
        "# `--verify` compares this file with its source and reports every difference.",
        f"{STAMP_SOURCE} {source}",
        f"{STAMP_DIGEST} {digest_of(source)}",
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


# ---------------------------------------------------------------------------------------------
# Verification — the half that makes the file authoritative rather than merely first
# ---------------------------------------------------------------------------------------------
#
# Rendering puts the file's values into `deploy/compose/.env`, and Compose then fills every gap
# from `${VAR:-default}`. That is a **silent** hierarchy: a value left empty, a variable the file
# does not name, a `.env` edited by hand afterwards, or a source edited without re-rendering all
# end the same way — the deployment runs on something nobody chose, and nothing says so.
#
# So the file is authoritative because a command can prove it is, and fails when it is not.

#: Variables the deployment is allowed to decide, each with the reason. **An entry is a decision.**
COMPOSE_DECIDES = {
    # Empty deliberately means *use the in-network default*: the issuer is what the browser saw,
    # the JWKS is fetched by the container, and those are two different addresses for one server.
    "AIRA_OIDC_JWKS_URI": "empty means the in-network default (see docker-compose.apps.yml)",
    # Two databases on one server; there is no second setting to name Management's with.
    "AIRA_POSTGRES_DB": "Management's second database is named by the deployment",
}


#: Written next to a rendered `.env`, and never a secret: it holds one path.
#:
#: **Why a second file.** The stamp inside `.env` answers "was this rendered, and from what". It
#: cannot answer the question that matters more — "was this deployment *ever* config-driven" —
#: because the evidence disappears with the file it lives in: replace `.env` wholesale with a
#: hand-written one and the stamp goes too, leaving something that looks exactly like the demo
#: path, which legitimately has no config file at all. Without this marker the check has one
#: rule for both, and either it cries wolf on every demo start-up — the surest way to be ignored
#: when it is finally right — or it stays quiet through the takeover it exists to catch.
MARKER = ".aira-config-source"


def marker_of(env_file: Path) -> Path:
    return env_file.resolve().parent / MARKER


def _env_pairs(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, raw = line.partition("=")
        out[name.strip()] = raw[1:-1] if len(raw) > 1 and raw[0] == raw[-1] == '"' else raw
    return out


def _effective_environment(compose_files: list[Path]) -> dict[str, set[str]] | None:
    """What each service would actually receive, or `None` when Docker is not available.

    `docker compose config` renders the interpolation the daemon would perform, so this is the
    real answer rather than a re-implementation of Compose's substitution rules — which is the
    point: a re-implementation would agree with itself and not with Docker.
    """
    try:
        import yaml as _yaml

        argv = ["docker", "compose"]
        for path in compose_files:
            argv += ["-f", str(path)]
        argv += ["--profile", "observability", "--profile", "demo", "config"]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    doc = _yaml.safe_load(result.stdout) or {}
    seen: dict[str, set[str]] = {}
    for service in (doc.get("services") or {}).values():
        for name, value in (service.get("environment") or {}).items():
            seen.setdefault(name, set()).add("" if value is None else str(value))
    return seen


def duplicated_keys(text: str) -> list[str]:
    """Keys the file defines more than once, with both line numbers.

    **Checked whatever the file's provenance**, because this is about the file disagreeing with
    itself rather than with a config source. Compose takes the *last* definition and says nothing
    about the first, so a value appended at the bottom silently beats the one near the top — and
    the one near the top is the one somebody goes back and reads.

    Found in a live `deploy/compose/.env` on 2026-09-02: `AIRA_BIND_HOST=127.0.0.1` at line 10,
    from the shipped example, and `AIRA_BIND_HOST=0.0.0.0` at line 123, appended later to make the
    stack reachable. The stack was reachable; the file said loopback where anybody looks; and
    rebuilding `.env` from the example took the override away with no sign that anything had been
    lost — which arrives as `server could not be reached` and sends you looking at Docker.
    """
    seen: dict[str, int] = {}
    found: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in seen:
            found.append(
                f"{key} is set on line {seen[key]} and again on line {number} — Compose uses the "
                f"second and ignores the first."
            )
        seen[key] = number
    return found


def verify(env_file: Path, compose_files: list[Path]) -> list[str]:
    """Every way the deployment could be running on something the config file did not say."""
    problems: list[str] = []
    if not env_file.is_file():
        return [f"{env_file} does not exist — nothing has been rendered."]

    text = env_file.read_text(encoding="utf-8")
    # **Before the provenance check, and never behind its early return.** A hand-made `.env` — the
    # demo path, and every operator who edited the example — takes the `note:` branch below and
    # was therefore checked for nothing at all. A key set twice is exactly the defect a hand-made
    # file acquires, because that is what appending to one does.
    duplicates = duplicated_keys(text)
    source_line = next((ln for ln in text.splitlines() if ln.startswith(STAMP_SOURCE)), None)
    if source_line is None:
        marker = marker_of(env_file)
        if not marker.is_file():
            # Not a finding. The demo path ships a hand-made `.env` on purpose and names no
            # config file, so there is nothing above it to disagree with.
            return [
                *duplicates,
                f"note: {env_file} was not rendered from a config file, and nothing claims it "
                "should have been — it is authoritative because nothing else is.",
            ]
        return [
            f"{env_file} carries no `{STAMP_SOURCE}` stamp, and "
            f"{marker.name} says this deployment is driven by "
            f"{marker.read_text(encoding='utf-8').strip()}. The rendered file has been replaced "
            "by one nobody rendered, so the config file no longer decides anything."
        ]
    source = Path(source_line.split(":", 1)[1].strip())
    if not source.is_file():
        return [*duplicates, f"{env_file} names {source} as its source, and that file is gone."]

    # Carried into the rendered path too: a rendered file that somebody appended to has the same
    # problem, and the appended line is precisely the one nobody re-renders.
    problems.extend(duplicates)

    # 1. the source changed, or the file was edited after rendering
    stamped = next((ln for ln in text.splitlines() if ln.startswith(STAMP_DIGEST)), "")
    if stamped.split(":", 1)[1].strip() != digest_of(source):
        problems.append(
            f"{source} has changed since {env_file} was rendered from it. Re-render, or the stack "
            "runs on the previous answer."
        )
    rendered = render(load(source))
    present = _env_pairs(text)
    for name, want in rendered.items():
        if name not in present:
            problems.append(f"{name}: named by {source.name} and missing from {env_file}.")
        elif present[name] != want:
            problems.append(
                f"{name}: {source.name} says {want!r}, {env_file} has {present[name]!r} — "
                "somebody edited the rendered file."
            )

    # 2. Compose filling a gap of its own, which is the silent case
    effective = _effective_environment(compose_files)
    if effective is None:
        problems.append(
            "note: Docker is not available, so what the containers would actually receive was not "
            "checked — only the file against its source."
        )
        return problems
    for name, want in rendered.items():
        if name in COMPOSE_DECIDES:
            continue
        values = effective.get(name)
        if values is None:
            problems.append(
                f"{name}: {source.name} sets it and no service receives it. The compose files do "
                "not take it from the environment, so the value in the file does nothing."
            )
        elif not any(v.strip().strip('"').lower() == want.strip().lower() for v in values):
            problems.append(
                f"{name}: {source.name} says {want!r}, the stack would use {sorted(values)} — a "
                "compose default is taking over."
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("config", type=Path, nargs="?", help="The installation's YAML file.")
    parser.add_argument("-o", "--out", type=Path, help="Write here instead of to stdout.")
    parser.add_argument(
        "--verify",
        type=Path,
        metavar="ENV_FILE",
        help=(
            "Check a rendered file against its source and against what the stack would actually "
            "use. Exits non-zero on any difference."
        ),
    )
    args = parser.parse_args(argv)

    if args.verify is not None:
        problems = verify(
            args.verify,
            # Every file that defines a service, `compose_files` deciding which those are: a
            # variable reaching *any* container is the question here, and after the showcase
            # split a two-file list would report the demo's own settings as reaching nothing.
            list(compose_files.ALL),
        )
        real = [p for p in problems if not p.startswith("note:")]
        for problem in problems:
            print(("  " if problem.startswith("note:") else "  ! ") + problem, file=sys.stderr)
        if real:
            print(
                f"{len(real)} difference(s) — the stack is not running what the config says.",
                file=sys.stderr,
            )
            return 1
        if not any(p.startswith("note: ") and "not rendered" in p for p in problems):
            print(f"{args.verify} matches its source, and the stack would use it.")
        return 0

    if args.config is None:
        parser.error("a config file is required unless --verify is given")

    try:
        values = render(load(args.config))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    body = as_env_file(values, args.config)
    if args.out:
        args.out.write_text(body, encoding="utf-8")
        marker = marker_of(args.out)
        marker.write_text(f"{args.config}\n", encoding="utf-8")
        print(f"{len(values)} setting(s) -> {args.out}")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
