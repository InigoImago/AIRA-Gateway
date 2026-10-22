"""The SBOMs a release attaches (`FRD-148`).

What a reader relies on without looking: each image's document describes the digest that was
pushed, the source's document is there for what the console's bundle hides, each is a CycloneDX 1.6
document that lists something, and the checksum beside it is its own. Syft is replaced by a stand-in
that writes what Syft writes — the real one was measured by hand (`FRD-148` §5.2).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import release_sbom as sbom

DIGEST = "sha256:" + "a" * 64
GATEWAY = f"ghcr.io/x/aira-gateway@{DIGEST}"


class Syft:
    """Writes a document like Syft's for whatever it is asked to scan, and remembers the asking."""

    def __init__(self, **override: Any) -> None:
        self.calls: list[list[str]] = []
        self.override = override

    def __call__(self, arguments: list[str]) -> None:
        self.calls.append(arguments)
        output = arguments[arguments.index("--output") + 1]
        assert output.startswith(f"{sbom.FORMAT}="), output
        component = arguments[arguments.index("--source-name") + 1]
        document = {
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "metadata": {"component": {"type": "container", "name": component}},
            "components": [
                {"type": "library", "name": "fastapi", "purl": "pkg:pypi/fastapi@0.1"},
                {"type": "file", "name": "/app/x.py"},
            ],
            **self.override,
        }
        Path(output.split("=", 1)[1]).write_text(json.dumps(document))


def _build(tmp_path: Path, syft: Syft, **kwargs: Any) -> list[Path]:
    return sbom.build(
        kwargs.pop("version", "v1.2.3"),
        tmp_path,
        images=kwargs.pop("images", {"aira-gateway": GATEWAY}),
        source=kwargs.pop("source", Path("/checkout")),
        run=syft,
    )


def test_one_document_per_image_and_one_of_the_source(tmp_path: Path) -> None:
    written = _build(
        tmp_path,
        Syft(),
        images={"aira-gateway": GATEWAY, "aira-frontend": f"ghcr.io/x/aira-frontend@{DIGEST}"},
    )

    assert [path.name for path in written] == [
        "aira-gateway-v1.2.3.cdx.json",
        "aira-gateway-v1.2.3.cdx.json.sha256",
        "aira-frontend-v1.2.3.cdx.json",
        "aira-frontend-v1.2.3.cdx.json.sha256",
        "aira-source-v1.2.3.cdx.json",
        "aira-source-v1.2.3.cdx.json.sha256",
    ]


def test_an_image_is_scanned_from_the_registry_by_the_digest_that_was_pushed(
    tmp_path: Path,
) -> None:
    """`registry:` reads what GHCR holds — not a local image of the same name, which on a runner
    is a rebuild — and the digest is what a deployment pulls."""
    syft = Syft()
    _build(tmp_path, syft, source=None)

    (call,) = syft.calls
    assert call[:2] == ["scan", f"registry:{GATEWAY}"]
    assert call[call.index("--source-version") + 1] == "v1.2.3"
    assert f"{sbom.FORMAT}={tmp_path / 'aira-gateway-v1.2.3.cdx.json'}" in call


def test_the_source_leaves_out_the_test_tooling(tmp_path: Path) -> None:
    syft = Syft()
    _build(tmp_path, syft, images={})

    (call,) = syft.calls
    assert call[:2] == ["scan", "dir:/checkout"]
    assert call[call.index("--source-name") + 1] == "aira-source"
    excluded = [call[i + 1] for i, part in enumerate(call) if part == "--exclude"]
    assert excluded == ["./e2e/**", "./tools/clients/**"]


def test_the_checksum_is_the_documents_and_sha256sum_accepts_it(tmp_path: Path) -> None:
    document, checksum = _build(tmp_path, Syft(), source=None)

    digest = hashlib.sha256(document.read_bytes()).hexdigest()
    assert checksum.read_text() == f"{digest}  {document.name}\n"
    subprocess.run(  # noqa: S603
        ["sha256sum", "-c", checksum.name],  # noqa: S607
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    ("override", "said"),
    [
        ({"bomFormat": "SPDX"}, "not a CycloneDX document"),
        ({"specVersion": "1.4"}, "CycloneDX 1.4, not 1.6"),
        ({"metadata": {"component": {"name": "aira-frontend"}}}, "describes 'aira-frontend'"),
        ({"components": [{"type": "file", "name": "/etc/os-release"}]}, "lists no package"),
        ({"components": []}, "lists no package"),
    ],
    ids=["another-format", "an-older-spec", "another-component", "files-only", "nothing"],
)
def test_a_document_a_reader_cannot_use_stops_the_release(
    tmp_path: Path, override: dict[str, Any], said: str
) -> None:
    """Published, an empty or mislabelled document claims the release contains nothing — worse than
    no document, because it is believed. No checksum is written for it."""
    with pytest.raises(ValueError, match=said):
        _build(tmp_path, Syft(**override), source=None)
    assert not list(tmp_path.glob("*.sha256"))


def test_a_document_that_is_not_json_stops_the_release(tmp_path: Path) -> None:
    def broken(arguments: list[str]) -> None:
        Path(arguments[arguments.index("--output") + 1].split("=", 1)[1]).write_text("{")

    with pytest.raises(ValueError, match="not a JSON document"):
        sbom.build("v1.2.3", tmp_path, images={"aira-gateway": GATEWAY}, source=None, run=broken)


@pytest.mark.parametrize(
    ("version", "images", "said"),
    [
        ("v1.2", {}, "not a release version"),
        ("$(id)", {}, "not a release version"),
        ("v1.2.3", {"aira-gateway": "ghcr.io/x/aira-gateway:v1.2.3"}, "not pinned by digest"),
        ("v1.2.3", {"aira-gateway": f"{GATEWAY} --config /x"}, "not pinned by digest"),
        ("v1.2.3", {"../x": GATEWAY}, "not an image name"),
    ],
    ids=["short-version", "command-in-version", "by-tag", "smuggled-flag", "path-in-name"],
)
def test_what_a_release_would_not_pass_is_refused_before_syft_runs(
    tmp_path: Path, version: str, images: dict[str, str], said: str
) -> None:
    """A tag can move after the scan; a version and a name reach file names."""
    syft = Syft()

    with pytest.raises(ValueError, match=said):
        _build(tmp_path / "out", syft, version=version, images=images)
    assert syft.calls == []
    assert not (tmp_path / "out").exists()


def test_the_command_reports_a_refusal_and_a_failed_scan_as_failure(tmp_path: Path) -> None:
    assert sbom.main(["v1.2.3", "--image", "aira-gateway=by:tag", "--out", str(tmp_path)]) == 1
    assert (
        sbom.main(
            [
                "v1.2.3",
                "--image",
                f"aira-gateway={GATEWAY}",
                "--syft",
                "false",
                "--out",
                str(tmp_path),
            ]
        )
        == 1
    )
