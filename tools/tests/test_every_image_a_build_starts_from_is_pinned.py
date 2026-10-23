"""Every image a build starts from names a version, and a tool copied into a build its digest.

A `FROM` or `COPY --from` is code the build runs or ships, fetched by a name somebody else can
move. Two rules, of different strength for a reason:

- **A base names a version** — `python:3.14-slim-bookworm`, `nginx:1.30-alpine` — never `latest`
  and never a bare variant such as `alpine`. A version line is still rebuilt by its maintainers
  whenever its distribution ships a fix, which is what keeps a base patched; a moving name is
  whatever was pushed last, and a line that has ended keeps building without being patched — which
  is how the console came to carry two critical OpenSSL findings.
- **A tool copied out of an image is pinned to its digest as well.** The installer reads the lock
  and writes every package into the image, so the bytes that run it must be the bytes reviewed;
  nothing has to be patched into a binary that only runs during the build.

And the two Python images copy the same installer: one build that resolves differently from the
other is a difference nobody chose.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
#: The Dockerfiles the repository holds — asked of Git, so a checkout's other working trees, caches
#: and installed packages are not read as this repository's builds.
DOCKERFILES = sorted(
    ROOT / name
    for name in subprocess.run(
        ["git", "ls-files", "*Dockerfile*"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    if (ROOT / name).exists()
)

_FROM = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?", re.I)
_COPY_FROM = re.compile(r"^COPY\s+(?:--\S+\s+)*?--from=(?P<image>\S+)", re.I)
#: `name:tag` where the tag is a version: it starts with a digit. `latest` and `alpine` do not.
_VERSIONED = re.compile(r"^[^@\s]+:(?P<tag>\d[\w.\-]*)(?:@sha256:[0-9a-f]{64})?$")
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def _references(path: Path) -> list[tuple[str, str]]:
    """``(instruction, image)`` for every image a Dockerfile names; its own stages excluded."""
    stages: set[str] = set()
    found: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if match := _FROM.match(line):
            if match["image"] not in stages:
                found.append(("FROM", match["image"]))
            if match["stage"]:
                stages.add(match["stage"])
        elif (match := _COPY_FROM.match(line)) and match["image"] not in stages:
            found.append(("COPY --from", match["image"]))
    return found


def test_the_dockerfiles_are_found() -> None:
    """The guard's own failure mode: a glob that matched nothing would make every test pass."""
    names = {str(path.relative_to(ROOT)) for path in DOCKERFILES}

    assert {
        "gateway/Dockerfile",
        "management/backend/Dockerfile",
        "management/frontend/Dockerfile",
    } <= names


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda path: str(path.relative_to(ROOT)))
def test_every_image_a_build_names_is_a_version(path: Path) -> None:
    references = _references(path)
    assert references, f"{path.relative_to(ROOT)} names no image, so the parse stopped matching"

    for instruction, image in references:
        assert _VERSIONED.match(image), (
            f"{path.relative_to(ROOT)}: `{instruction} {image}` names no version, so the build "
            "uses whatever was pushed under that name last"
        )


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda path: str(path.relative_to(ROOT)))
def test_a_tool_copied_into_a_build_is_pinned_to_its_digest(path: Path) -> None:
    for instruction, image in _references(path):
        if instruction == "COPY --from":
            assert _DIGEST.search(image), (
                f"{path.relative_to(ROOT)}: `{instruction}={image}` can change under a fixed "
                "version tag; pin it to the digest that was reviewed"
            )


def test_both_python_images_copy_the_same_installer() -> None:
    installers = {
        image
        for path in (ROOT / "gateway/Dockerfile", ROOT / "management/backend/Dockerfile")
        for instruction, image in _references(path)
        if instruction == "COPY --from" and "/astral-sh/uv:" in image
    }

    assert len(installers) == 1, (
        f"the Python images are built with different installers: {installers}"
    )


def test_the_console_is_served_by_nginx_s_stable_line() -> None:
    """nginx keeps two lines: **stable**, an even minor, which takes only fixes and is maintained
    until the next stable one a year later, and **mainline**, an odd minor, whose image stops being
    rebuilt as soon as the next mainline minor appears. The console's base was `1.27` — a mainline
    line that had ended, with two critical OpenSSL findings nobody would ever rebuild away."""
    (runtime,) = [
        image
        for instruction, image in _references(ROOT / "management/frontend/Dockerfile")
        if instruction == "FROM" and image.startswith("nginx:")
    ]
    version = re.match(r"nginx:(?P<major>\d+)\.(?P<minor>\d+)", runtime)

    assert version, f"the console's base `{runtime}` names no nginx line"
    assert int(version["minor"]) % 2 == 0, f"`{runtime}` is a mainline line, not nginx's stable one"
