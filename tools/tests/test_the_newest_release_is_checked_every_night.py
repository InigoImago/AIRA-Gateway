"""The nightly check of the newest release (`FRD-154`, `known-vulnerabilities.yml`).

A workflow is configuration, so nothing here runs it on GitHub. What reading can check is what its
answer depends on:
- it runs every night, and on demand;
- it reads the newest Release's own documents;
- it fails when something is to act on;
- it keeps its report when it fails;
- it can write nothing.

The rules every workflow is held to — pinned actions, no token left in a clone, nothing written —
are in `test_a_release_is_published_only_behind_the_gates.py`.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
NIGHTLY = ROOT / ".github/workflows/known-vulnerabilities.yml"

#: `yaml.safe_load` reads the key `on:` as the boolean `True`.
ON = True


def _workflow() -> dict:
    return yaml.safe_load(NIGHTLY.read_text())


def _steps() -> list[dict]:
    return _workflow()["jobs"]["newest-release"]["steps"]


def _step(fragment: str) -> dict:
    for step in _steps():
        haystack = f"{step.get('name', '')} {step.get('uses', '')} {step.get('run', '')}"
        if fragment in haystack:
            return step
    raise AssertionError(f"no step mentioning {fragment!r}")


def _names() -> list[str]:
    return [str(step.get("name", step.get("uses", ""))) for step in _steps()]


def test_it_runs_every_night_and_on_demand_and_on_nothing_else() -> None:
    """Every night, because the data changes every day; on demand, to see an acceptance take effect
    before the night does. Not on a push: its answer depends on a database, not on the commit."""
    triggers = _workflow()[ON]

    assert set(triggers) == {"schedule", "workflow_dispatch"}
    (schedule,) = triggers["schedule"]
    minute, hour, day, month, weekday = schedule["cron"].split()
    assert minute.isdigit() and hour.isdigit(), "not once a day at a fixed time"
    assert (day, month, weekday) == ("*", "*", "*"), "not every night"


def test_it_can_write_nothing() -> None:
    """Grype's installer is a third party's action; beside it, nothing may be writable."""
    assert _workflow()["permissions"] == {"contents": "read"}
    assert all("permissions" not in job for job in _workflow()["jobs"].values())


def test_it_reads_the_documents_the_newest_release_attached(tmp_path: pathlib.Path) -> None:
    """**Run, not read.** The Release GitHub calls the latest, its documents and their checksums,
    and nothing rebuilt; with `gh` replaced by a stand-in that answers like it."""
    find = _step("Find the newest release")
    download = _step("Download what it contains")
    env_file = tmp_path / "env"
    calls = tmp_path / "calls"
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" >> "{calls}"\necho v1.2.3\n')
    gh.chmod(0o755)
    environment = {
        "PATH": f"{gh.parent}:/usr/bin:/bin",
        "GITHUB_ENV": str(env_file),
        "RUNNER_TEMP": str(tmp_path),
    }

    subprocess.run(["bash", "-e", "-c", find["run"]], check=True, env=environment)  # noqa: S603, S607
    assert env_file.read_text() == "TAG=v1.2.3\n"
    subprocess.run(  # noqa: S603
        ["bash", "-e", "-c", download["run"]],  # noqa: S607
        check=True,
        env={**environment, "TAG": "v1.2.3"},
    )
    arguments = calls.read_text().splitlines()
    assert arguments[:4] == ["release", "view", "--json", "tagName"]
    download_call = arguments[arguments.index("download") - 1 :]
    assert download_call[:3] == ["release", "download", "v1.2.3"]
    assert "aira-*.cdx.json" in download_call and "aira-*.cdx.json.sha256" in download_call
    assert f"{tmp_path}/sboms" in download_call
    for step in (find, download):
        assert step["env"] == {
            "GH_TOKEN": "${{ github.token }}",
            "GH_REPO": "${{ github.repository }}",
        }


def test_a_release_named_like_anything_but_a_version_stops_the_night(
    tmp_path: pathlib.Path,
) -> None:
    """The tag was chosen by whoever pushed it, and the step writes it into `$GITHUB_ENV`, where a
    newline would set any variable a later step reads. It is refused before it is written."""
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/bash\nprintf "v1.2.3\\nBASH_ENV=/tmp/x\\n"\n')
    gh.chmod(0o755)
    env_file = tmp_path / "env"

    finished = subprocess.run(  # noqa: S603
        ["bash", "-e", "-c", _step("Find the newest release")["run"]],  # noqa: S607
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "GITHUB_ENV": str(env_file)},
        capture_output=True,
        text=True,
    )

    assert finished.returncode != 0
    assert not env_file.exists()


def test_it_fails_when_something_is_to_act_on_and_keeps_the_report_when_it_does() -> None:
    """Without `--report-only` the tool exits 1 on a finding to act on and 2 on a check not made,
    and the run fails: GitHub's mail of a failed scheduled run is the alarm. The report is kept
    either way, since the run that fails is the one whose report somebody reads."""
    check = _step("release_vulnerabilities.py")
    run = str(check["run"])
    keep = _step("actions/upload-artifact")

    assert "--report-only" not in run
    assert 'python tools/release_vulnerabilities.py "$TAG" --grype "$GRYPE"' in run
    assert '--sboms "$RUNNER_TEMP/sboms"' in run
    assert "--accepted .github/accepted-vulnerabilities.toml" in run
    assert '--summary "$GITHUB_STEP_SUMMARY"' in run
    assert check["env"]["GRYPE"] == "${{ steps.grype.outputs.cmd }}"
    assert keep["if"] == "${{ !cancelled() }}"
    assert (
        keep["with"]["path"] == "${{ runner.temp }}/report" and '--out "$RUNNER_TEMP/report"' in run
    )


def test_the_steps_come_in_the_order_their_answers_are_needed() -> None:
    names = _names()

    order = [
        "Find the newest release",
        "Download what it contains",
        "Install Grype",
        "Check it against tonight's vulnerability data",
        "Keep the report",
    ]
    assert [name for name in names if name in order] == order


def test_two_runs_never_race() -> None:
    """A run asked for by hand while the night's is going waits for it."""
    concurrency = _workflow()["concurrency"]

    assert concurrency["cancel-in-progress"] is False
    assert re.fullmatch(r"[\w-]+", concurrency["group"])
