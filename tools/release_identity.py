"""Refuse a release whose images do not say they are that release (`FRD-157`).

The release job builds each image with the tag, the commit and the time as build arguments, and the
images wrote them where a reader looks: as labels, which ``docker inspect`` shows, and — in the two
that run Python — in the environment every process reads its identity from, which is what it states
in its first line, on every span and metric and on ``/healthz``. v0.1.17's processes said
``0.1.0``, the number written into the packages before the first release, because nothing handed
the tag over. This reads each image back from the registry **by the digest just pushed** and fails
on any image that says another version than the tag, another commit than the one built, or no time.

    python tools/release_identity.py v0.1.18 --commit <sha> --built 2026-11-27T12:00:00Z \\
        --image aira-gateway=ghcr.io/<owner>/aira-gateway@sha256:…
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from release_version import parse

#: The labels an image carries its identity under, as the Open Container Initiative names them.
LABEL_VERSION = "org.opencontainers.image.version"
LABEL_REVISION = "org.opencontainers.image.revision"
LABEL_CREATED = "org.opencontainers.image.created"

#: The images whose processes state their build themselves. The console's is nginx, which states
#: nothing of its own: its labels are what say it.
STATES_ITSELF = frozenset({"aira-gateway", "aira-management"})

#: What a process states, as `aira_common.build` prints it: the module every process reads.
ASK_THE_IMAGE = ["--entrypoint", "python"]
STATEMENT = ["-m", "aira_common.build"]

#: An image is checked by digest: a tag can be moved after the push, a digest cannot.
_BY_DIGEST = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")

#: Runs ``docker <arguments>`` and returns what it printed.
Docker = Callable[[list[str]], str]


def _docker(command: str) -> Docker:
    def run(arguments: list[str]) -> str:
        return subprocess.run(  # noqa: S603
            [command, *arguments], check=True, capture_output=True, text=True
        ).stdout

    return run


def release_number(tag: str) -> str:
    """``v0.1.18`` → ``0.1.18``: the version as a process states it."""
    version = parse(tag)
    if version is None:
        raise ValueError(f"'{tag}' is not a release version (vX.Y.Z)")
    return "{}.{}.{}".format(*version)


def _differences(
    name: str, where: str, said: Mapping[str, object], wanted: Mapping[str, str]
) -> list[str]:
    return [
        f"{name}: {where} {key} is {said.get(key)!r}, not {value!r}"
        for key, value in wanted.items()
        if said.get(key) != value
    ]


def problems_of(
    name: str, reference: str, *, version: str, commit: str, built: str, docker: Docker
) -> list[str]:
    """Everything ``reference`` says about itself that is not ``version``, ``commit``, ``built``."""
    if not _BY_DIGEST.fullmatch(reference):
        return [f"{name}: {reference} is not pinned by digest"]
    docker(["pull", "--quiet", reference])
    labels = json.loads(
        docker(["inspect", "--format", "{{json .Config.Labels}}", reference]) or "null"
    )
    problems = _differences(
        name,
        "its label",
        labels if isinstance(labels, dict) else {},
        {LABEL_VERSION: version, LABEL_REVISION: commit, LABEL_CREATED: built},
    )
    if name in STATES_ITSELF:
        statement = json.loads(docker(["run", "--rm", *ASK_THE_IMAGE, reference, *STATEMENT]))
        problems += _differences(
            name,
            "what its processes state as",
            statement if isinstance(statement, dict) else {},
            {"version": version, "commit": commit, "time": built},
        )
    return problems


def check(
    tag: str, *, commit: str, built: str, images: Mapping[str, str], docker: Docker
) -> list[str]:
    """Every image's problems, in the order the images were named; none means the release may go."""
    if not images:
        return ["no image was named, so none says what it is"]
    if not built.strip():
        return ["no build time was handed over, so none can be checked"]
    version = release_number(tag)
    problems: list[str] = []
    for name, reference in images.items():
        problems += problems_of(
            name, reference, version=version, commit=commit, built=built, docker=docker
        )
    return problems


def _image(argument: str) -> tuple[str, str]:
    component, separator, reference = argument.partition("=")
    if not separator:
        raise argparse.ArgumentTypeError(f"'{argument}' is not NAME=REFERENCE")
    return component, reference


def _summarise(path: Path | None, tag: str, images: Sequence[str], problems: list[str]) -> None:
    if path is None:
        return
    lines = [f"### What each image of {tag} says it is", ""]
    lines += [f"- {problem}" for problem in problems] or [
        f"- {name}: {tag}, the commit built, and the time" for name in images
    ]
    with path.open("a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="the release tag, e.g. v0.1.18")
    parser.add_argument("--commit", required=True, help="the commit the images were built from")
    parser.add_argument("--built", required=True, help="the build time the images were given")
    parser.add_argument(
        "--image",
        type=_image,
        action="append",
        default=[],
        help="NAME=REFERENCE, the reference pinned by digest; once per image",
    )
    parser.add_argument("--summary", type=Path, help="a Markdown file to append the result to")
    parser.add_argument("--docker", default="docker", help="the Docker command to run")
    arguments = parser.parse_args(argv)
    images = dict(arguments.image)
    try:
        problems = check(
            arguments.tag,
            commit=arguments.commit,
            built=arguments.built,
            images=images,
            docker=_docker(arguments.docker),
        )
    except (ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as failure:
        problems = [f"the check could not be made: {failure}"]
    _summarise(arguments.summary, arguments.tag, list(images), problems)
    for problem in problems:
        print(f"::error::{problem}", file=sys.stderr)
    if problems:
        return 1
    for name in images:
        print(f"{name} says it is {arguments.tag} at {arguments.commit}, built {arguments.built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
