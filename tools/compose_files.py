"""Which Compose file is which — decided once, because a split is only safe if nobody guesses.

**The stack is described by three files and they are not interchangeable.** On 2026-08-26
`docker-compose.apps.yml` was 625 lines, and roughly a third of it — a development Keycloak realm,
a `-dev` Vault refilled on every start, five seeded demo accounts — was there for the showcase.
Anybody deploying AIRA onto their own Keycloak, Vault and Postgres had to work out which half
applied to them. So the showcase moved into `docker-compose.showcase.yml`.

A split like that is cheap to make and expensive to get wrong, because **sixteen places named the
files by hand**: the Makefile, `tools/config_render.py`, `tools/stack_addresses.py` and thirteen
tests. Each one would have had to be found. The ones that were missed would not fail loudly —
they would quietly check two files where three now exist, and report that a variable reaches no
container when it reaches one in the file they did not read.

Same rule as `tools/stack_addresses.py`, one layer up: **one place decides, everybody asks.**
`tools/tests/test_one_owner_for_the_stack_addresses.py` already fails on a stray literal address;
`tools/tests/test_the_core_stack_carries_no_demo.py` fails on a demo service that reappears in the
core file.

Which list to ask for:

- `CORE` — infrastructure plus the application processes. **This is the product**: what a real
  deployment runs, and what a reader should be able to read end to end without skipping demo
  scaffolding. Usable on its own; `make up-apps` runs exactly this.
- `SHOWCASE` — `CORE` plus the demo provisioning. `make up-full` and `make showcase` run this.
- `ALL` — the same three files. Named apart from `SHOWCASE` because the *reason* differs: a check
  that asks "does any container receive this variable" must read every file that defines one, and
  that question is not about the demo.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_DIR = ROOT / "deploy" / "compose"

INFRA = COMPOSE_DIR / "docker-compose.yml"
APPS = COMPOSE_DIR / "docker-compose.apps.yml"
SHOWCASE_ONLY = COMPOSE_DIR / "docker-compose.showcase.yml"

#: What a real deployment runs.
CORE: tuple[Path, ...] = (INFRA, APPS)

#: The demo, which is `CORE` with provisioning layered on top.
SHOWCASE: tuple[Path, ...] = (INFRA, APPS, SHOWCASE_ONLY)

#: Every file that defines a service, for checks that must not miss one.
ALL: tuple[Path, ...] = SHOWCASE

#: An overlay for trying something **alongside** the stack — a SIEM, a host on your network, a
#: second exporter. `make up-lab` is the only thing that adds it.
#:
#: **Deliberately in none of the lists above, and named here so that is a decision rather than an
#: oversight.** It is not `CORE`, because a real deployment does not run experiments; it is not
#: `SHOWCASE`, because the walkthrough must be the same everywhere; and it is not `ALL`, because
#: `ALL` answers *"does any container receive this variable"* about the **product's** settings, and
#: this file's variables (`LAB_*`) are Compose knobs that deliberately live outside the `AIRA_*`
#: contract — putting them in that list would make a check about the product answer about a
#: laboratory.
#:
#: The rule this encodes is `tools/tests/test_the_lab_is_never_part_of_the_stack.py`, and it exists
#: for the reason the module docstring gives: a fourth file nobody registered is one that thirteen
#: tests quietly do not read.
LAB: tuple[Path, ...] = (COMPOSE_DIR / "docker-compose.lab.yml",)

#: Services that exist only for the demo. The core file must contain none of them — a rule with a
#: test, because the cheapest way to undo this split is to add "just one" demo service back where
#: the anchors happen to be.
DEMO_ONLY = ("keycloak-init", "vault-init", "management-seed")


def args(files: tuple[Path, ...]) -> list[str]:
    """`-f a -f b …`, in the order Compose must read them."""
    return [arg for path in files for arg in ("-f", str(path))]
