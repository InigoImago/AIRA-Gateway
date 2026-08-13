"""Whatever this repository can start, `make down` can stop.

**Starting and stopping are not mirror images, and treating them as one is the bug.** An `up`
target is entitled to start a subset — `make up` brings up infrastructure, `make showcase` brings
up everything, `make verify-up` brings up a model. A *stopping* target has no such freedom: it has
to deal with whatever is there, which is the union of everything any of them could have left
behind. Those were two different sets and `down` had the smaller one.

Measured on 2026-08-13, on a machine somebody had run `make showcase` on. `make down` named
`docker-compose.yml` and the observability profile, so it knew **8** of the **21** services that
were running. It removed the infrastructure and left `gateway`, `management`, `gateway-consumer`,
`management-relay`, `frontend` and `gateway-retention` up. They do not merely survive — the
application services carry `restart: unless-stopped` while the infrastructure does not, so they
also come *back*. The consumer had been crash-looping for eight hours against the Postgres that
`make down` deleted out from under it.

The part that makes it this project's own recurring defect rather than an oversight: compose could
not remove its own network, said so — `Network aira Resource is still in use` — and the target
**exited 0**. An operation that is accepted, appears to have worked, and did not happen.

So the rule is checked here rather than remembered:

**Every file, every profile.** Both are needed and neither substitutes for the other. Tested at the
time: `--remove-orphans` over both files removed the six application containers and *left* `ollama`
and `management-seed`, because a service behind an inactive profile is in the model and is
therefore not an orphan. Only naming the profile reaches those.

**The profile list has a counterpart.** `--profile "*"` would be self-maintaining and needs Compose
v2.24; on anything older it matches a profile literally named `*`, which is silently this same bug
again. A written list is version-proof and fails loudly instead — as long as something compares it
to the profiles that exist, in both directions, which is what the last two tests do.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
COMPOSE = ROOT / "deploy" / "compose"

#: The Makefile variables that act on **whatever is running** rather than starting a chosen subset.
#: A target that stops, removes, inspects or restarts has to see the whole stack; one that starts
#: something may legitimately see part of it.
LIFECYCLE_VARIABLE = "COMPOSE_ALL"

#: The targets that must use it, and what each would get wrong with a narrower view.
LIFECYCLE_TARGETS = {
    "down": "leaves containers running, and cannot remove the network they are attached to",
    "destroy": "deletes the volumes while the services still using them keep running",
    "ps": "reports a partial stack, which is indistinguishable from a stopped one",
    "logs": "silently omits the services somebody is looking for",
    "restart": "restarts some of the stack against infrastructure that just moved",
}

#: Removing containers must also remove those whose service is gone from the configuration —
#: renamed, or deleted between two runs. Compose leaves those behind without it and calls them
#: orphans in a warning nobody reads.
REMOVE_ORPHANS = "--remove-orphans"


def _makefile() -> str:
    return MAKEFILE.read_text()


def _recipe(target: str) -> str:
    """The command lines of one Makefile target."""
    match = re.search(
        rf"^{re.escape(target)}:[^\n]*\n((?:\t[^\n]*\n|#[^\n]*\n)*)", _makefile(), re.M
    )
    assert match, f"no target named {target!r} in the Makefile"
    return match.group(1)


def _declared_profiles() -> set[str]:
    profiles: set[str] = set()
    for name in ("docker-compose.yml", "docker-compose.apps.yml"):
        document = yaml.safe_load((COMPOSE / name).read_text())
        for definition in (document.get("services") or {}).values():
            profiles.update((definition or {}).get("profiles") or [])
    return profiles


def _lifecycle_definition() -> str:
    """The `COMPOSE_ALL := …` assignment, line continuations joined."""
    match = re.search(rf"^{LIFECYCLE_VARIABLE} :=((?:[^\n]*\\\n)*[^\n]*)\n", _makefile(), re.M)
    assert match, f"{LIFECYCLE_VARIABLE} is no longer defined in the Makefile"
    return match.group(1).replace("\\\n", " ")


def test_the_makefile_still_looks_the_way_this_file_reads_it() -> None:
    """The guard's own failure mode. Every assertion below greps the Makefile, and a pattern that
    stops matching would make them pass by comparing nothing — which is how the thing they check
    went unnoticed in the first place."""
    assert _lifecycle_definition().strip()
    assert all(_recipe(target).strip() for target in LIFECYCLE_TARGETS)


def test_every_lifecycle_target_sees_the_whole_stack() -> None:
    wrong = {
        target: why
        for target, why in LIFECYCLE_TARGETS.items()
        if f"$({LIFECYCLE_VARIABLE})" not in _recipe(target)
        # An alias delegating to another target of this set inherits its view.
        and not re.search(rf"^{target}:\s*(?:{'|'.join(LIFECYCLE_TARGETS)})\b", _makefile(), re.M)
    }

    assert not wrong, (
        "these targets act on a narrower view of the stack than can be started:\n  "
        + "\n  ".join(f"{target}: {why}" for target, why in sorted(wrong.items()))
        + f"\n\nUse $({LIFECYCLE_VARIABLE}), which names every compose file and every profile. A "
        "stopping target does not get to choose its subset — it meets whatever an `up` target left."
    )


def test_removing_containers_also_removes_the_ones_the_configuration_forgot() -> None:
    missing = [target for target in ("down", "destroy") if REMOVE_ORPHANS not in _recipe(target)]

    assert not missing, (
        f"{missing} remove containers without {REMOVE_ORPHANS}, so a service that was renamed or "
        "deleted since it was started stays up. Compose mentions it in a warning and exits 0."
    )


def test_the_lifecycle_view_names_every_profile_that_exists() -> None:
    """The direction that bites: a profile added to a compose file and not to the Makefile is a set
    of services `make down` cannot see, and nothing about starting them would fail."""
    named = set(re.findall(r"--profile (\S+)", _lifecycle_definition()))
    unreachable = sorted(_declared_profiles() - named)

    assert not unreachable, (
        f"these profiles gate services that {LIFECYCLE_VARIABLE} cannot reach: {unreachable}. "
        "Whatever `make showcase` or `make verify-up` can start, `make down` has to be able to "
        "stop — including the parts of it that only exist behind a profile."
    )


def test_the_lifecycle_view_names_no_profile_that_does_not_exist() -> None:
    """The other direction, which fails quietly rather than loudly. Compose accepts an unknown
    profile name without complaint, so a stale one reads as coverage and is none — and the day
    somebody deletes the last service behind a profile, the Makefile keeps claiming to reach it."""
    named = set(re.findall(r"--profile (\S+)", _lifecycle_definition()))
    stale = sorted(named - _declared_profiles())

    assert not stale, (
        f"{LIFECYCLE_VARIABLE} names profiles that no service declares: {stale}. Compose ignores "
        "an unknown profile silently, so this reads as coverage of something that is not there."
    )
