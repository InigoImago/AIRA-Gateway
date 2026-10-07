"""The next release version, derived from the tags that exist.

**Why the tags and not a file.** A version written into `pyproject.toml`, `package.json` and a
Helm chart is the same fact in three places, and this repository has paid for that shape often
enough to have a rule about it. A Git tag is already the thing that ties a version to a commit, it
cannot disagree with itself, and it is what somebody reads when they ask *"what is deployed?"*. So
the tag is the source and this computes from it; nothing in the tree records a version at all.

**The ordering is the whole difficulty.** Tags are strings and `v0.10.0` sorts before `v0.9.0`
in every string comparison there is, so a release cut from the lexicographic maximum silently goes
backwards the first time a minor number reaches ten. Compared as integer triples here, and pinned
by a test, because the defect appears once and then never again until it is too late to notice.

    uv run python tools/release_version.py                 # what a patch release would be
    uv run python tools/release_version.py --bump minor
    uv run python tools/release_version.py --current       # the newest release tag, or nothing
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: `v1.2.3` and nothing else. A pre-release (`v1.2.3-rc1`), a two-part tag (`v1.2`) and an
#: unprefixed one (`1.2.3`) are **not** releases of this project and must not be bumped from: a
#: pattern loose enough to match them would make the next version depend on somebody's scratch tag.
_RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

Version = tuple[int, int, int]

#: The first release, used when no tag exists yet. Returned **as it is** rather than bumped from
#: `v0.0.0`, so the first release is `v0.1.0` and not `v0.0.1`: a project's first published images
#: are an early version of something, not a patch to nothing.
FIRST = (0, 1, 0)

PARTS = ("major", "minor", "patch")


def parse(tag: str) -> Version | None:
    """The triple a release tag names, or `None` for anything that is not one."""
    match = _RELEASE_TAG.match(tag.strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def latest(tags: list[str]) -> Version | None:
    """The newest release among `tags`, by **number**, or `None` if there is none."""
    found = [version for version in (parse(tag) for tag in tags) if version is not None]
    return max(found) if found else None


def bump(version: Version, part: str) -> Version:
    """`version` with `part` incremented and everything less significant reset to zero.

    The reset is the point: `v1.4.7` bumped by minor is `v1.5.0`, never `v1.5.7`, which would
    claim a patch history the new minor does not have.
    """
    if part not in PARTS:
        raise ValueError(f"'{part}' is not one of {', '.join(PARTS)}")
    major, minor, patch = version
    if part == "major":
        return major + 1, 0, 0
    if part == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def next_version(tags: list[str], part: str = "patch") -> Version:
    """The version a release cut now would carry — the first one if nothing is tagged yet."""
    current = latest(tags)
    if current is None:
        # Validated even though it is not used, so `--bump nonsense` fails on an unreleased
        # project exactly as it does on a released one. A check that only runs down one branch is
        # a check that reports green about the path it did not take.
        if part not in PARTS:
            raise ValueError(f"'{part}' is not one of {', '.join(PARTS)}")
        return FIRST
    return bump(current, part)


def render(version: Version) -> str:
    """`(1, 2, 3)` as `v1.2.3` — one speller, so a tag and an image tag cannot differ."""
    return "v{}.{}.{}".format(*version)


def tags_in(repository: Path = ROOT) -> list[str]:
    """Every tag the checkout can see.

    A shallow clone has none, which would make every release look like the first one — so the
    release job checks out with full history, and a test asserts that it does. `--require-tags` is
    for a caller that already knows a release exists: it turns the same mistake into a refusal
    rather than into `v0.1.0` published over an existing `v2.4.0`. The release job cannot pass it,
    because on a genuinely first release an empty tag list is the correct answer.
    """
    result = subprocess.run(  # noqa: S603  — fixed argument list, no shell
        ["git", "tag", "--list"],  # noqa: S607  — `git` from PATH is the intent
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bump", choices=PARTS, default="patch")
    parser.add_argument(
        "--current", action="store_true", help="print the newest release tag instead of the next"
    )
    parser.add_argument(
        "--require-tags",
        action="store_true",
        help="fail rather than fall back to the first version when no release tag is visible",
    )
    arguments = parser.parse_args(argv)

    tags = tags_in()
    if arguments.current:
        current = latest(tags)
        if current is None:
            return 1
        print(render(current))
        return 0

    if arguments.require_tags and latest(tags) is None:
        print(
            "no release tag is visible in this checkout.\n"
            "A shallow clone sees no tags, and treating that as 'never released' would publish\n"
            "v0.1.0 over whatever is already out. Fetch the tags (checkout with fetch-depth: 0)\n"
            "or drop --require-tags if this really is the first release.",
            file=sys.stderr,
        )
        return 1

    print(render(next_version(tags, arguments.bump)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
