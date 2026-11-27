"""A process's identity is the image's: the release, the commit and the time it was built
(`FRD-157`).

The version lives in the release's tag and nowhere in the code; the release job hands it to the
image build. What a build was not told is said as such: `0.0.0+local` for no release, `None` for a
commit or a time — never `0.1.0`, the number every process stated before the images carried one.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aira_common import build


def test_a_build_that_was_told_nothing_is_no_release() -> None:
    identity = build.build_of({})

    assert identity == build.Build(
        version=build.LOCAL_VERSION, commit=None, time=None, number=None, branch=None
    )
    assert build.LOCAL_VERSION == "0.0.0+local"
    assert identity.commit_short is None


def test_a_release_build_says_its_version_commit_and_time() -> None:
    identity = build.build_of(
        {
            "AIRA_BUILD_VERSION": "0.1.18",
            "AIRA_GIT_COMMIT": "0123456789abcdef",
            "AIRA_BUILD_TIME": "2026-11-27T12:00:00Z",
        }
    )

    assert (identity.version, identity.commit, identity.time) == (
        "0.1.18",
        "0123456789abcdef",
        "2026-11-27T12:00:00Z",
    )
    assert identity.commit_short == "0123456"


@pytest.mark.parametrize(
    ("given", "said"),
    [
        # A build by hand may hand over the tag itself; the version is the same one.
        ("v0.1.18", "0.1.18"),
        (" 0.1.18 ", "0.1.18"),
        # A `v` that starts no number is part of what the build was told.
        ("vnext", "vnext"),
        ("", build.LOCAL_VERSION),
        ("   ", build.LOCAL_VERSION),
    ],
)
def test_the_version_is_the_tags_number(given: str, said: str) -> None:
    assert build.build_of({"AIRA_BUILD_VERSION": given}).version == said


@pytest.mark.parametrize(("given", "said"), [("42", 42), ("0", None), ("-1", None), ("x", None)])
def test_a_build_number_is_a_positive_number_or_none(given: str, said: int | None) -> None:
    assert build.build_of({"AIRA_BUILD_NUMBER": given}).number == said


def test_an_empty_fact_is_none_rather_than_an_empty_string() -> None:
    """An image built without a commit writes `AIRA_GIT_COMMIT=` — an empty value, not none."""
    identity = build.build_of({"AIRA_GIT_COMMIT": "", "AIRA_BUILD_TIME": "", "AIRA_GIT_BRANCH": ""})

    assert (identity.commit, identity.time, identity.branch) == (None, None, None)


def test_this_process_reads_its_environment_when_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIRA_BUILD_VERSION", "0.1.18")
    monkeypatch.setenv("AIRA_GIT_BRANCH", "main")

    assert (build.this_build().version, build.this_build().branch) == ("0.1.18", "main")


def test_the_statement_the_release_job_reads_is_the_identity(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("AIRA_BUILD_VERSION", "0.1.18")
    monkeypatch.setenv("AIRA_GIT_COMMIT", "abc")
    monkeypatch.setenv("AIRA_BUILD_TIME", "2026-11-27T12:00:00Z")

    assert build.main() == 0

    assert json.loads(capsys.readouterr().out) == {
        "version": "0.1.18",
        "commit": "abc",
        "time": "2026-11-27T12:00:00Z",
        "number": None,
        "branch": None,
    }


def test_every_name_the_identity_is_read_from_is_named_once() -> None:
    assert len(set(build.IDENTITY_VARIABLES)) == len(build.IDENTITY_VARIABLES) == 5
    assert all(name.startswith("AIRA_") for name in build.IDENTITY_VARIABLES)
