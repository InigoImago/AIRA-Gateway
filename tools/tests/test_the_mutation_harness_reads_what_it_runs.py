"""The mutation harness's engine: what it does to the source, and what it concludes from a run.

`make mutants` edits real source files and reports a verdict per property, so two of its failure
modes are worse than a missing test. It can **damage the tree** — restore the wrong text, or leave a
mutant behind — and it can **report a catch that is not one**: a mutant that does not parse or
build fails every test that loads it, whatever the tests check.

Each property below was broken in the harness first:

- an interrupted run's mutant made another anchor match twice, and the anchor check — which ran
  before recovery — then refused to start and left the mutant on disk;
- recovery wrote the pre-crash snapshot over whatever was there, a developer's later fix included,
  and so did the restore after every mutation;
- `--only A1,B2` was refused, `--only` alone raised `IndexError`, and `--help` was "unknown";
- a run that hung was waited for indefinitely;
- every selection was handed to pytest, so a console spec could not guard anything.

Everything runs against a scratch `ROOT`; no test here edits the repository.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import mutation_check as harness
import pytest

PASSED, FAILED = harness.Outcome.PASSED, harness.Outcome.FAILED
TIMED_OUT, BROKEN = harness.Outcome.TIMED_OUT, harness.Outcome.BROKEN

#: What `ng test` prints, as recorded from real runs: Vitest's summary carries colour codes.
NG_PASSED = "\x1b[2m Test Files \x1b[22m \x1b[1m\x1b[32m1 passed\x1b[39m\x1b[22m\x1b[90m (1)\n"
NG_FAILED = " Test Files  1 failed (1)\n      Tests  2 failed | 54 passed (56)\n"
NG_BUILD_ERROR = (
    "✘ [ERROR] TS2322: Type 'string' is not assignable to type 'number'.\n"
    "\nApplication bundle generation failed. [2.104 seconds]\n"
)
SPEC = "management/frontend/src/app/x.spec.ts"


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `ROOT` of its own, so the journal and every source edited here are scratch files."""
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    monkeypatch.setattr(harness, "JOURNAL", tmp_path / ".mutation-journal.json")
    monkeypatch.setattr(harness, "FRONTEND", tmp_path / "management" / "frontend")
    return tmp_path


def _mutation(ident: str, path: str, old: str, new: str, tests: str = "t.py") -> harness.Mutation:
    return harness.Mutation(ident, f"property {ident}", path, old, new, tests)


def _journal(path: str, original: str, mutant: str) -> None:
    harness._write_journal(
        {
            "ident": "X",
            "path": path,
            "original": original,
            "mutated_sha256": harness._digest(mutant),
        }
    )


# ---- recovery and restore ---------------------------------------------------------------------


def test_a_mutant_left_by_a_killed_run_is_put_back(sandbox: Path) -> None:
    (sandbox / "src.py").write_text("limit = 100\n")
    _journal("src.py", original="limit = 10\n", mutant="limit = 100\n")

    harness._recover()

    assert (sandbox / "src.py").read_text() == "limit = 10\n"
    assert not harness.JOURNAL.exists()


def test_a_file_edited_since_the_crash_is_left_as_it_is(sandbox: Path) -> None:
    """The fix a developer made after the crash is the newest text there is."""
    (sandbox / "src.py").write_text("limit = 1000  # the fix\n")
    _journal("src.py", original="limit = 10\n", mutant="limit = 100\n")

    with pytest.raises(SystemExit) as stopped:
        harness._recover()

    assert stopped.value.code == 2
    assert (sandbox / "src.py").read_text() == "limit = 1000  # the fix\n"
    assert harness.JOURNAL.exists(), "the original is in the journal and must stay there"


def test_a_journal_that_cannot_be_read_stops_the_run_and_writes_nothing(sandbox: Path) -> None:
    (sandbox / "src.py").write_text("limit = 100\n")
    harness.JOURNAL.write_text('{"path": "src.py", "orig')

    with pytest.raises(SystemExit):
        harness._recover()

    assert (sandbox / "src.py").read_text() == "limit = 100\n"


