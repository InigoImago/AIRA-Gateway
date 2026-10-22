"""Describe what a release contains: one CycloneDX SBOM per image, and one of the source.

The release job pushes three images and then runs this, so each document describes the **digest
that was pushed** — scanned back from the registry — rather than a tag that can move or a local
rebuild. The console's image holds a compiled bundle, in which no scanner finds the npm packages it
was built from; the source document, read from the tagged commit's lock files, is where they are.

Each document is checked before it is published: CycloneDX, specification 1.6, naming what it
describes, and listing at least one package. An empty document is a release claiming to contain
nothing, and the release stops instead.

    uv run python tools/release_sbom.py v0.1.8 \\
        --image aira-gateway=ghcr.io/<owner>/aira-gateway@sha256:… --source . --out dist/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from release_version import parse

ROOT = Path(__file__).resolve().parents[1]

#: Syft's name for the output: CycloneDX JSON, specification 1.6 — accepted by the BSI's TR-03183-2
#: and read by Dependency-Track, Trivy and Grype.
FORMAT = "cyclonedx-json@1.6"
SPEC_VERSION = "1.6"

#: The source document's component name, beside the images' own.
SOURCE_NAME = "aira-source"

#: Test tooling with lock files of its own: the browser suite and the client harnesses. Nothing in
#: them ships, and listing them would put Playwright in an inventory of the product.
SOURCE_EXCLUDES = ("./e2e/**", "./tools/clients/**")

#: A component name reaches a file name; the images are `aira-<word>`.
_NAME = re.compile(r"aira-[a-z]+")

#: An image is described by digest: a tag can be moved after the scan, a digest cannot.
_BY_DIGEST = re.compile(r"[^@\s]+@sha256:[0-9a-f]{64}")

Run = Callable[[list[str]], None]


def name(component: str, version: str) -> str:
    """The document's file name: the version is in it, so a release's assets never collide."""
    return f"{component}-{version}.cdx.json"


def _syft(command: str) -> Run:
    def run(arguments: list[str]) -> None:
        subprocess.run([command, *arguments], check=True)  # noqa: S603

    return run


def checked(path: Path, component: str) -> None:
    """Refuse a document that is not one a reader can use."""
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError) as problem:
        raise ValueError(f"{path.name}: not a JSON document ({problem})") from None
    if document.get("bomFormat") != "CycloneDX":
        raise ValueError(f"{path.name}: not a CycloneDX document")
    if document.get("specVersion") != SPEC_VERSION:
        raise ValueError(
            f"{path.name}: CycloneDX {document.get('specVersion')}, not {SPEC_VERSION}"
        )
    described = (document.get("metadata") or {}).get("component") or {}
    if described.get("name") != component:
        raise ValueError(f"{path.name}: describes {described.get('name')!r}, not {component!r}")
    # Syft lists the files it read as components too; a package is anything that is not a file.
    packages = [c for c in document.get("components") or [] if c.get("type") != "file"]
    if not packages:
        raise ValueError(f"{path.name}: lists no package, so it describes nothing")


def _checksum(path: Path) -> Path:
    """`sha256sum` format, so `sha256sum -c` checks a download beside it."""
    checksum = path.with_name(f"{path.name}.sha256")
    checksum.write_text(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    return checksum


def describe(
    target: str,
    component: str,
    version: str,
    out: Path,
    run: Run,
    extra: Sequence[str] = (),
) -> tuple[Path, Path]:
    """One document for ``target`` and its checksum."""
    document = out / name(component, version)
    run(
        [
            "scan",
            target,
            "--quiet",
            "--source-name",
            component,
            "--source-version",
            version,
            "--output",
            f"{FORMAT}={document}",
            *extra,
        ]
    )
    checked(document, component)
    return document, _checksum(document)


def build(
    version: str,
    out: Path,
    *,
    images: dict[str, str],
    source: Path | None,
    run: Run,
) -> list[Path]:
    """Every document and checksum, images first; refused before anything runs if an argument is
    not what a release passes."""
    if parse(version) is None:
        raise ValueError(f"'{version}' is not a release version (vX.Y.Z)")
    for component, reference in images.items():
        if not _NAME.fullmatch(component):
            raise ValueError(f"'{component}' is not an image name (aira-<word>)")
        if not _BY_DIGEST.fullmatch(reference):
            raise ValueError(f"'{reference}' is not pinned by digest (<image>@sha256:…)")
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for component, reference in images.items():
        written += describe(f"registry:{reference}", component, version, out, run)
    if source is not None:
        excludes = [part for pattern in SOURCE_EXCLUDES for part in ("--exclude", pattern)]
        written += describe(f"dir:{source}", SOURCE_NAME, version, out, run, excludes)
    return written


def _image(argument: str) -> tuple[str, str]:
    component, separator, reference = argument.partition("=")
    if not separator:
        raise argparse.ArgumentTypeError(f"'{argument}' is not NAME=REFERENCE")
    return component, reference


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the release tag, e.g. v0.1.8")
    parser.add_argument(
        "--image",
        type=_image,
        action="append",
        default=[],
        help="NAME=REFERENCE, the reference pinned by digest; once per image",
    )
    parser.add_argument("--source", type=Path, help="the checkout whose lock files to describe")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="directory to write into")
    parser.add_argument("--syft", default="syft", help="the Syft command to run")
    arguments = parser.parse_args(argv)
    try:
        paths = build(
            arguments.version,
            arguments.out,
            images=dict(arguments.image),
            source=arguments.source,
            run=_syft(arguments.syft),
        )
    except (ValueError, subprocess.CalledProcessError) as problem:
        print(problem, file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
