"""A release carries a description, or it is refused (`FRD-137`, `ADR-0030`).

**The defect these tests exist for is a missing field, not a missing habit.** `v0.1.0` was published
by a form with a `bump` dropdown; the tag it left behind said, in full, `Release v0.1.0`, and there
was no GitHub Release at all. The mechanism had nowhere to put a description, so there was none —
and no amount of resolve fixes that, because there was no moment at which anybody was asked.

The trigger is an annotated tag now, and its message *is* the release notes. That makes the message
load-bearing, so it is checked before anything is published, which is the only point at which
refusing is still free.

Hermetic: every test passes a message string. The two functions that touch Git are exercised against
the repository's own tags, where the only honest assertion is that they report what Git reports.
"""

from __future__ import annotations

import pytest
from release_notes import NotDescribed, Notes, read


def _read(message: str, version: str = "v1.2.3") -> Notes:
    return read(message, version=version)


def test_a_description_with_a_subject_and_a_body_is_accepted() -> None:
    """The shape a commit message has, for the same reason: a subject somebody can scan in a list,
    and a body they read when it turns out to concern them."""
    notes = _read("Tag-driven releases\n\nThe tag's message is now the release notes.\n")

    assert notes.subject == "Tag-driven releases"
    assert notes.body == "The tag's message is now the release notes."


def test_a_lightweight_tag_is_refused_and_told_which_flag_is_missing() -> None:
    """**One missing flag.** `git tag v0.1.2` rather than `git tag -a` leaves no message at all, and
    the difference is invisible in `git tag --list` — so the refusal names the flag rather than
    saying the annotation is "invalid"."""
    with pytest.raises(NotDescribed, match="git tag -a"):
        _read("")

    with pytest.raises(NotDescribed, match="no annotation"):
        _read("   \n\n  \n")


@pytest.mark.parametrize(
    "subject",
    [
        "Release v1.2.3",  # what the old mechanism wrote by itself
        "v1.2.3",
        "1.2.3",
        "version 1.2.3",
        "Version v1.2.3",
        "v1.2.3 release",
        "release",
        "tag v1.2.3",
        "   v1.2.3   ",
        "Release: v1.2.3!",
    ],
)
def test_a_subject_that_only_restates_the_version_is_refused(subject: str) -> None:
    """**The case that actually happened**, plus the spellings of the same non-answer.

    `Release v0.1.0` is indistinguishable from having written nothing — it tells a reader exactly
    what the version number already told them. Checked by removing the words a restatement is made
    of and asking whether anything is left, rather than by matching the one string the old job
    produced: a guard against a single spelling is a guard somebody walks around by accident.
    """
    with pytest.raises(NotDescribed, match="only restates the version"):
        _read(f"{subject}\n\nA body that is perfectly fine on its own.\n")


def test_a_subject_that_mentions_the_version_but_says_something_is_accepted() -> None:
    """The rule is *only* restates, not *mentions*. Refusing a subject for containing its own
    version number would be a guard that punishes the careful case along with the empty one."""
    notes = _read("v1.2.3 drops the dispatch form\n\nReleases are tags now.\n")

    assert notes.subject == "v1.2.3 drops the dispatch form"


def test_a_subject_with_no_body_is_refused() -> None:
    """A headline alone is what a reader already has from the version number and the commit list.
    The body is where they find out what changed and whether it affects them."""
    with pytest.raises(NotDescribed, match="subject and no body"):
        _read("Tag-driven releases\n")

    with pytest.raises(NotDescribed, match="subject and no body"):
        _read("Tag-driven releases\n\n   \n  \n")


def test_the_body_keeps_its_own_shape() -> None:
    """Markdown goes to GitHub unchanged — lists, headings and links are how release notes are
    written, and reflowing them would be this tool having an opinion it has no business having."""
    notes = _read(
        "Tag-driven releases\n\n"
        "- the tag's message is the notes\n"
        "- a bare annotation is refused\n\n"
        "## Upgrading\n\nNothing to do.\n"
    )

    assert "- the tag's message is the notes" in notes.body
    assert "## Upgrading" in notes.body


def test_the_notes_put_the_prose_before_the_evidence() -> None:
    """**Order carries a claim.** A commit list answers *what moved* and never *why it matters*, so
    a release whose notes open with one has told the reader nothing in the place they look first."""
    notes = _read("Tag-driven releases\n\nWhat it means for you.\n")

    rendered = notes.markdown(
        version="v1.2.3",
        commits=["feat: something", "fix: something else"],
        images=["ghcr.io/x/aira-gateway:v1.2.3"],
    )
    assert rendered.index("What it means for you.") < rendered.index("## Commits")
    assert rendered.index("## Commits") < rendered.index("## Images")
    assert "- feat: something" in rendered
    assert "`ghcr.io/x/aira-gateway:v1.2.3`" in rendered


def test_notes_with_nothing_to_add_are_just_the_description() -> None:
    """A first release has no previous tag to compare against, and listing four hundred commit
    subjects would not improve it. An empty section is worse than no section.

    **Both halves, because the first draft only had the second.** A mutation that made
    `commits_since` return `[""]` for a first release survived: the test handed `markdown` an empty
    list directly and so never reached the function that decides what that list is. A test that
    asserts the renderer's behaviour says nothing about its caller.
    """
    from release_notes import commits_since

    # The `None` branch returns before touching git, so this stays hermetic.
    assert commits_since(None, "v0.1.0") == []

    rendered = _read("First release\n\nIt exists.\n").markdown(
        version="v0.1.0", commits=commits_since(None, "v0.1.0"), images=[]
    )

    assert rendered.strip() == "It exists."
    assert "## Commits" not in rendered


def test_the_repositorys_own_tags_are_read_as_git_reports_them() -> None:
    """The two impure functions, against the repository they run in.

    Asserted as a relationship rather than against an expected value, because what this checkout
    has tagged is not this test's business — and `v0.1.0`, which exists, is the very tag whose
    annotation the rules above reject. That makes it the one case worth naming here: the guard is
    checked against the real thing it was written for, not only against strings.
    """
    import subprocess

    from release_notes import annotation, previous

    tags = subprocess.run(
        ["git", "tag", "--list"], capture_output=True, text=True, check=True
    ).stdout.split()
    if "v0.1.0" not in tags:
        pytest.skip("v0.1.0 is not in this checkout, so there is nothing to compare against")

    assert annotation("v0.1.0").strip() == "Release v0.1.0", (
        "the first release's annotation changed; it is quoted in FRD-137 and ADR-0030"
    )
    with pytest.raises(NotDescribed, match="only restates the version"):
        read(annotation("v0.1.0"), version="v0.1.0")

    # Nothing precedes the first release, numerically.
    assert previous("v0.1.0") is None
