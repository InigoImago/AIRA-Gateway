"""The version arithmetic a release is cut with (`FRD-137`, `ADR-0029`).

Hermetic on purpose: every test passes a list of tag *strings*, so none of this depends on what
this checkout happens to have tagged. The one thing that touches `git` is `tags_in`, and the only
property worth asserting about it is that it reports what `git tag --list` reports.

The defect this file exists to prevent is the ordering one. Tags are strings, `"v0.10.0" <
"v0.9.0"` is true in every string comparison there is, and a release cut from the lexicographic
maximum goes **backwards** the first time a minor number reaches ten — publishing `v0.10.1` as
`v0.9.1`, over an image that already exists. It appears once, long after the code was written, and
by then the wrong version is in somebody's deployment.
"""

from __future__ import annotations

import pytest
from release_version import FIRST, bump, latest, next_version, parse, render, tags_in


def test_a_release_tag_is_three_numbers_behind_a_v() -> None:
    assert parse("v1.2.3") == (1, 2, 3)
    assert parse("v0.0.0") == (0, 0, 0)
    assert parse("v10.20.30") == (10, 20, 30)
    # Whitespace, because `git tag --list` is read line by line and a stray space would otherwise
    # make a real tag invisible — which reads as "never released".
    assert parse("  v1.2.3\n") == (1, 2, 3)


@pytest.mark.parametrize(
    "tag",
    [
        "v1.2",  # two parts: a series, not a release
        "1.2.3",  # unprefixed
        "v1.2.3-rc1",  # a pre-release is not what this project publishes
        "v1.2.3.4",
        "va.b.c",
        "release-1.2.3",
        "v1.2.3+build7",
        "",
    ],
)
def test_what_is_not_a_release_tag_is_not_one(tag: str) -> None:
    """**Deliberately strict.** A looser pattern would make the next version depend on somebody's
    scratch tag: tag a spike `v9.9.9-wip`, and the next real release jumps to `v9.9.10`.

    Surrounding whitespace is the one thing that is *not* rejected — it is stripped — and the test
    above asserts that separately. Mixing the two here would need an escape clause, and a
    parametrised test with an exception for one of its cases is a test that stops saying anything
    about that case.
    """
    assert parse(tag) is None


def test_the_newest_release_is_chosen_by_number_and_not_by_string() -> None:
    """**The defect this module exists for.** Every one of these lists has a lexicographic maximum
    that is not its numeric maximum."""
    assert latest(["v0.9.0", "v0.10.0"]) == (0, 10, 0)
    assert latest(["v1.0.0", "v2.0.0", "v10.0.0"]) == (10, 0, 0)
    assert latest(["v1.2.9", "v1.2.10"]) == (1, 2, 10)
    assert latest(["v1.9.9", "v1.10.0", "v1.2.3"]) == (1, 10, 0)


def test_tags_that_are_not_releases_do_not_decide_the_next_one() -> None:
    assert latest(["v1.2.3", "nightly", "v2.0.0-rc1", "demo-2026-10-07"]) == (1, 2, 3)


def test_nothing_tagged_is_reported_as_nothing_rather_than_as_zero() -> None:
    """`None`, never `(0, 0, 0)`. A zero would be a version somebody could bump from, and the
    first release would come out as `v0.0.1` — the same *unknown is not zero* rule the money path
    rests on (`FRD-403`), in a place nobody expects to meet it."""
    assert latest([]) is None
    assert latest(["main", "v1.2", "nightly"]) is None


def test_a_bump_resets_everything_less_significant() -> None:
    """`v1.4.7` by minor is `v1.5.0`, never `v1.5.7` — which would claim a patch history the new
    minor does not have."""
    assert bump((1, 4, 7), "patch") == (1, 4, 8)
    assert bump((1, 4, 7), "minor") == (1, 5, 0)
    assert bump((1, 4, 7), "major") == (2, 0, 0)


def test_a_bump_nobody_defined_refuses() -> None:
    with pytest.raises(ValueError, match="major, minor, patch"):
        bump((1, 0, 0), "mayor")


def test_the_first_release_is_the_first_version_and_not_a_patch_to_nothing() -> None:
    """`v0.1.0`. Returned as it is rather than bumped from a zero, for all three parts: the first
    published images are an early version of something, whichever part was asked for."""
    assert next_version([], "patch") == FIRST
    assert next_version([], "minor") == FIRST
    assert next_version([], "major") == FIRST
    assert FIRST == (0, 1, 0)


def test_an_unknown_bump_refuses_on_an_unreleased_project_too() -> None:
    """The branch that returns `FIRST` validates the part it was handed although it does not use
    it. A check that only runs down one branch reports green about the path it did not take, which
    is how an invalid input reaches a `git tag` command."""
    with pytest.raises(ValueError, match="major, minor, patch"):
        next_version([], "mayor")


def test_the_next_version_follows_the_newest_tag_through_both_traps_at_once() -> None:
    """Numeric ordering and the reset, composed — the shape a real repository hands this."""
    tags = ["v0.1.0", "v0.2.0", "v0.9.0", "v0.10.0", "v0.10.3", "wip", "v1.0.0-rc1"]

    assert next_version(tags, "patch") == (0, 10, 4)
    assert next_version(tags, "minor") == (0, 11, 0)
    assert next_version(tags, "major") == (1, 0, 0)


def test_one_speller_for_a_version() -> None:
    """The Git tag and the three image tags are the same string. Rendered in one place so a
    release cannot tag the repository `v1.2.3` and the images `1.2.3`."""
    assert render((1, 2, 3)) == "v1.2.3"
    assert render(next_version([], "patch")) == "v0.1.0"


def test_tags_in_reports_what_git_reports() -> None:
    """The one impure function, and the only claim worth making about it. Compared against the
    same command rather than against an expected list, because what this checkout has tagged is
    not this test's business — and on a repository with no tags both sides are empty, which still
    distinguishes "asked git" from "returned a guess"."""
    import subprocess

    expected = subprocess.run(
        ["git", "tag", "--list"], capture_output=True, text=True, check=True
    ).stdout.split()

    assert tags_in() == expected
