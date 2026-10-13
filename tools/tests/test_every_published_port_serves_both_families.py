"""A published port is published on IPv4 **and** IPv6, or half the callers get nothing.

On 2026-08-19 the console was unreachable from outside the machine with `ERR_CONNECTION_RESET`,
and every check from inside was green: `docker compose ps` healthy, `curl localhost:4200` a 200,
the served HTML correct. nginx's own access log settled it — **no request from the browser had
ever arrived**. The forwarder in front carried `::1 4200 -> 4200`, the browser resolved `localhost`
to `::1`, and the machine behind it had fourteen IPv4 listeners and zero IPv6 ones. The connection
was accepted on the near side and reset on the far side, before any of this project's code saw it.

The trap is Docker's, and it is worth stating because `bindv6only=0` makes it look impossible: the
userland proxy opens **one socket per published entry**, not one dual-stack socket. Measured here
rather than assumed — `-p "[::]:15200:80"` alone answered on `[::1]` and *failed* on `127.0.0.1`;
`-p "0.0.0.0:15200:80"` alone did the reverse; both entries together answered on both.

Two lessons this file exists to keep, and neither is about IPv6:

- **A reset is not a refusal.** Something accepted, so the search starts at the wrong end — every
  diagnosis went looking for a problem on the *host*, because the sandbox side was demonstrably up.
- **Every check ran over IPv4**, and each one was green and true. The failure lived in the family
  nobody tested, which is the same shape as a test whose setup never reaches the path it is named
  after (`LESSONS.md` §7): green for a reason unrelated to the question.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import compose_files

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (*compose_files.ALL,)

_V4 = re.compile(r'^\s*- "\$\{AIRA_BIND_HOST:-127\.0\.0\.1\}:(?P<rest>[^"]+)"')
_V6 = re.compile(r'^\s*- "\[\$\{AIRA_BIND_HOST6:-::1\}\]:(?P<rest>[^"]+)"')


def _published() -> tuple[list[str], list[str]]:
    """What each family publishes, as the `<port>:<container port>` tail of every entry."""
    v4: list[str] = []
    v6: list[str] = []
    for path in COMPOSE:
        for line in path.read_text().splitlines():
            if match := _V4.match(line):
                v4.append(match.group("rest"))
            elif match := _V6.match(line):
                v6.append(match.group("rest"))
    return v4, v6


def test_there_are_published_ports_to_check() -> None:
    """A guard on the guard: two empty lists are equal, and would report perfect agreement."""
    v4, v6 = _published()

    assert len(v4) > 10, v4


def test_every_port_is_published_on_both_families() -> None:
    """Both directions. An IPv4 entry without its IPv6 twin is the defect above; an IPv6 entry
    without its IPv4 twin is the same failure for anyone whose resolver prefers A records."""
    v4, v6 = _published()

    assert sorted(v4) == sorted(v6), (
        "These ports are published on one address family only, and Docker opens one socket per "
        "entry — so the other family reaches a machine with nothing listening and the caller sees "
        "a **reset**, not a refusal:\n"
        f"  IPv4 only: {sorted(set(v4) - set(v6))}\n"
        f"  IPv6 only: {sorted(set(v6) - set(v4))}"
    )


def test_the_two_bind_variables_default_to_the_same_reach() -> None:
    """`127.0.0.1` and `::1` are the same decision written twice: loopback, because these files
    publish credentials. A wildcard default on one family would quietly undo the other's care."""
    infra = COMPOSE[0].read_text()

    assert "${AIRA_BIND_HOST:-127.0.0.1}" in infra
    assert "${AIRA_BIND_HOST6:-::1}" in infra
    for wildcard in ("AIRA_BIND_HOST:-0.0.0.0", "AIRA_BIND_HOST6:-::}"):
        assert wildcard not in infra, (
            f"the default binds {wildcard} — every service in this stack would be offered to the "
            "network, including the ones whose passwords are printed in `.env.example`"
        )


def test_the_example_env_explains_both() -> None:
    """An operator who needs the stack reachable from elsewhere has to set **two** variables, and
    will set the one they remember. The file that documents the first documents the second."""
    example = (ROOT / "deploy" / "compose" / ".env.example").read_text()

    assert "AIRA_BIND_HOST=" in example
    assert "AIRA_BIND_HOST6=" in example, (
        "`.env.example` names the IPv4 bind host and not the IPv6 one, so somebody following it "
        "gets a stack that is reachable over one family and reset over the other"
    )


# ---- and binds where the convention says ---------------------------------------------------------
#
# "Every published port binds to loopback" is the convention `docker-compose.yml` opens with, and
# the tests above only looked at lines that already began with a bind variable. An entry written as
# Compose's plain `"5000:5000"` binds every interface on both families and was invisible to all of
# them — the open Postgres or dev-mode Vault the convention exists to prevent.

#: How each family's entry must begin: the variable, defaulting to loopback.
BOUND = ("${AIRA_BIND_HOST:-127.0.0.1}:", "[${AIRA_BIND_HOST6:-::1}]:")
BOUND_HOSTS = ("${AIRA_BIND_HOST:-127.0.0.1}", "${AIRA_BIND_HOST6:-::1}")


def _unbound(document: dict) -> list[str]:
    """Every `ports` entry of every service that does not bind through one of the two variables."""
    found: list[str] = []
    for name, service in (document.get("services") or {}).items():
        for entry in (service or {}).get("ports") or []:
            if isinstance(entry, dict):
                bound = str(entry.get("host_ip", "")) in BOUND_HOSTS
            else:
                bound = str(entry).startswith(BOUND)
            if not bound:
                found.append(f"{name}: {entry!r}")
    return found


def _tracked_compose_files() -> list[Path]:
    """Every Compose file in the repository, overlays included — not only the ones a stack runs."""
    listed = subprocess.run(
        ["git", "ls-files", "deploy/compose/*.yml"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [ROOT / name for name in listed]


def test_every_published_port_binds_through_the_bind_variables() -> None:
    files = _tracked_compose_files()
    assert len(files) >= len(compose_files.ALL), files

    unbound = [
        f"{path.relative_to(ROOT)}  {entry}"
        for path in files
        for entry in _unbound(compose_files.load(path))
    ]

    assert not unbound, (
        "These publish a port without `AIRA_BIND_HOST` / `AIRA_BIND_HOST6`, so they bind every "
        "interface whatever the operator chose:\n  " + "\n  ".join(unbound)
    )


def test_an_entry_that_binds_everywhere_is_caught() -> None:
    """The guard broken on purpose: the short form, an explicit wildcard, and the long form."""
    document = {
        "services": {
            "probe": {
                "ports": [
                    "5000:5000",
                    "[::]:5001:5001",
                    {"target": 5002, "published": 5002},
                    "${AIRA_BIND_HOST:-127.0.0.1}:5003:5003",
                    {"target": 5004, "published": 5004, "host_ip": "${AIRA_BIND_HOST6:-::1}"},
                ]
            }
        }
    }

    assert len(_unbound(document)) == 3, _unbound(document)
