"""Every image says which release it is, and nothing between the build and the process can change
that (`FRD-157`).

v0.1.17's processes said `0.1.0` in their first line, on every span and metric and at `/healthz`,
because the version lived in the tag and nothing handed it to the build. What must hold, read from
the files that make it hold:

- each image's last stage takes the release, the commit and the time as build arguments, writes
  them into its environment — where every process reads them (`aira_common.build`) — and into its
  labels, where `docker inspect` shows them;
- the release job hands all three to every image it builds, and a job reads them back from what
  was pushed before any Release names the version;
- **no compose file passes any of them**: `${AIRA_GIT_COMMIT:-}` would set the variable to nothing
  over what the image says, and every process would state an empty commit.
"""

from __future__ import annotations

import re
from pathlib import Path

import compose_files
import yaml

from aira_common.build import IDENTITY_VARIABLES, LOCAL_VERSION

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
DOCKERFILES = {
    "gateway/Dockerfile": "aira-gateway",
    "management/backend/Dockerfile": "aira-management",
    "management/frontend/Dockerfile": "aira-frontend",
}

#: What the release job hands over, and what each image writes where.
HANDED_OVER = ("AIRA_BUILD_VERSION", "AIRA_GIT_COMMIT", "AIRA_BUILD_TIME")
LABELS = {
    "org.opencontainers.image.version": "AIRA_BUILD_VERSION",
    "org.opencontainers.image.revision": "AIRA_GIT_COMMIT",
    "org.opencontainers.image.created": "AIRA_BUILD_TIME",
}


def _last_stage(dockerfile: str) -> str:
    """The instructions of the stage that becomes the image, continuation lines joined."""
    text = (ROOT / dockerfile).read_text(encoding="utf-8").replace("\\\n", " ")
    return re.split(r"^FROM .*$", text, flags=re.MULTILINE)[-1]


def test_every_image_takes_its_identity_as_build_arguments_and_writes_it_down() -> None:
    for dockerfile in DOCKERFILES:
        stage = _last_stage(dockerfile)
        arguments = dict(re.findall(r"^ARG (\w+)=(.*)$", stage, flags=re.MULTILINE))
        assert set(HANDED_OVER) <= set(arguments), f"{dockerfile} takes {sorted(arguments)}"
        assert arguments["AIRA_BUILD_VERSION"] == LOCAL_VERSION, (
            f"{dockerfile}: a build that was told nothing must say it is no release"
        )
        environment = " ".join(re.findall(r"^ENV (.*)$", stage, flags=re.MULTILINE))
        labels = " ".join(re.findall(r"^LABEL (.*)$", stage, flags=re.MULTILINE))
        for name in HANDED_OVER:
            assert f"{name}=${name}" in environment.split(), (
                f"{dockerfile} does not write {name} where its processes read it"
            )
        for label, name in LABELS.items():
            assert f"{label}=${name}" in labels.split(), f"{dockerfile} has no label {label}"


def test_the_identity_comes_after_everything_a_new_commit_would_otherwise_rebuild() -> None:
    """An argument in scope invalidates every `RUN` after it, so a stage that declared these first
    would reinstall its packages for every commit."""
    for dockerfile in DOCKERFILES:
        stage = _last_stage(dockerfile)
        declared = stage.index("ARG AIRA_BUILD_VERSION")
        assert not re.search(r"^(RUN|COPY) ", stage[declared:], flags=re.MULTILINE), (
            f"{dockerfile} builds something after taking the identity"
        )


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _build_steps() -> dict[str, dict]:
    return {
        step["with"]["file"]: step
        for step in _workflow()["jobs"]["publish"]["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    }


def test_the_release_hands_every_image_the_tag_the_commit_and_the_time() -> None:
    steps = _build_steps()
    assert set(steps) == set(DOCKERFILES)
    for dockerfile, step in steps.items():
        handed = dict(
            line.split("=", 1) for line in str(step["with"].get("build-args", "")).splitlines()
        )
        assert handed == {
            "AIRA_BUILD_VERSION": "${{ steps.release.outputs.number }}",
            "AIRA_GIT_COMMIT": "${{ github.sha }}",
            "AIRA_BUILD_TIME": "${{ steps.release.outputs.built }}",
        }, f"{dockerfile} is built with {handed}"


def test_the_version_handed_over_is_the_tag_without_its_v_and_one_time_for_all() -> None:
    naming = next(
        step for step in _workflow()["jobs"]["publish"]["steps"] if step.get("id") == "release"
    )
    script = naming["run"]
    assert 'echo "number=${TAG#v}" >> "$GITHUB_OUTPUT"' in script
    assert 'echo "built=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$GITHUB_OUTPUT"' in script
    assert (
        _workflow()["jobs"]["publish"]["outputs"]["built"] == "${{ steps.release.outputs.built }}"
    )


def test_what_was_pushed_is_read_back_before_a_release_names_it() -> None:
    jobs = _workflow()["jobs"]
    assert jobs["identified"]["needs"] == ["publish"]
    assert "identified" in jobs["release"]["needs"], "a Release can name images that say otherwise"
    (check,) = [step for step in jobs["identified"]["steps"] if "release_identity.py" in str(step)]
    script = check["run"]
    assert (
        'python tools/release_identity.py "$TAG" --commit "$GITHUB_SHA" --built "$BUILT"' in script
    )
    for image in DOCKERFILES.values():
        assert f'--image "{image}=ghcr.io/$OWNER/{image}@$' in script, f"{image} is not checked"
    assert check["env"]["BUILT"] == "${{ needs.publish.outputs.built }}"


def test_no_compose_file_sets_what_the_image_says_it_is() -> None:
    for path in compose_files.ALL:
        text = path.read_text(encoding="utf-8")
        for name in IDENTITY_VARIABLES:
            assert not re.search(rf"^\s*-?\s*{name}\s*[:=]", text, flags=re.MULTILINE), (
                f"{path.name} sets {name} over what the image says"
            )
