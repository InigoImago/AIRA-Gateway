"""Would both planes start with this configuration? Asked before anything is deployed.

**Why this is a command and not a paragraph.** `tools/config_render.py --verify` answers a
different question — does the *deployment* use what the file says. This one asks whether the file
says anything the product would refuse: an environment that is not `local` turns on nine or so
hardening checks per plane (`ADR-0015`), and the first time most of them are met is when a
container exits during a maintenance window, with a message nobody was watching for.

Both answers come from the product's own code, in a subprocess with only the rendered environment
in it. A re-implementation of `unsafe_settings` would agree with itself and not with the service.

Two outcomes are deliberately kept apart:

- **A credential the file does not carry.** `config_render.FORBIDDEN` is the list of names the
  renderer *refuses* to write, because they belong in Vault (`FRD-116`). A refusal naming one of
  those is not a hole in the configuration — it is the configuration being right, and Vault having
  the other half. Reported, and not counted against the file.
- **Everything else.** A missing audience, a wildcard `ALLOWED_HOSTS`, `PLAINTEXT` Kafka, an
  unnamed global-admin group: these are the file's own, and the exit code is theirs.

Vault itself is a third answer. If the file declares `VAULT_ADDR`, the settings classes try to use
it and **fail closed** when they cannot — which is correct at boot and useful here, because it is
the same failure, found before the deployment instead of during it.

    make config-check CONFIG=config/my-installation.yaml
    uv run python tools/config_check.py config/my-installation.yaml
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import config_render

ROOT = Path(__file__).resolve().parents[1]

#: One probe per plane: import the settings class, hand back what it refuses. Written as a program
#: rather than a shared module because it must run with a **clean** environment, and the only
#: honest way to get one is a process that never had ours.
_PROBE = """
import json, sys
sys.path[:0] = {paths!r}
try:
    from {module} import {settings_class} as S
    from {checker_module} import unsafe_settings
    print(json.dumps({{"ok": True, "problems": unsafe_settings(S())}}))
except Exception as exc:                      # noqa: BLE001 — the message is the finding
    print(json.dumps({{"ok": False, "error": f"{{type(exc).__name__}}: {{exc}}"}}))
"""

PLANES = {
    "gateway": {
        "paths": [str(ROOT / "gateway" / "src"), str(ROOT / "libs" / "src")],
        "module": "aira_gateway.config",
        "settings_class": "GatewaySettings",
        "checker_module": "aira_gateway.security",
    },
    "management": {
        "paths": [str(ROOT / "management" / "backend" / "src"), str(ROOT / "libs" / "src")],
        "module": "aira_management.config.app_settings",
        "settings_class": "ManagementSettings",
        "checker_module": "aira_management.config.security",
    },
}


def _ask(plane: str, environment: dict[str, str]) -> dict[str, object]:
    spec = PLANES[plane]
    probe = _PROBE.format(**spec)
    # `env=` and nothing else: the check is worthless if it inherits a value the deployment
    # will not have. PATH is kept because a subprocess without one cannot always start.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env={**environment, "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=str(ROOT),
        timeout=120,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {"ok": False, "error": (result.stderr or "no output").strip()[-400:]}
    return json.loads(result.stdout.splitlines()[-1])


#: Vault refusing is neither a pass nor the file's fault, so it gets an exit code of its own.
VAULT_UNAVAILABLE = 3


def check(config: Path, *, without_vault: bool = False) -> tuple[int, list[str]]:
    """`(exit code, lines to print)`.

    `1` — the file has problems of its own. `2` — it does not render. `3` — it declares a Vault
    this machine cannot use, so the question could not be asked; that is not the same as an answer,
    and reporting it as one would be the permissive stand-in this project keeps paying for.
    """
    lines: list[str] = []
    try:
        values = config_render.render(config_render.load(config))
    except config_render.ConfigError as exc:
        return 2, [f"error: {exc}"]

    address = values.get("VAULT_ADDR", "")
    if without_vault:
        values = {name: value for name, value in values.items() if not name.startswith("VAULT_")}

    lines.append(
        f"{config.name}: {len(values)} setting(s), environment "
        f"'{values.get('AIRA_ENVIRONMENT', 'local')}'"
        + (f", Vault at {address} ignored (--without-vault)" if without_vault and address else "")
    )
    own_problems = 0
    vault_refused: list[str] = []
    for plane in sorted(PLANES):
        answer = _ask(plane, values)
        if not answer.get("ok"):
            error = str(answer.get("error"))
            if "VaultUnavailable" in error or "Vault" in error and "secret-id" in error:
                vault_refused.append(f"  {plane}: {error}")
            else:
                own_problems += 1
                lines.append(f"  {plane}: could not be asked — {error}")
            continue
        problems = list(answer.get("problems") or [])
        if not problems:
            lines.append(f"  {plane}: accepts this configuration")
            continue
        # Split on the renderer's own credential list, so the two lists cannot drift apart.
        from_vault = [p for p in problems if any(name in p for name in config_render.FORBIDDEN)]
        theirs = [p for p in problems if p not in from_vault]
        own_problems += len(theirs)
        lines.append(f"  {plane}: {len(theirs)} to fix here, {len(from_vault)} for Vault")
        for problem in theirs:
            lines.append(f"    ! {problem}")
        for problem in from_vault:
            lines.append(f"    · {problem}")
    if vault_refused:
        lines.append("")
        lines.append(
            f"This file declares Vault at {address or '(unset)'}, and this machine cannot use it, "
            "so neither plane could be asked anything else. The same refusal would stop the "
            "deployment — which is why it is reported rather than treated as a pass:"
        )
        lines.extend(vault_refused)
        lines.append("")
        lines.append(
            "  Run this where the deployment's VAULT_SECRET_ID_FILE is readable, or pass "
            "--without-vault to check everything the file decides on its own."
        )
        return VAULT_UNAVAILABLE, lines

    if own_problems:
        marked = any(line.lstrip().startswith("·") for line in lines)
        lines.append("")
        lines.append(
            f"{own_problems} thing(s) this file has to answer for."
            + (
                " Lines marked `·` are credentials it deliberately does not carry — Vault "
                "supplies those, and they are not counted here."
                if marked
                else ""
            )
        )
    return (1 if own_problems else 0), lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path, help="a config/*.yaml to check")
    parser.add_argument(
        "--without-vault",
        action="store_true",
        help="drop every VAULT_* setting first, to check what the file decides on its own",
    )
    args = parser.parse_args(argv)
    code, lines = check(args.config, without_vault=args.without_vault)
    for line in lines:
        print(line, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
