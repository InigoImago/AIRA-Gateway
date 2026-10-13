"""A release carries a description, or it is refused (`FRD-137`, `ADR-0030`).

**The defect these tests exist for is a missing field, not a missing habit.** `v0.1.0` was published
by a form with a `bump` dropdown; the tag it left behind said, in full, `Release v0.1.0`, and there
was no GitHub Release at all. The mechanism had nowhere to put a description, so there was none —
and no amount of resolve fixes that, because there was no moment at which anybody was asked.

The trigger is an annotated tag now, and its message *is* the release notes. That makes the message
load-bearing, so it is checked before anything is published, which is the only point at which
refusing is still free.

Hermetic: the rules are tested on message strings, and the functions that ask Git are tested
against **a repository of their own, made per test** — never by tagging the developer's checkout. A
test that tags the real repository and removes the tag in `finally` leaves it behind when the run is
killed, and a leftover `v99.99.98` with a perfectly good annotation is a release one `git push
--follow-tags` away, after which every real release is refused as older.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import release_notes
from release_notes import NotDescribed, Notes, read

REPOSITORY = Path(__file__).resolve().parents[2]

#: Git as the scratch repositories see it: no global or system configuration — so a developer's
#: `tag.gpgSign` cannot prompt for a passphrase — and an identity, which an annotated tag needs and
#: a fresh CI checkout does not have.
ISOLATED_GIT = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "the test suite",
    "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "the test suite",
    "GIT_COMMITTER_EMAIL": "tests@example.invalid",
}


def _tags(repository: Path) -> list[str]:
    return subprocess.run(
        ["git", "tag", "--list"], cwd=repository, capture_output=True, text=True, check=True
    ).stdout.split()


@pytest.fixture(autouse=True)
def _the_real_repository_is_never_tagged() -> Iterator[None]:
    """The property this module once broke: whatever a test does, the checkout's tags are as they
    were — checked after every test, so a test that tags the wrong repository fails here."""
    before = _tags(REPOSITORY)
    yield
    assert _tags(REPOSITORY) == before, "a test changed the tags of the repository it runs in"


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A one-commit repository that `release_notes` asks instead of the checkout."""
    for name, value in ISOLATED_GIT.items():
        monkeypatch.setenv(name, value)
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    (repository / "README").write_text("scratch\n")
    subprocess.run(["git", "add", "README"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "scratch"], cwd=repository, check=True)
    monkeypatch.setattr(release_notes, "ROOT", repository)
    return repository


def _tag(repository: Path, name: str, message: str | None = None) -> None:
    """A lightweight tag without a message, an annotated one with."""
    annotation = ["-a", "-m", message] if message is not None else []
    subprocess.run(
        ["git", "tag", *annotation, name], cwd=repository, check=True, capture_output=True
    )


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
        # **Found by writing real notes with this very tool.** The project's own name is no more
        # informative than the word "release": anybody opening a release of this repository already
        # knows which project it is. The first draft of the rule let this through.
        "AIRA Gateway v1.2.3",
        "aira v1.2.3",
        "AIRA Gateway",
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


@pytest.mark.parametrize(
    "subject",
    [
        "v1.2.3 drops the dispatch form",
        "The gateway refuses a model it has no price for",
        "AIRA Gateway speaks the Anthropic dialect on Vertex",
        "The same gateway as v1.2.2, described for the first time",
    ],
)
def test_a_subject_that_uses_those_words_to_say_something_is_accepted(subject: str) -> None:
    """The rule is *only* restates, not *mentions*. Refusing a subject for containing its own
    version number — or the project's name — would punish the careful case along with the empty
    one, and `AIRA Gateway speaks the Anthropic dialect on Vertex` is exactly the subject somebody
    should be able to write."""
    notes = _read(f"{subject}\n\nA body that is perfectly fine on its own.\n")

    assert notes.subject == subject


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


def test_a_lightweight_tag_is_not_mistaken_for_an_annotated_one(scratch: Path) -> None:
    """**Rule 1 did not work, and this is the test that would have caught it.**

    `annotation` asked Git for `%(contents)`, and its comment claimed that was empty for a
    lightweight tag. It is not: for a ref pointing straight at a commit, `%(contents)` is **the
    commit's message**. A commit message has a subject, has a body, and says something — so it
    passed rules 2, 3 and 4, and rule 1 was never reached.

    What that cost: `v0.1.1` was published on 2026-10-11 with `def905f`'s commit message as its
    release notes and `fix(tests): wait for the rows a request makes, not for one` as its title,
    while the annotated tag on the remote carried the real description the whole time. The release
    job had been handed a flattened tag by `actions/checkout` and had no way to notice.

    Asserted against a real lightweight tag, because the whole defect was a claim about what Git
    returns that nobody had asked Git about.
    """
    from release_notes import annotation

    _tag(scratch, "v99.99.99")
    assert "v99.99.99" not in _tags(REPOSITORY), "the test tagged the checkout it runs in"

    assert annotation("v99.99.99") == "", (
        "a lightweight tag reports an annotation — which is the commit's message, and will be "
        "published as the release notes"
    )
    with pytest.raises(NotDescribed, match="git tag -a"):
        read(annotation("v99.99.99"), version="v99.99.99")


