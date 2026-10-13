"""What must hold about the release jobs (`FRD-137`, `ADR-0030`).

A workflow is configuration, so nothing here runs it. What is checkable by reading is the part that
matters: **a published version is a tested version, it says what it is, and it is never reused.**
Each of those is a property of the jobs' *shape* — what they depend on, what guards them, what order
their steps are in — and every one is a line somebody could delete while the workflow still parses
and still publishes. That is the definition of a rule only a reviewer enforces.

Asserted against the parsed YAML wherever the structure carries the meaning, because a grep for
`needs:` passes on a `needs:` that names the wrong jobs.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
MAKEFILE = ROOT / "Makefile"

#: `yaml.safe_load` turns the key `on:` into the boolean `True`, which is why this is spelled as a
#: constant rather than as a string that reads like a mistake.
ON = True


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _job(name: str) -> dict:
    return _workflow()["jobs"][name]


def _step(job: str, fragment: str) -> dict:
    for step in _job(job)["steps"]:
        haystack = f"{step.get('name', '')} {step.get('uses', '')} {step.get('run', '')}"
        if fragment in haystack:
            return step
    raise AssertionError(f"no step in {job!r} mentioning {fragment!r}")


def _pushed_tags() -> str:
    return " ".join(str(step.get("with", {}).get("tags", "")) for step in _job("publish")["steps"])


def test_a_version_tag_is_what_starts_a_release() -> None:
    """`ADR-0030`. The trigger is the tag, and the pattern is the release pattern — a looser one
    would start a release for `v2-spike`."""
    assert _workflow()[ON]["push"]["tags"] == ["v*.*.*"]


def test_nothing_is_published_that_the_gates_and_the_description_check_did_not_pass() -> None:
    """**The property the release exists behind.** Drop one name from `needs` and the workflow still
    parses, still publishes, and publishes something no test looked at — or something that says
    nothing about itself, depending on which name went."""
    needs = _job("publish")["needs"]

    assert set(needs) == {"python", "frontend", "stack", "described"}, (
        f"the release depends on {needs}; every check job and the description check must gate it"
    )


def test_only_a_tag_publishes() -> None:
    """Both release jobs are gated on the ref being a tag. Without it, `described` would fail every
    ordinary push — and `publish` would run on one."""
    for job in ("described", "publish"):
        assert "refs/tags/v" in str(_job(job)["if"]), (
            f"the {job} job is not gated on a tag ref, so it takes part in ordinary pushes"
        )


def test_the_description_check_does_not_wait_for_the_gates() -> None:
    """**The reason it is a job of its own.** A bare annotation is the one failure a tagger can
    still fix for free, and it costs seconds to detect — behind `needs` it would be reported after
    forty minutes of gates, by which time everybody is already waiting for the release."""
    assert "needs" not in _job("described"), (
        "the description check waits for the gates, so the cheapest failure is reported last"
    )


def test_the_annotation_is_checked_by_the_tool_a_tagger_can_run() -> None:
    """The refusal met in CI must be the refusal that could have been met before pushing — otherwise
    the check is a surprise rather than a guard."""
    check = _step("described", "release_notes.py --check")

    assert 'release_notes.py --check "$TAG"' in str(check["run"])
    assert _job("described")["env"]["TAG"] == "${{ github.ref_name }}"
    assert "release-check:" in MAKEFILE.read_text(), (
        "there is no make target for the annotation check, so a tagger cannot run it first"
    )


#: Expressions a script may have spliced into its text. A commit digest is hex, whatever the pusher
#: did; every other value an event carries — a tag or branch name above all — is chosen by them.
SPLICEABLE = {"${{ github.sha }}"}


def test_no_script_has_a_value_the_pusher_chooses_written_into_it() -> None:
    """**A tag name is input, and in a `run:` block an expression is code.** Actions substitutes
    `${{ github.ref_name }}` into the script text before the shell starts, Git allows `$(…)`, `;`
    and backticks in a tag name, and `v*.*.*` matches such a name — so a pushed tag could run a
    command in a job holding `contents: write` and `packages: write`. The name travels through the
    environment instead, where the shell treats a quoted `"$TAG"` as data."""
    spliced = [
        f"{job}: {step.get('name', step.get('run', '')[:40])!r} has {expression}"
        for job, body in _workflow()["jobs"].items()
        for step in body.get("steps", [])
        for expression in re.findall(r"\$\{\{[^}]*\}\}", str(step.get("run", "")))
        if " ".join(expression.split()) not in SPLICEABLE
    ]

    assert not spliced, "an event's value is written into a script:\n  " + "\n  ".join(spliced)


def test_a_tag_that_is_not_a_release_version_stops_both_jobs_before_anything_runs() -> None:
    """Quoting makes a hostile name inert; refusing it first means no step ever sees one. The
    spelling is `tools/release_version.py`'s, so CI and `make release-check` agree on what a
    release tag is."""
    sys.path.insert(0, str(ROOT / "tools"))
    import release_version

    for job in ("described", "publish"):
        first = _job(job)["steps"][0]
        assert first.get("name") == "Refuse a tag name that is not a release version", (
            f"{job} runs something before it has looked at the tag's name"
        )
        pattern = re.search(r'\[\[ "\$TAG" =~ \^(.*)\$ \]\]', str(first["run"]))
        assert pattern, f"{job}'s first step no longer matches $TAG against a pattern"
        assert pattern.group(1) == release_version._RELEASE_TAG.pattern
        assert "exit 1" in str(first["run"])


def test_a_tag_that_is_not_on_the_default_branch_is_refused() -> None:
    """A tag can be pushed from anywhere, so "which branch" is not a property of the push. The
    answerable question is whether the tagged commit is in the history `main` names — a version
    naming a commit nobody deploys is worse than no version."""
    guard = _step("described", "merge-base --is-ancestor")

    run = str(guard["run"])

    assert "git fetch --no-tags origin main" in run
    # **The command, matched as a command.** The first version asserted `"FETCH_HEAD" in run` and
    # was satisfied by the step's own explanatory *comment*, which names `FETCH_HEAD` while
    # explaining why it is used — so the guard passed with the comparison pointed back at
    # `origin/main`. Its mutation caught it. Written as the shape of the comparison, prose cannot
    # satisfy it.
    #
    # Why `FETCH_HEAD` and not `origin/main`: `actions/checkout` configures a refspec for the ref it
    # checked out — a tag here — so `refs/remotes/origin/main` need not exist, and `merge-base`
    # against an unresolvable ref exits non-zero. The guard would refuse *every* release.
    # `[^\n]*` rather than `\S+`: the argument is `"${{ github.sha }}"`, which has spaces in it.
    # Confined to one line, so a comment elsewhere in the block cannot satisfy it.
    assert re.search(r"--is-ancestor[^\n]*\bFETCH_HEAD\b", run), (
        "the ancestor check compares against a remote-tracking ref a tag checkout need not have"
    )
    assert "exit 1" in run


def test_a_version_older_than_the_newest_release_is_refused() -> None:
    """`latest` would move **backwards**. Releases here go forward only; a backport would need a
    second moving tag per series, which is a feature and not an oversight."""
    guard = _step("described", "releases go forward only")

    assert "release_version.py --current" in str(guard["run"])
    assert "exit 1" in str(guard["run"])


def test_a_version_that_is_already_released_is_refused_rather_than_republished() -> None:
    """**The tag cannot be the lock any more.** It exists before the job starts, by definition — so
    re-running a run whose images already landed would push over a published version. The GitHub
    Release is the lock instead, and an image tag that changes under a deployment is the one thing a
    version number exists to prevent."""
    guard = _step("publish", "already released")

    assert "gh release view" in str(guard["run"])
    assert "exit 1" in str(guard["run"])


def test_the_notes_are_published_after_the_images_and_not_before() -> None:
    """A Release created first and then a failed build claims images that do not exist. In this
    order a failed build leaves a tag with **no Release against it** — visibly unfinished, rather
    than a version that silently names nothing."""
    names = [str(step.get("name", step.get("uses", ""))) for step in _job("publish")["steps"]]
    pushes = [i for i, name in enumerate(names) if name.startswith("Build and push")]
    notes = names.index("Publish the release notes")

    assert pushes, "nothing in the release job pushes an image"
    assert max(pushes) < notes, (
        "the release notes are published before the last image, so a failed build can leave a "
        "Release claiming images that were never pushed"
    )


def test_the_notes_are_the_tagger_s_own_words_and_not_a_generated_list() -> None:
    """The tag's own message is the description (`ADR-0030`). A commit list is evidence, not a
    description: it answers *what moved* and never *why it matters*, which is the whole reason this
    mechanism replaced one that wrote `Release v0.1.0` by itself."""
    publish = _step("publish", "release_notes.py")

    assert "--notes-file" in str(publish["run"]), (
        "the Release body does not come from the rendered notes"
    )
    assert "--generate-notes" not in str(publish["run"]), (
        "GitHub's generated notes replaced the tagger's own description"
    )


def test_both_release_jobs_check_out_the_history_the_notes_need() -> None:
    """The notes quote the commits since the previous release, and the forward-only check compares
    against every tag. `--depth 1` sees no tags at all, which makes every release look like the
    first one."""
    for job in ("described", "publish"):
        checkout = _step(job, "actions/checkout")
        assert checkout["with"]["fetch-depth"] == 0, f"{job} checks out shallowly"


def test_all_three_images_are_published_at_the_release_version() -> None:
    """A release is the three images at one version. Two of them produce a deployment whose console
    is a version older than its gateway — the state a version number exists to make impossible."""
    pushed = _pushed_tags()
    version = "${{ steps.release.outputs.version }}"

    for image in ("aira-gateway", "aira-management", "aira-frontend"):
        # **The version tag specifically**, not merely the image name. A mutation that renamed
        # `aira-frontend`'s versioned tag survived the first draft of this test, because the same
        # image's `:latest` and `:sha-…` lines kept the name present.
        assert f"/{image}:{version}" in pushed, f"{image} is not published at the release version"


def test_every_published_image_carries_the_commit_it_was_built_from() -> None:
    """`sha-<commit>` answers *"is this instance running what I think it is?"* without trusting that
    a version tag was never moved, and it is the reference a rolling update advances to
    (`FRD-127`)."""
    for step in _job("publish")["steps"]:
        tags = str(step.get("with", {}).get("tags", ""))
        if "ghcr.io/" not in tags:
            continue
        assert "sha-${{ github.sha }}" in tags, (
            f"{step.get('name')} publishes without a commit tag; checked per image rather than "
            "once over all of them, because one image missing it is the case that matters"
        )


def test_the_release_job_asks_for_exactly_the_two_permissions_it_needs() -> None:
    """Named rather than inherited, and no more than two. `packages: write` is what GHCR accepts
    `GITHUB_TOKEN` for in this repository's own namespace — which is why no personal access token is
    configured anywhere. `contents: write` is for the GitHub Release."""
    assert _job("publish")["permissions"] == {"contents": "write", "packages": "write"}
    assert _workflow()["permissions"] == {"contents": "read"}, (
        "the workflow's default permissions widened; only the release job may write"
    )


def test_no_personal_access_token_is_referenced() -> None:
    """If this ever fails, somebody added a secret that did not need to exist — and a long-lived
    token with package scope is a credential this project should not be holding."""
    body = WORKFLOW.read_text()

    assert "secrets.GITHUB_TOKEN" in body, "the registry login no longer uses the built-in token"
    for forbidden in ("secrets.GHCR_", "secrets.CR_PAT", "secrets.DOCKER_"):
        assert forbidden not in body, f"{forbidden} is a long-lived token the built-in one replaces"


def test_a_release_run_is_never_cancelled_by_a_newer_one() -> None:
    """Cancelling between two image pushes leaves a version half published — one image at `vX.Y.Z`
    and two at the previous one, which no reader of a version number would expect."""
    cancel = str(_workflow()["concurrency"]["cancel-in-progress"])

    assert "refs/tags/" in cancel, "a release run can be cancelled between two image pushes"


def test_both_release_jobs_restore_the_tag_object_before_reading_it() -> None:
    """**What `v0.1.1` was published with.** `actions/checkout` creates `refs/tags/<name>` pointing
    at the *commit* it checked out, so the annotated tag object — the thing that carries the notes —
    is not in the clone. `%(contents)` of a ref pointing at a commit is the commit's message, which
    has a subject and a body and passes every description rule, so nothing failed: the Release went
    out carrying `def905f`'s commit message and a title to match.

    Both jobs need it. The `described` job without it validates a commit message and reports
    success, which is worse than not checking at all — it is a gate that says the thing it did not
    look at is fine.
    """
    for job in ("described", "publish"):
        restore = _step(job, "Restore the annotated tag object")
        run = str(restore["run"])

        assert "git fetch --force origin" in run, f"{job} does not re-fetch the tag object"
        assert "refs/tags/" in run
        assert "cat-file -t" in run and "exit 1" in run, (
            f"{job} re-fetches the tag and does not assert it is one, so a future version of the "
            "checkout action flattening it again would publish the nearest available prose"
        )


def test_the_release_title_comes_from_the_tool_that_checks_the_annotation() -> None:
    """The title was a second `git tag --format` in the workflow, unchecked — so when the tag
    arrived flattened, the body and the title were both the commit's and only the body looked like
    a mistake. One owner for *what the tag says*, and it goes through the rules."""
    publish = _step("publish", "gh release create")
    run = str(publish["run"])

    assert "release_notes.py --subject" in run, "the title does not come from the checked tool"
    assert "git tag -l --format" not in run, (
        "the title is read from Git a second time, beside the body that already was"
    )


def _recipe(target: str) -> str:
    """The **commands** of a Make target, with its `@#` comment lines removed.

    **Both mutations of the test below survived its first draft**, for the third recorded instance
    of the same shape: the guard asked whether `docs/RELEASE-TEMPLATE.txt` and `--cleanup=strip`
    appeared in the target, and the comment block *explaining why they are there* kept them
    appearing after the commands were gone. `PB14` was the same thing in a workflow's `run:`
    block, `IM11` in a Compose file one plane along.

    A comment is prose about a rule; only the recipe is the rule. This repository comments heavily
    and on purpose, which makes "is the string in the file" a guard it will keep defeating.
    """
    body = MAKEFILE.read_text()
    block = body.partition(f"\n{target}:")[2].partition("\n\n")[0]
    return "\n".join(
        line for line in block.splitlines() if not line.strip().startswith(("@#", "#"))
    )


def test_the_tag_message_is_written_from_the_template() -> None:
    """A blank annotation is a prompt to improvise, and what gets improvised is a sentence
    restating the version — `Release v0.1.0`, which is where this whole mechanism started. The
    editor opens on the template instead, and `--cleanup=strip` is what keeps its guidance out of
    the published notes."""
    recipe = _recipe("release-tag")

    assert "docs/RELEASE-TEMPLATE.txt" in recipe, "the editor opens on an empty buffer"
    assert "--cleanup=strip" in recipe, (
        "without it the template's own guidance lines are published as release notes"
    )
    assert "release_notes.py --check" in recipe and "git tag -d" in recipe, (
        "a rejected annotation leaves the tag behind, so the version number is spent"
    )
