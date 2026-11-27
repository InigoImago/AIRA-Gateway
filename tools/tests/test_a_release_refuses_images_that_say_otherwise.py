"""A release whose images do not say they are that release is refused (`FRD-157`).

`tools/release_identity.py` reads each pushed image back by digest and holds its labels — and, for
the two that run Python, what its processes state — to the tag, the commit and the build time.
Docker is replaced by a stand-in that answers as the real one does; the real images were built
with these arguments and asked by hand (`FRD-157` §5).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import release_identity as identity

DIGEST = "sha256:" + "a" * 64
COMMIT = "0123456789abcdef0123456789abcdef01234567"
BUILT = "2026-11-27T12:00:00Z"
IMAGES = {
    "aira-gateway": f"ghcr.io/x/aira-gateway@{DIGEST}",
    "aira-management": f"ghcr.io/x/aira-management@{DIGEST}",
    "aira-frontend": f"ghcr.io/x/aira-frontend@{DIGEST}",
}


class Docker:
    """Answers `pull`, `inspect` and `run` as Docker does, with what each image was built as —
    its labels and its processes' statement each changed on their own, as the two can differ."""

    def __init__(self, **changed: dict[str, dict[str, Any]]) -> None:
        self.calls: list[list[str]] = []
        self.changed = changed

    def _said(self, reference: str, where: str, built: dict[str, Any]) -> dict[str, Any]:
        name = reference.split("/")[-1].split("@")[0].replace("-", "_")
        return {**built, **self.changed.get(name, {}).get(where, {})}

    def __call__(self, arguments: list[str]) -> str:
        self.calls.append(arguments)
        if arguments[0] == "pull":
            return ""
        if arguments[0] == "inspect":
            labels = {
                identity.LABEL_VERSION: "0.1.18",
                identity.LABEL_REVISION: COMMIT,
                identity.LABEL_CREATED: BUILT,
            }
            return json.dumps(self._said(arguments[-1], "labels", labels))
        if arguments[0] == "run":
            reference = arguments[arguments.index("python") + 1]
            statement = {
                "version": "0.1.18",
                "commit": COMMIT,
                "time": BUILT,
                "number": None,
                "branch": None,
            }
            return json.dumps(self._said(reference, "statement", statement))
        raise AssertionError(arguments)


def _check(docker: Docker, **given: Any) -> list[str]:
    return identity.check(
        given.get("tag", "v0.1.18"),
        commit=given.get("commit", COMMIT),
        built=given.get("built", BUILT),
        images=given.get("images", IMAGES),
        docker=docker,
    )


def test_images_that_say_they_are_the_release_pass() -> None:
    docker = Docker()

    assert _check(docker) == []
    # Each image pulled by its digest, its labels read, and the two that run Python asked.
    asked = [call for call in docker.calls if call[0] == "run"]
    assert [call[-3] for call in asked] == [IMAGES["aira-gateway"], IMAGES["aira-management"]]
    assert all(call[-2:] == ["-m", "aira_common.build"] for call in asked)
    assert [call for call in docker.calls if call[0] == "pull"] == [
        ["pull", "--quiet", reference] for reference in IMAGES.values()
    ]


def test_an_image_whose_processes_say_another_version_is_refused() -> None:
    """v0.1.17: the labels could be right and every process still say `0.1.0`."""
    problems = _check(Docker(aira_gateway={"statement": {"version": "0.1.0"}}))

    assert problems == [
        "aira-gateway: what its processes state as version is '0.1.0', not '0.1.18'"
    ]


def test_an_image_labelled_with_another_version_is_refused() -> None:
    problems = _check(Docker(aira_frontend={"labels": {identity.LABEL_VERSION: "0.0.0+local"}}))

    assert problems == [
        "aira-frontend: its label org.opencontainers.image.version is '0.0.0+local', not '0.1.18'"
    ]


def test_an_image_without_its_commit_or_its_time_is_refused() -> None:
    problems = _check(
        Docker(
            aira_management={
                "labels": {identity.LABEL_REVISION: "", identity.LABEL_CREATED: ""},
                "statement": {"commit": None, "time": None},
            }
        )
    )

    assert len(problems) == 4
    assert all(problem.startswith("aira-management: ") for problem in problems)


def test_an_image_named_by_a_tag_that_can_move_is_refused_unread() -> None:
    docker = Docker()

    problems = _check(docker, images={"aira-gateway": "ghcr.io/x/aira-gateway:v0.1.18"})

    assert problems == ["aira-gateway: ghcr.io/x/aira-gateway:v0.1.18 is not pinned by digest"]
    assert docker.calls == []


def test_nothing_to_check_is_no_pass() -> None:
    assert _check(Docker(), images={}) == ["no image was named, so none says what it is"]
    assert _check(Docker(), built=" ") == ["no build time was handed over, so none can be checked"]


def test_a_tag_that_is_no_release_version_is_refused() -> None:
    with pytest.raises(ValueError, match="not a release version"):
        _check(Docker(), tag="latest")
    assert identity.release_number("v0.1.18") == "0.1.18"


def test_the_command_fails_and_says_why_in_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(
        identity, "_docker", lambda command: Docker(aira_gateway={"statement": {"version": "x"}})
    )
    summary = tmp_path / "summary.md"

    code = identity.main(
        [
            "v0.1.18",
            "--commit",
            COMMIT,
            "--built",
            BUILT,
            "--image",
            f"aira-gateway={IMAGES['aira-gateway']}",
            "--summary",
            str(summary),
        ]
    )

    assert code == 1
    assert "::error::aira-gateway: what its processes state as version is 'x'" in (
        capsys.readouterr().err
    )
    assert "aira-gateway: what its processes state" in summary.read_text(encoding="utf-8")


def test_the_command_passes_and_says_so(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(identity, "_docker", lambda command: Docker())

    code = identity.main(
        ["v0.1.18", "--commit", COMMIT, "--built", BUILT]
        + [argument for name, ref in IMAGES.items() for argument in ("--image", f"{name}={ref}")]
    )

    assert code == 0
    assert capsys.readouterr().out.count("says it is v0.1.18") == 3


def test_a_check_that_could_not_be_made_fails_the_release(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """Docker failing — a pull refused, an image that cannot start — is no pass."""

    def broken(command: str) -> Any:
        def run(arguments: list[str]) -> str:
            raise identity.subprocess.CalledProcessError(1, ["docker", *arguments])

        return run

    monkeypatch.setattr(identity, "_docker", broken)

    code = identity.main(
        ["v0.1.18", "--commit", COMMIT, "--built", BUILT, "--image", f"x={IMAGES['aira-gateway']}"]
    )

    assert code == 1
    assert "the check could not be made" in capsys.readouterr().err
