"""`make ci` is what CI checks — checked, because both say so and they had drifted.

The workflow said every step calls the target a developer runs, and the Makefile called `make ci`
"what CI checks". Neither was true: the CI jobs write their commands out (so each installs its
toolchain once), CI built the console in **production** mode while `make lint-frontend` built it
for development — and only the production build applies the content policy that once disabled the
whole stylesheet — and CI never checked `proxy.conf.cjs`'s formatting. Each difference is a change
that passes locally and fails in CI, or the reverse.

So the rule is stated as what it can be — every command of the local targets appears in the job
that stands for them — and checked.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

#: Which job stands for which local targets.
JOBS = {"python": ("lint-py", "test-py"), "frontend": ("lint-frontend", "test-frontend")}


def _commands(text: str) -> set[str]:
    """Each command in `text`, one per line or `&&` link, without a leading `cd …`."""
    found: set[str] = set()
    for line in text.splitlines():
        for part in line.split("&&"):
            command = " ".join(part.split())
            if command and not command.startswith(("cd ", "@#", "#")):
                found.add(command)
    return found


def _recipe(target: str) -> set[str]:
    body = MAKEFILE.read_text().partition(f"\n{target}:")[2]
    lines = []
    for line in body.splitlines()[1:]:
        if not line.startswith("\t"):
            break
        lines.append(line.strip())
    return _commands("\n".join(lines))


def _job(name: str) -> set[str]:
    job = yaml.safe_load(WORKFLOW.read_text())["jobs"][name]
    return _commands("\n".join(str(step.get("run", "")) for step in job["steps"]))


@pytest.mark.parametrize("job", sorted(JOBS))
def test_every_command_of_the_local_targets_runs_in_ci(job: str) -> None:
    local = set().union(*(_recipe(target) for target in JOBS[job]))
    assert local, f"no commands found for {JOBS[job]} — the parse stopped matching"

    missing = sorted(local - _job(job))

    assert not missing, (
        f"`make {' '.join(JOBS[job])}` runs {missing}, and the `{job}` job does not — a change "
        "that passes one fails the other."
    )


def test_both_build_the_console_in_the_configuration_that_ships() -> None:
    """The one difference with a recorded cost: only the production build showed the CSP failure."""
    builds = [command for command in _recipe("lint-frontend") if command.startswith("npx ng build")]

    assert builds == ["npx ng build"], builds
    assert not any(re.search(r"--configuration\s+development", c) for c in _job("frontend"))
