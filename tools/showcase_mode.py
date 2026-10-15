"""Switch the showcase between strict data mode and the per-use-case settings (`FRD-627`).

    uv run python tools/showcase_mode.py on       # or off, or status, or key

The mode is decided when read, by both planes, so the same stack shows the same requests, figures
and findings both ways — which is the point of a demonstration: the works council sees *one*
system, and what changes when one setting changes. `make showcase-strict-on` and
`make showcase-strict-off` call this and then recreate the containers whose environment changed.

**It touches one line of `deploy/compose/.env` and prints none of the others**: that file holds the
installation's real credentials. A `.env` rendered from a configuration file (`config_render.py`
stamps it) is refused rather than edited, because `--verify` would then report the edit as drift:
the mode belongs in that YAML (`core.strict_data_mode`), rendered again.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: The file the functions below edit. Read when called, never bound as a parameter's default: a
#: default is fixed when the module loads, and a test pointing this elsewhere would still write the
#: installation's real file.
ENV_FILE = ROOT / "deploy" / "compose" / ".env"
NAME = "AIRA_STRICT_DATA_MODE"

#: The key telemetry's pseudonyms are made with. The showcase draws one per machine, once, so one
#: person keeps one pseudonym across restarts — never a published value: the key is what keeps a
#: pseudonym from being hashed back to a name.
KEY_NAME = "AIRA_PSEUDONYM_SECRET"

#: What `tools/config_render.py` writes at the top of a `.env` it generated.
RENDERED_STAMP = "# aira-config-source:"

#: The container whose environment says which mode the running stack is in.
GATEWAY_CONTAINER = "gateway"

USAGE = "usage: showcase_mode.py on|off|status|key"


class Refused(Exception):
    """The `.env` is not this tool's to edit."""


def configured(env_file: Path | None = None) -> bool:
    """The mode the `.env` asks for. Absent or commented out is the default: on."""
    env_file = env_file or ENV_FILE
    if not env_file.exists():
        return True
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == NAME:
            return value.strip().strip("\"'").lower() not in {"false", "0", "no", "off"}
    return True


def set_mode(strict: bool, env_file: Path | None = None) -> None:
    """Write ``AIRA_STRICT_DATA_MODE`` into ``env_file``, replacing an active line or appending one.

    A commented-out example line is left as it is: it documents the setting, and the active line is
    what Compose reads.
    """
    env_file = env_file or ENV_FILE
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    if RENDERED_STAMP in text:
        raise Refused(
            f"{env_file} was rendered from a configuration file. Set core.strict_data_mode there "
            "and render it again (tools/config_render.py), so --verify keeps agreeing with it."
        )
    wanted = f"{NAME}={'true' if strict else 'false'}"
    lines = text.splitlines()
    for index, line in enumerate(lines):
        name, separator, _ = line.partition("=")
        if separator and name.strip() == NAME:
            lines[index] = wanted
            break
    else:
        lines.append(wanted)
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_pseudonym_secret(env_file: Path | None = None) -> bool:
    """Draw ``AIRA_PSEUDONYM_SECRET`` into ``env_file`` unless it has one. Returns whether it wrote.

    A rendered `.env` is left alone: its secrets come from Vault, which is where this one belongs.
    """
    env_file = env_file or ENV_FILE
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    if RENDERED_STAMP in text:
        return False
    for line in text.splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == KEY_NAME and value.strip():
            return False
    lines = [*text.splitlines(), f"{KEY_NAME}={secrets.token_hex(32)}"]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def running(container: str = GATEWAY_CONTAINER, stack: str = "aira") -> bool | None:
    """The mode the running gateway was started with, or ``None`` when it is not running."""
    result = subprocess.run(  # noqa: S603 - fixed argv
        ["docker", "inspect", "--format", "{{json .Config.Env}}", f"{stack}-{container}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    for entry in json.loads(result.stdout or "[]") or []:
        name, _, value = str(entry).partition("=")
        if name == NAME:
            return value.strip().lower() not in {"false", "0", "no", "off"}
    return True


def describe(strict: bool) -> str:
    if strict:
        return "strict data mode ON — a person is seen by Global Administrator and IT Security only"
    return "strict data mode OFF — each use case's own content setting decides"


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in {"on", "off", "status", "key"}:
        print(USAGE, file=sys.stderr)
        return 2
    if argv[0] == "key":
        if ensure_pseudonym_secret():
            # Said, never shown: the value is a credential.
            print(f"drew a random {KEY_NAME} into deploy/compose/.env, for stable pseudonyms")
        return 0
    if argv[0] == "status":
        live = running(stack=os.environ.get("AIRA_STACK", "aira"))
        print(f"deploy/compose/.env asks for: {describe(configured())}")
        if live is None:
            print("running gateway: not running")
        else:
            print(f"running gateway:          {describe(live)}")
            if live != configured():
                print("They differ: run make showcase-strict-on or make showcase-strict-off.")
        return 0
    try:
        set_mode(argv[0] == "on")
    except Refused as refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return 1
    print(f"deploy/compose/.env now asks for: {describe(argv[0] == 'on')}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
