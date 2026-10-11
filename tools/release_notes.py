"""The description a release carries, read from the tag that started it.

**Why this exists.** `v0.1.0` was published by a form with a `bump` dropdown, and the tag it left
behind said, in full: `Release v0.1.0`. No GitHub Release, no notes, nothing that tells anybody
what the version contains. The mechanism had no place to put a description, so there was none —
which is not a discipline problem, it is a missing field.

So the trigger is now an **annotated tag**, and its message *is* the release notes. That gives the
description a home at exactly the moment somebody has the change in mind, in an editor, in the
terminal they are already in. And because the message is now load-bearing, it is checked: a tag
whose annotation says nothing is **refused before anything is published**, which is the only point
at which refusing is still free.

Four rules, each one a thing that actually happened or would:

1. The tag is **annotated**. A lightweight tag has no message at all — `git tag v0.1.2` rather than
   `git tag -a`, one missing flag.
2. There is a **subject** line.
3. The subject is not a **restatement of the version**. `Release v0.1.0` is what the old mechanism
   wrote by itself, and it is indistinguishable from having written nothing.
4. There is a **body** after a blank line. A subject alone is a headline; the body is where the
   reader finds out what changed and whether it affects them.

    uv run python tools/release_notes.py --check v0.1.1
    uv run python tools/release_notes.py v0.1.1        # the rendered notes, to stdout
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from release_version import parse, render

ROOT = Path(__file__).resolve().parents[1]

#: Words a bare subject is made of. Stripped out before asking whether anything is left, so that
#: `Release v0.1.0`, `v0.1.0`, `version 0.1.0` and `0.1.0 release` are all recognised as saying
#: nothing — the restatement comes in more spellings than the one the old job happened to write.
#:
#: **The project's own name belongs here**, and the first draft of this left it out. Writing
#: `v0.1.1`'s notes produced the subject `AIRA Gateway v0.1.1`, which passed — and it is the same
#: non-answer as `Release v0.1.0` with the product's name in place of the word. Anybody opening a
#: release of this repository already knows which project it is. Only the words are removed, so a
#: subject that *uses* them to say something ("the gateway now refuses …") still has a subject left.
_FILLER = re.compile(
    r"\b(aira|gateway|release|version|tag|v?\d+\.\d+\.\d+|v\d+)\b|[^\w\s]",
    re.IGNORECASE,
)


class NotDescribed(Exception):
    """The tag carries no usable description. Carries the reason, because "bad annotation" sends a
    reader to look at everything at once."""


@dataclass(frozen=True)
class Notes:
    """A release's description, split the way Git splits a commit message."""

    subject: str
    body: str

    def markdown(self, *, version: str, commits: list[str], images: list[str]) -> str:
        """The GitHub Release body: what the tagger wrote, then what the range contains.

        The prose comes first and the generated part second, on purpose. A commit list is evidence,
        not a description — it answers *what moved* and never *why it matters*, and a release whose
        notes are only a commit list is the thing this module was written to stop.
        """
        sections = [self.body.strip()]
        if commits:
            sections.append("## Commits\n\n" + "\n".join(f"- {line}" for line in commits))
        if images:
            sections.append(
                "## Images\n\n"
                + "\n".join(f"- `{image}`" for image in images)
                + f"\n\nEach is also tagged `latest` and `sha-<commit>`. Pin `{version}`."
            )
        return "\n\n".join(sections) + "\n"


def _stripped_subject(subject: str) -> str:
    """`subject` with the words a restatement is made of removed."""
    return _FILLER.sub(" ", subject).strip()


def read(message: str, *, version: str) -> Notes:
    """`message` as `Notes`, or `NotDescribed` saying which of the four rules it breaks."""
    text = message.strip()
    if not text:
        raise NotDescribed(
            f"{version} has no annotation. A lightweight tag carries no message, so there is "
            "nothing to publish as release notes — create it with 'git tag -a'."
        )

    subject, _, body = text.partition("\n")
    subject = subject.strip()
    if not subject:
        raise NotDescribed(f"{version} has no subject line.")

    if not _stripped_subject(subject):
        raise NotDescribed(
            f"{version}'s subject is {subject!r}, which only restates the version. Say what the "
            "release is — the same sentence you would write as a commit subject."
        )

    if not body.strip():
        raise NotDescribed(
            f"{version} has a subject and no body. Leave a blank line after the subject and say "
            "what changed and whom it affects; a headline alone is what a reader already has from "
            "the version number."
        )

    return Notes(subject=subject, body=body.strip())


def _git(*arguments: str) -> str:
    result = subprocess.run(  # noqa: S603  — fixed argument list, no shell
        ["git", *arguments],  # noqa: S607  — `git` from PATH is the intent
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def annotation(version: str) -> str:
    """The message of the annotated tag `version`, or `''` for a lightweight one.

    `%(contents)` is empty for a lightweight tag, which is exactly the case rule 1 is about — so
    this reports it rather than failing, and `read` turns it into the sentence that explains it.
    """
    return _git("for-each-ref", f"refs/tags/{version}", "--format=%(contents)")


def previous(version: str) -> str | None:
    """The newest release tag strictly older than `version`, by number."""
    current = parse(version)
    if current is None:
        return None
    older = [
        found
        for found in (parse(tag) for tag in _git("tag", "--list").split())
        if found is not None and found < current
    ]
    return render(max(older)) if older else None


def commits_since(earlier: str | None, version: str) -> list[str]:
    """One line per commit in `(earlier, version]`, subjects only.

    With no earlier release this is deliberately **empty** rather than the whole history: a first
    release's notes are not improved by four hundred commit subjects.
    """
    if earlier is None:
        return []
    return [
        line
        for line in _git("log", "--format=%s", f"{earlier}..{version}").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the release tag, e.g. v0.1.1")
    parser.add_argument(
        "--check", action="store_true", help="validate the annotation and print nothing on success"
    )
    parser.add_argument(
        "--image",
        action="append",
        default=[],
        help="a published image reference to list in the notes; repeatable",
    )
    arguments = parser.parse_args(argv)

    if parse(arguments.version) is None:
        print(f"'{arguments.version}' is not a release tag (vX.Y.Z).", file=sys.stderr)
        return 1

    try:
        notes = read(annotation(arguments.version), version=arguments.version)
    except NotDescribed as problem:
        print(f"{problem}", file=sys.stderr)
        return 1

    if arguments.check:
        return 0

    print(
        notes.markdown(
            version=arguments.version,
            commits=commits_since(previous(arguments.version), arguments.version),
            images=arguments.image,
        ),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