def test_the_template_exists_and_asks_for_something() -> None:
    """The template is what `make release-tag` opens an editor on, and the source of the
    placeholder list rule 5 refuses. A template with no placeholders makes rule 5 inert without
    making it absent, which is the worst of the three states."""
    from release_notes import TEMPLATE, placeholders

    assert TEMPLATE.exists(), f"{TEMPLATE} is missing, so `make release-tag` opens on nothing"

    prompts = placeholders()
    assert len(prompts) >= 4, f"the template asks for {len(prompts)} things: {prompts}"
    assert all(prompt.startswith("<") and prompt.endswith(">") for prompt in prompts)


def test_a_template_nobody_filled_in_is_refused() -> None:
    """**Rule 5.** The template's sections are the ones a reader needs, so a release that still
    carries its prompts has the right shape and nothing in it — and rules 2 to 4 all pass on it,
    because a template has a subject and a body.

    Run against the real template, with its comment lines stripped the way
    `git tag --cleanup=strip` strips them, so this fails if the template grows a section whose
    placeholder is spelled in some way the rule does not see.
    """
    from release_notes import TEMPLATE

    filled_in_by_nobody = "\n".join(
        line for line in TEMPLATE.read_text().splitlines() if not line.startswith("#")
    )

    with pytest.raises(NotDescribed, match="placeholder"):
        read(filled_in_by_nobody, version="v1.2.3")


def test_answering_the_template_is_accepted() -> None:
    """The other direction, because a rule that refuses everything is not a rule. The shape here is
    the template's own, with its prompts answered."""
    notes = read(
        "Deployment: run the published images instead of building them\n"
        "\n"
        "The application code is unchanged from v0.1.0.\n"
        "\n"
        "WHAT CHANGED\n"
        "\n"
        "Two Compose overlays take the three images from the registry.\n"
        "\n"
        "UPGRADING\n"
        "\n"
        "Nothing to do.\n",
        version="v1.2.3",
    )

    assert notes.subject.startswith("Deployment")
    assert "UPGRADING" in notes.body


def test_the_title_and_the_body_come_from_the_same_place(scratch: Path) -> None:
    """**Two places asking Git what a tag says are two chances to be told about a commit**, and
    only one of them was checked. The Release's title was a second `git tag --format` in the
    workflow; when the tag arrived flattened, the title became the commit's subject and nothing
    objected. `subject` is the one owner now, and it goes through `read`, so a title can never be
    published from an annotation the rules would refuse.

    **Against tags this test makes, not the ones the checkout has.** The first version asserted
    about `v0.1.1` and `v0.1.0`, which passed here and failed in CI: `actions/checkout` fetches the
    triggering ref and no other tag, so `v0.1.1` was not in the clone at all and `annotation`
    correctly reported nothing. A guard that reads the ambient repository says something different
    in every clone — and the alternative it tempts you into, skipping when the tag is absent, means
    the property goes unchecked in the one place that publishes.

    The tagger identity comes from the scratch repository's environment, because an annotated tag
    needs one and a fresh checkout has none: `git tag -a` exits 128 with *Committer identity
    unknown* in CI, where nothing has configured a `user.email`.
    """
    from release_notes import annotation, subject

    described, bare = "v99.99.98", "v99.99.97"
    _tag(scratch, described, "The gateway speaks a second dialect\n\nAnd why.")
    _tag(scratch, bare, f"Release {bare}\n\nA body that is fine on its own.")
    assert not {described, bare} & set(_tags(REPOSITORY)), "the test tagged its own checkout"

    assert (
        subject(described)
        == read(annotation(described), version=described).subject
        == "The gateway speaks a second dialect"
    )

    # The half that matters: a title is not published from an annotation the rules refuse.
    with pytest.raises(NotDescribed, match="only restates the version"):
        subject(bare)


def test_the_first_releases_own_annotation_is_refused_wherever_this_runs(scratch: Path) -> None:
    """The case the rules were written for, checked in CI too.

    The test above that reads `v0.1.0` from the checkout skips wherever the tag is absent — and a
    CI checkout fetches no tags — so the property would be unchecked in the one place that
    publishes. The same annotation, on a tag made here.
    """
    from release_notes import annotation, previous

    _tag(scratch, "v0.1.0", "Release v0.1.0")

    with pytest.raises(NotDescribed, match="only restates the version"):
        read(annotation("v0.1.0"), version="v0.1.0")
    assert previous("v0.1.0") is None