def test_the_journal_is_written_whole_or_not_at_all(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    renamed: list[tuple[str, str]] = []
    real = os.replace

    def watching(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        renamed.append((Path(source).name, Path(target).name))
        real(source, target)

    monkeypatch.setattr(harness.os, "replace", watching)

    harness._write_journal({"path": "src.py", "original": "x"})

    assert renamed == [(".mutation-journal.json.tmp", ".mutation-journal.json")]
    assert json.loads(harness.JOURNAL.read_text()) == {"path": "src.py", "original": "x"}


def test_recovery_comes_before_the_checks_that_read_the_source(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While `X` is applied, `Y`'s anchor matches twice — so a run killed during `X` used to make
    the next run refuse to start, with the mutant still on disk."""
    original, mutant = "a = 1\nb = 2\n", "b = 2\nb = 2\n"
    (sandbox / "src.py").write_text(mutant)
    _journal("src.py", original, mutant)
    monkeypatch.setattr(
        harness,
        "MUTATIONS",
        [_mutation("X", "src.py", "a = 1", "b = 2"), _mutation("Y", "src.py", "b = 2", "b = 3")],
    )
    seen: list[str] = []

    def run(selection: str, timeout: float) -> tuple[harness.Outcome, str]:
        seen.append((sandbox / "src.py").read_text())
        return (PASSED if len(seen) == 1 else FAILED), ""

    monkeypatch.setattr(harness, "_run", run)

    assert harness.main(["--only", "Y"]) == 0
    assert seen == [original, "a = 1\nb = 3\n"]
    assert (sandbox / "src.py").read_text() == original


def test_a_file_changed_while_its_mutant_is_applied_is_not_overwritten(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (sandbox / "src.py").write_text("a = 1\n")
    monkeypatch.setattr(harness, "MUTATIONS", [_mutation("X", "src.py", "a = 1", "a = 2")])
    runs: list[str] = []

    def run(selection: str, timeout: float) -> tuple[harness.Outcome, str]:
        runs.append(selection)
        if len(runs) == 2:
            # Somebody's editor saves into the tree while the mutant is on disk.
            (sandbox / "src.py").write_text("a = 1\nb = 'an edit'\n")
            return FAILED, ""
        return PASSED, ""

    monkeypatch.setattr(harness, "_run", run)

    assert harness.main(["--only", "X"]) == 2
    assert (sandbox / "src.py").read_text() == "a = 1\nb = 'an edit'\n"
    assert harness.JOURNAL.exists()


def test_a_mutant_that_does_not_parse_is_never_counted_as_caught(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (sandbox / "src.py").write_text("def f():\n    return 1\n")
    monkeypatch.setattr(
        harness, "MUTATIONS", [_mutation("X", "src.py", "    return 1", "    return (")]
    )
    seen: list[str] = []

    def run(selection: str, timeout: float) -> tuple[harness.Outcome, str]:
        seen.append((sandbox / "src.py").read_text())
        return PASSED, ""

    monkeypatch.setattr(harness, "_run", run)

    assert harness.main(["--only=X"]) == 1
    assert seen == ["def f():\n    return 1\n"], "only the baseline ran; the mutant never did"
    assert "X    BROKEN" in capsys.readouterr().out


# ---- the command line -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv", [["--only=A1,B2"], ["--only", "A1,B2"], ["--only", "A1", "--only", "B2"]]
)
def test_every_spelling_of_a_subset_selects_it(argv: list[str]) -> None:
    assert harness._arguments(argv).only == {"A1", "B2"}
    assert harness._arguments([]).only is None


@pytest.mark.parametrize("argv", [["--only"], ["--only="], ["A1"], ["--frobnicate"]])
def test_a_command_line_that_selects_nothing_is_refused_without_a_traceback(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        harness._arguments(argv)

    assert stopped.value.code == 2
    assert "Traceback" not in capsys.readouterr().err


def test_help_is_answered(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        harness._arguments(["--help"])

    assert stopped.value.code == 0
    assert "--only" in capsys.readouterr().out


# ---- running a selection ----------------------------------------------------------------------


def test_a_run_that_hangs_is_stopped_with_everything_it_started(tmp_path: Path) -> None:
    """A mutant can make a test wait for ever. The run is stopped at its limit, and so is anything
    it started — a builder, a browser, a server a test brought up."""
    pid_file = tmp_path / "grandchild"
    script = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()

    code, output = harness._execute([sys.executable, "-c", script], tmp_path, timeout=3)

    assert code is None
    assert time.monotonic() - started < 30
    assert harness._classify(["pytest"], code, output)[0] is TIMED_OUT
    grandchild = int(pid_file.read_text())
    for _ in range(50):
        status = Path(f"/proc/{grandchild}/status")
        if not status.exists() or "\nState:\tZ" in status.read_text():
            break
        time.sleep(0.1)
    else:
        pytest.fail("the run's own subprocess outlived the time limit")


def test_a_selection_is_split_between_the_two_runners(sandbox: Path) -> None:
    commands = harness._commands(f"tools/tests/test_a.py {SPEC} gateway/tests/test_b.py")

    assert commands == [
        (
            [sys.executable, "-m", "pytest", "tools/tests/test_a.py", "gateway/tests/test_b.py"]
            + ["-x", "-q", "--no-cov"],
            sandbox,
        ),
        (
            [
                "npx",
                "ng",
                "test",
                "--watch=false",
                "--no-coverage",
                "--include",
                "src/app/x.spec.ts",
            ],
            sandbox / "management" / "frontend",
        ),
    ]


def test_a_selection_of_specs_alone_runs_no_pytest(sandbox: Path) -> None:
    commands = harness._commands(f"{SPEC} management/frontend/src/app/y.spec.ts")

    assert [command[:3] for command, _ in commands] == [["npx", "ng", "test"]]
    assert commands[0][0].count("--include") == 2


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (0, NG_PASSED, PASSED),
        (1, NG_FAILED, FAILED),
        (1, NG_BUILD_ERROR, BROKEN),
        (1, "", BROKEN),
        (0, "no test file matched the include", BROKEN),
    ],
)
def test_what_ng_test_says_is_read_from_what_it_prints(
    code: int, output: str, expected: harness.Outcome
) -> None:
    """Its exit code cannot tell a failing assertion from a mutant that did not compile: both are
    non-zero. Only the second must never count as a catch."""
    assert harness._classify(["npx", "ng", "test"], code, output)[0] is expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [(0, PASSED), (1, FAILED), (2, FAILED), (3, BROKEN), (4, BROKEN), (5, BROKEN), (127, BROKEN)],
)
def test_what_pytest_says_is_read_from_its_exit_code(code: int, expected: harness.Outcome) -> None:
    """1 and 2 are a failing test and a test module that failed to load; 4 and 5 are a usage
    error and an empty collection — a run that asked the tests nothing."""
    assert harness._classify([sys.executable, "-m", "pytest"], code, "")[0] is expected


def test_a_mixed_selection_is_green_only_when_both_runners_are(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answers: dict[str, tuple[int, str]] = {}
    monkeypatch.setattr(
        harness,
        "_execute",
        lambda command, cwd, timeout: answers["ng" if command[0] == "npx" else "pytest"],
    )
    selection = f"tools/tests/test_a.py {SPEC}"

    answers.update(pytest=(0, ""), ng=(0, NG_PASSED))
    assert harness._run(selection, 5)[0] is PASSED
    answers.update(pytest=(0, ""), ng=(1, NG_FAILED))
    assert harness._run(selection, 5)[0] is FAILED
    answers.update(pytest=(1, ""), ng=(0, NG_PASSED))
    assert harness._run(selection, 5)[0] is FAILED
    answers.update(pytest=(1, ""), ng=(1, NG_BUILD_ERROR))
    assert harness._run(selection, 5)[0] is BROKEN, "a mutant that does not build is no catch"


def test_a_console_mutant_that_does_not_build_is_reported_rather_than_caught(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = sandbox / "management" / "frontend" / "src" / "app" / "x.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export const x = 1;\n")
    monkeypatch.setattr(
        harness,
        "MUTATIONS",
        [_mutation("X", "management/frontend/src/app/x.ts", "x = 1;", "x: number = 'one';", SPEC)],
    )
    replies = iter([(0, NG_PASSED), (1, NG_BUILD_ERROR)])
    monkeypatch.setattr(harness, "_execute", lambda command, cwd, timeout: next(replies))

    assert harness.main(["--only", "X"]) == 1
    printed = capsys.readouterr().out
    assert "X    BROKEN" in printed
    assert "All 1 properties are defended" not in printed
    assert source.read_text() == "export const x = 1;\n"
