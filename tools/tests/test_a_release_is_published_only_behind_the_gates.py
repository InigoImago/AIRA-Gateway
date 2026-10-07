"""What must hold about the release job (`FRD-137`, `ADR-0029`).

A workflow is configuration, so nothing here runs it. What is checkable by reading is the part that
matters: **a published version is a tested version, and a version number is never reused.** Both are
properties of the job's *shape* — what it depends on, what guards it, what order its steps are in —
and every one of them is a line somebody could delete while the workflow still parses and still
publishes. That is the definition of a rule only a reviewer enforces.

Asserted against the parsed YAML rather than against the file's text wherever the structure carries
the meaning, because a grep for `needs:` passes on a `needs:` that names the wrong jobs.
"""

from __future__ import annotations

import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
MAKEFILE = ROOT / "Makefile"

#: `yaml.safe_load` turns the key `on:` into the boolean `True`, which is why this is spelled as a
#: constant instead of as a string that reads like a mistake.
ON = True


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _publish() -> dict:
    return _workflow()["jobs"]["publish"]


def _step(name_fragment: str) -> dict:
    for step in _publish()["steps"]:
        if name_fragment in str(step.get("name", "")) or name_fragment in str(step.get("uses", "")):
            return step
    raise AssertionError(f"no step mentioning {name_fragment!r}")


def test_nothing_is_published_that_the_three_gates_did_not_pass() -> None:
    """**The property the whole job exists for.** Drop one name from `needs` and the workflow still
    parses, still publishes, and publishes something no test looked at — the unit tests, the browser
    tests or the live stack, depending on which name went."""
    needs = _publish()["needs"]

    assert set(needs) == {"python", "frontend", "stack"}, (
        f"the release depends on {needs}; every check job must gate it, or a version can be "
        "published that one layer never saw"
    )


def test_an_ordinary_push_never_publishes() -> None:
    """The job is manual by *gate*, not by hope. Without the condition, every push to every branch
    would cut a release — and `on.push.branches` here is `**`."""
    assert "inputs.release" in str(_publish()["if"]), (
        "the release job is not gated on the dispatch input, so it runs on ordinary pushes"
    )
    assert _workflow()[ON]["workflow_dispatch"]["inputs"]["release"]["default"] is False, (
        "the release input defaults to true, so an unmodified dispatch publishes"
    )


def test_a_release_can_only_be_cut_from_the_default_branch() -> None:
    """A version tag on a feature branch names a commit that is not in the history anybody deploys,
    and the tag outlives the branch."""
    guard = _step("Refuse to release from anywhere but the default branch")

    assert "refs/heads/main" in str(guard["if"])
    assert "exit 1" in str(guard["run"])


def test_the_version_is_read_from_a_checkout_that_can_see_the_tags() -> None:
    """The tags **are** the version (`ADR-0029`). A shallow clone sees none, "no tag" reads as
    "never released", and the release would publish `v0.1.0` over whatever is already out — the
    worst available failure, because it overwrites rather than refuses."""
    checkout = _step("actions/checkout")

    assert checkout["with"]["fetch-depth"] == 0, (
        "the release checkout is shallow, so it cannot see the tags it derives the version from"
    )


def test_the_arithmetic_lives_in_the_tool_and_not_in_yaml() -> None:
    """A second implementation is a second place for the ordering bug: `v0.10.0` sorts below
    `v0.9.0` as a string, and a release cut from a `sort | tail -1` goes backwards the first time a
    minor number reaches ten. `tools/release_version.py` has the test for that; YAML would not."""
    body = WORKFLOW.read_text()

    assert "tools/release_version.py" in body, "the workflow computes the version by itself"
    assert "next-version:" in MAKEFILE.read_text(), (
        "there is no make target for the version, so the release command cannot be run locally"
    )


def test_a_version_that_already_exists_is_refused_rather_than_overwritten() -> None:
    """The Git tag is the lock. Two dispatches racing, or a rerun of a release that got as far as
    pushing images, would otherwise move a published version in place — and an image tag that
    changes under a deployment is the one thing a version number is for."""
    guard = _step("Refuse to publish a version that already exists")

    assert "refs/tags/" in str(guard["run"])
    assert "exit 1" in str(guard["run"])


def test_the_tag_is_pushed_after_the_images_and_not_before() -> None:
    """A tag pushed first and then a failed build leaves a version naming no image — and the next
    release counts from it, so the series skips a number to hide the failure. In this order a failed
    publish leaves nothing behind and can simply be run again."""
    names = [str(step.get("name", step.get("uses", ""))) for step in _publish()["steps"]]
    pushes = [i for i, name in enumerate(names) if name.startswith("Build and push")]
    tagging = names.index("Tag the release")

    assert pushes, "nothing in the release job pushes an image"
    assert max(pushes) < tagging, (
        "the Git tag is created before the last image is pushed, so a failed build can leave a "
        "version that names no image"
    )


def test_all_three_images_are_published_together() -> None:
    """A release is the three images at one version. Publishing two of them produces a deployment
    whose console is a version older than its gateway, which is exactly the state the version number
    is supposed to make impossible."""
    pushed = " ".join(str(step.get("with", {}).get("tags", "")) for step in _publish()["steps"])
    version = "${{ steps.version.outputs.version }}"

    for image in ("aira-gateway", "aira-management", "aira-frontend"):
        # **The version tag specifically**, not merely the image name. A mutation that renamed
        # `aira-frontend`'s versioned tag survived the first draft of this test, because the same
        # image's `:latest` and `:sha-…` lines kept the name present — so the assertion passed
        # about a release in which two images carried the version and one did not.
        assert f"/{image}:{version}" in pushed, (
            f"{image} is not published at the release version, so a deployment can end up with a "
            "console one version older than its gateway"
        )


def test_the_version_tag_is_not_the_only_tag_an_image_carries() -> None:
    """`sha-<commit>` ties an image to the commit a rolling update is advancing to (`FRD-127`), and
    it is the tag that answers *"is this instance running what I think it is?"* without trusting
    that a version tag was never moved."""
    for step in _publish()["steps"]:
        tags = str(step.get("with", {}).get("tags", ""))
        if "ghcr.io/" not in tags:
            continue
        assert "sha-${{ github.sha }}" in tags, (
            f"{step.get('name')} publishes without a commit tag; checked per image rather than "
            "once over all of them, because one image missing it is the case that matters"
        )


def test_the_job_asks_for_exactly_the_two_permissions_it_needs() -> None:
    """Named rather than inherited, and no more than two. `packages: write` is what GHCR accepts
    `GITHUB_TOKEN` for in this repository's own namespace — which is why no personal access token is
    configured anywhere. `contents: write` is for the tag the *next* release reads the version from.
    """
    assert _publish()["permissions"] == {"contents": "write", "packages": "write"}
    assert _workflow()["permissions"] == {"contents": "read"}, (
        "the workflow's default permissions widened; only the release job may write"
    )


def test_no_personal_access_token_is_referenced() -> None:
    """If this ever fails, somebody added a secret that did not need to exist — and a long-lived
    token with package scope is a credential in a place `ADR-0029` says it should not be."""
    body = WORKFLOW.read_text()

    assert "secrets.GITHUB_TOKEN" in body, "the registry login no longer uses the built-in token"
    for forbidden in ("secrets.GHCR_", "secrets.CR_PAT", "secrets.DOCKER_"):
        assert forbidden not in body, f"{forbidden} is a long-lived token the built-in one replaces"
