"""A service that comes back by itself may not depend on one that does not.

**Half a stack is the worst of the three states.** On 2026-08-20 a machine that had been running
for hours came back from a Docker restart with the console answering, both planes answering, and
nobody able to log in: every infrastructure container had `Exited (255)` at the same millisecond
(`16:20:08.93Z` — what a daemon shutdown looks like) and the gateway had started again three
seconds later. `docker-compose.apps.yml` has always said `restart: unless-stopped`;
`docker-compose.yml` said nothing at all, and Compose's default is `no`. So the half that needs
the other half is the half that came back, `gateway-consumer` crash-looped against an absent
Postgres, and the gateway's container reported `healthy` throughout — liveness is not readiness.

The asymmetry was **already written down**, in `test_compose_lifecycle_covers_the_stack.py`:
*"the application services carry `restart: unless-stopped` while the infrastructure does not, so
they also come back."* It was recorded there as an aggravating detail of the `make down` defect —
the one where stopping saw eight of twenty-one services. Nobody asked what the same asymmetry does
to a **host restart**, which is the more ordinary event of the two. A fact noticed in passing is
not a fact anybody is holding.

Two assertions, and neither uses a list of service names:

**Every service says what it wants.** A silent default is how this happened: nobody decided that
Postgres should stay down, it was decided by an absent line. Requiring the key makes adding a
service a moment where somebody answers the question.

**A restarting service's dependencies restart too**, transitively, over this file's own
`depends_on` graph. A **job** is transparent in that walk and is required to stay `no`: a
migration or a seed that re-ran on every boot would be a different bug. What makes something a job
is read off the graph as well — every edge pointing at it carries
`service_completed_successfully`, which is Compose's own way of saying *this one finishes*.
"""

from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "compose"
FILES = ("docker-compose.yml", "docker-compose.apps.yml")

#: What a service that is meant to survive a restart must say. Not `always`: an operator who ran
#: `docker stop` on one service has said something, and `always` would argue with them at the next
#: boot. `unless-stopped` is the policy the application file already chose, and one concept with
#: two spellings is the drift this whole file exists to stop.
SURVIVES = "unless-stopped"

#: What a service that finishes must say. Compose accepts the bare word; the files quote it,
#: because unquoted `no` is YAML's boolean `false` and has bitten every project that writes YAML.
FINISHES = ("no", False)


def _services() -> dict[str, dict]:
    """Both files merged the way Compose merges them, so a cross-file `depends_on` resolves."""
    merged: dict[str, dict] = {}
    for name in FILES:
        document = yaml.safe_load((COMPOSE / name).read_text())
        merged.update(document.get("services") or {})
    return merged


def _edges(body: dict) -> list[tuple[str, str | None]]:
    """`(target, condition)` for each `depends_on` entry, in either of the two spellings."""
    depends = body.get("depends_on") or {}
    if isinstance(depends, list):
        return [(target, None) for target in depends]
    return [
        (target, spec.get("condition") if isinstance(spec, dict) else None)
        for target, spec in depends.items()
    ]


def _jobs(services: dict[str, dict]) -> set[str]:
    """Services that **finish**, read off the graph rather than named here.

    Every edge pointing at a job carries `service_completed_successfully` — that is Compose's own
    statement that the thing terminates. A service nothing depends on that way is long-running as
    far as this model is concerned, whatever its name suggests.
    """
    pointed_at: dict[str, set[str | None]] = {}
    for body in services.values():
        for target, condition in _edges(body):
            pointed_at.setdefault(target, set()).add(condition)
    return {
        target
        for target, conditions in pointed_at.items()
        if conditions == {"service_completed_successfully"}
    }


def test_every_service_says_what_it_wants_after_a_restart() -> None:
    """A silent default is how the stack came back in halves.

    Compose defaults to `no`; nobody chose that for Postgres, and the absence of a line is not a
    decision anybody can be shown to have taken.
    """
    silent = sorted(name for name, body in _services().items() if "restart" not in body)

    assert not silent, (
        "these services do not say what should happen to them after a restart, so Compose decides "
        f"for them (`no`): {', '.join(silent)}"
    )


def test_a_service_that_comes_back_does_not_depend_on_one_that_stays_down() -> None:
    """The invariant, walked transitively — a job is stepped through, not stopped at.

    `gateway` does not name Postgres directly: it reaches it through `gateway-migrate`, which is a
    job. Stopping the walk at the job would have declared this stack sound while it was exactly
    the stack that came back without a database.
    """
    services = _services()
    jobs = _jobs(services)
    broken: list[str] = []

    for name, body in services.items():
        if body.get("restart") != SURVIVES:
            continue
        seen: set[str] = set()
        queue = [target for target, _ in _edges(body)]
        while queue:
            target = queue.pop()
            if target in seen:
                continue
            seen.add(target)
            dependency = services.get(target)
            if dependency is None:
                broken.append(f"{name} depends on {target}, which no compose file declares")
                continue
            queue.extend(reached for reached, _ in _edges(dependency))
            if target in jobs:
                continue
            if dependency.get("restart") != SURVIVES:
                broken.append(
                    f"{name} comes back by itself and needs {target}, which does not "
                    f"(restart={dependency.get('restart')!r})"
                )

    assert not broken, "\n  ".join(["the stack would come back in halves:", *sorted(set(broken))])


def test_a_job_is_not_restarted_when_it_has_finished() -> None:
    """The other half, and the reason the walk above steps *through* a job rather than demanding
    the same policy of it: a migration or a seed that re-ran on every boot is a different bug."""
    services = _services()
    wrong = sorted(
        f"{name} (restart={services[name].get('restart')!r})"
        for name in _jobs(services)
        if services[name].get("restart") not in FINISHES
    )

    assert not wrong, f"these finish and must not be restarted: {', '.join(wrong)}"


def test_there_are_services_of_both_kinds_to_compare() -> None:
    """A guard on the guard. Both assertions above pass vacuously over an empty set, and a parser
    that stopped matching would empty both at once — the shape `LESSONS.md` §1 records four times.
    """
    services = _services()
    jobs = _jobs(services)
    survivors = {name for name, body in services.items() if body.get("restart") == SURVIVES}

    assert len(jobs) >= 3, f"no jobs found in the compose model: {sorted(jobs)}"
    assert len(survivors) >= 10, (
        f"too few restarting services to be the whole stack: {len(survivors)}"
    )
    assert jobs.isdisjoint(survivors), f"a service cannot be both: {sorted(jobs & survivors)}"
