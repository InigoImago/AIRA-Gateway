"""What must hold about the release jobs (`FRD-137`, `ADR-0030`).

A workflow is configuration, so nothing here runs it. What is checkable by reading is the part that
matters: **a published version is a tested version, it says what it is, and it is never reused.**
Each of those is a property of the jobs' *shape* — what they depend on, what guards them, what order
their steps are in — and every one is a line somebody could delete while the workflow still parses
and still publishes. That is the definition of a rule only a reviewer enforces.

Asserted against the parsed YAML wherever the structure carries the meaning, because a grep for
`needs:` passes on a `needs:` that names the wrong jobs.

**And what a release is built from, and who could have changed it** (`ADR-0043`): every action is
pinned to a commit, no checkout leaves the token in the clone, a job that can write runs only code
it has to trust, no release job restores a cache, and every file and image a release names is
signed before the release exists.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
MAKEFILE = ROOT / "Makefile"

#: `yaml.safe_load` turns the key `on:` into the boolean `True`, which is why this is spelled as a
#: constant rather than as a string that reads like a mistake.
ON = True

#: The jobs that release a version, as opposed to the jobs that check a commit.
RELEASE_JOBS = {"described", "publish", "inventory", "release"}


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
    nothing about itself, depending on which name went.

    Every job that is not a release job is a check, read from the workflow rather than listed here:
    a check job added later gates the release without anybody having to remember that it should.
    The list below is the floor, so a workflow that lost its checks does not pass by having none.
    """
    checks = set(_workflow()["jobs"]) - RELEASE_JOBS
    needs = _job("publish")["needs"]

    assert {"python", "frontend", "stack", "browser"} <= checks
    assert set(needs) == checks | {"described"}, (
        f"the release depends on {needs}; every check job and the description check must gate it"
    )
    # The jobs after the push wait for it, and so for everything it waited for.
    for job in ("inventory", "release"):
        assert "publish" in _job(job)["needs"], f"{job} can run before the gates have passed"


def test_only_a_tag_publishes() -> None:
    """Every release job is gated on the ref being a tag. Without it, `described` would fail every
    ordinary push — and the others would run on one."""
    for job in sorted(RELEASE_JOBS):
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


def test_a_tag_that_is_not_a_release_version_stops_every_release_job_before_anything_runs() -> None:
    """Quoting makes a hostile name inert; refusing it first means no step ever sees one. The
    spelling is `tools/release_version.py`'s, so CI and `make release-check` agree on what a
    release tag is."""
    sys.path.insert(0, str(ROOT / "tools"))
    import release_version

    for job in sorted(RELEASE_JOBS):
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


def _names(job: str) -> list[str]:
    return [str(step.get("name", step.get("uses", ""))) for step in _job(job)["steps"]]


def test_the_notes_are_published_after_the_images_and_not_before() -> None:
    """A Release created first and then a failed build claims images that do not exist. In this
    order a failed build leaves a tag with **no Release against it** — visibly unfinished, rather
    than a version that silently names nothing. The images are pushed by `publish` and the Release
    is created by `release`, which needs it — so the order is a job's dependency, and no other job
    may publish notes of its own."""
    assert any(name.startswith("Build and push") for name in _names("publish")), (
        "nothing in the publish job pushes an image"
    )
    assert "publish" in _job("release")["needs"], "the Release can be created before the images"
    creating = [
        job
        for job, body in _workflow()["jobs"].items()
        for step in body.get("steps", [])
        if "gh release create" in str(step.get("run", ""))
        or step.get("name") == "Publish the release notes"
    ]
    assert creating == ["release"], (
        f"{creating} publish release notes; only the job behind the images may, or a failed build "
        "can leave a Release claiming images that were never pushed"
    )


def test_the_notes_are_the_tagger_s_own_words_and_not_a_generated_list() -> None:
    """The tag's own message is the description (`ADR-0030`). A commit list is evidence, not a
    description: it answers *what moved* and never *why it matters*, which is the whole reason this
    mechanism replaced one that wrote `Release v0.1.0` by itself."""
    publish = _step("release", "release_notes.py")

    assert "--notes-file" in str(publish["run"]), (
        "the Release body does not come from the rendered notes"
    )
    assert "--generate-notes" not in str(publish["run"]), (
        "GitHub's generated notes replaced the tagger's own description"
    )


def test_both_jobs_that_read_the_tag_check_out_the_history_the_notes_need() -> None:
    """The notes quote the commits since the previous release, and the forward-only check compares
    against every tag. `--depth 1` sees no tags at all, which makes every release look like the
    first one."""
    for job in ("described", "release"):
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


#: What each release job may do, and nothing more. `packages: write` is what GHCR accepts
#: `GITHUB_TOKEN` for in this repository's own namespace — which is why no personal access token is
#: configured anywhere; `contents: write` is for the GitHub Release; `id-token` and `attestations`
#: sign it. Every other job inherits the workflow's read-only default.
RELEASE_PERMISSIONS = {
    "publish": {"contents": "read", "packages": "write"},
    "inventory": {"contents": "read", "packages": "read"},
    "release": {"contents": "write", "id-token": "write", "attestations": "write"},
}


def test_each_release_job_asks_for_exactly_what_it_uses() -> None:
    """Named rather than inherited, and split by job: a permission granted to a job is granted to
    every step in it, so the token that can push images is not in the job that writes the Release,
    and neither is in the job that runs Syft."""
    jobs = _workflow()["jobs"]
    for job, expected in RELEASE_PERMISSIONS.items():
        granted = jobs[job].get("permissions")
        assert granted == expected, f"{job} asks for {granted}, not {expected}"
    assert _workflow()["permissions"] == {"contents": "read"}, (
        "the workflow's default permissions widened; only the release jobs may write"
    )
    others = sorted(j for j, body in jobs.items() if "permissions" in body)
    assert others == sorted(RELEASE_PERMISSIONS), f"{others} ask for permissions of their own"


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


def test_both_jobs_that_read_the_tag_restore_its_object_first() -> None:
    """**What `v0.1.1` was published with.** `actions/checkout` creates `refs/tags/<name>` pointing
    at the *commit* it checked out, so the annotated tag object — the thing that carries the notes —
    is not in the clone. `%(contents)` of a ref pointing at a commit is the commit's message, which
    has a subject and a body and passes every description rule, so nothing failed: the Release went
    out carrying `def905f`'s commit message and a title to match.

    Both jobs need it. The `described` job without it validates a commit message and reports
    success, which is worse than not checking at all — it is a gate that says the thing it did not
    look at is fine.
    """
    for job in ("described", "release"):
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
    publish = _step("release", "gh release create")
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


def test_the_plugin_is_packed_into_the_directory_the_release_attaches() -> None:
    """A rollout downloads the plugin from the Release by a name that carries the version
    (`tools/opencode_plugin_archive.py`)."""
    pack = str(_step("release", "Pack the OpenCode plugin")["run"])

    assert 'tools/opencode_plugin_archive.py "$VERSION" --out "$RUNNER_TEMP/assets"' in pack


def _published(tmp_path: pathlib.Path, files: list[str]) -> tuple[list[str], list[str]]:
    """Run the step that creates the Release, with `python` and `gh` replaced by stand-ins that
    record their arguments, over an asset directory holding ``files``. Returns what
    `release_notes.py` and `gh release create` were given."""
    assets = tmp_path / "assets"
    assets.mkdir()
    for name in files:
        (assets / name).write_text(name)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool in ("python", "gh"):
        stub = bin_ / tool
        stub.write_text(
            f'#!/bin/bash\nprintf "%s\\n" "$@" > "{tmp_path}/{tool}.$RANDOM$RANDOM"\n'
            'echo "subject"\n'
        )
        stub.chmod(0o755)
    step = _step("release", "Publish the release notes")
    subprocess.run(  # noqa: S603
        ["bash", "-e", "-o", "pipefail", "-c", str(step["run"])],  # noqa: S607
        check=True,
        capture_output=True,
        env={
            "PATH": f"{bin_}:/usr/bin:/bin",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            "OWNER": "x",
            "VERSION": "v1.2.3",
        },
    )
    calls = [path.read_text().splitlines() for path in tmp_path.glob("*.[0-9]*")]
    (notes,) = [c for c in calls if c[:1] == ["tools/release_notes.py"] and "--subject" not in c]
    (create,) = [c for c in calls if c[:2] == ["release", "create"]]
    return notes, create


def test_every_file_the_release_job_wrote_is_attached_and_named(tmp_path: pathlib.Path) -> None:
    """**Run, not read.** The plugin, each SBOM and every checksum are attached; each file but a
    checksum is named in the notes — a file the notes do not mention is one nobody finds. Taken
    from the directory rather than listed, so a document for an image added later is published
    without a second edit (`FRD-148` FR-6)."""
    files = [
        "opencode-aira-budget-v1.2.3.tar.gz",
        "opencode-aira-budget-v1.2.3.tar.gz.sha256",
        "aira-gateway-v1.2.3.cdx.json",
        "aira-gateway-v1.2.3.cdx.json.sha256",
        "aira-source-v1.2.3.cdx.json",
        "aira-source-v1.2.3.cdx.json.sha256",
    ]

    notes, create = _published(tmp_path, files)

    attached = sorted(pathlib.Path(arg).name for arg in create if "/assets/" in arg)
    assert attached == sorted(files)
    named = [notes[i + 1] for i, arg in enumerate(notes) if arg == "--asset"]
    assert sorted(named) == sorted(name for name in files if not name.endswith(".sha256"))


def test_the_plugin_is_packed_before_the_release_is_created() -> None:
    """`gh release create` with a file that does not exist fails after nothing — but a Release
    created without its asset, and the asset added later, is a window in which a rollout pinned to
    the version finds no file."""
    names = _names("release")

    assert names.index("Pack the OpenCode plugin") < names.index("Publish the release notes")


# == what a release contains (FRD-148) ============================================================


def _pushes() -> list[dict]:
    return [
        s for s in _job("publish")["steps"] if str(s.get("name", "")).startswith("Build and push")
    ]


def test_every_image_carries_its_sbom_and_provenance_in_the_registry() -> None:
    """An image pushed without them is an image whose contents only a fresh scan can tell — the
    property is per step, so a fourth image added without it is caught."""
    for step in _pushes():
        assert step["with"].get("sbom") is True, f"{step['name']} pushes no SBOM attestation"
        assert step["with"].get("provenance") == "mode=max", f"{step['name']}: no full provenance"


def test_every_pushed_image_is_described_by_the_digest_its_own_step_pushed() -> None:
    """**A tag can be moved after the scan; a digest cannot.** Each image the release pushes is
    named in the SBOM step with the digest the step that pushed *it* produced, handed on as one of
    the publish job's outputs — crossing two would publish the gateway's inventory under the
    console's name."""
    describe = _step("inventory", "Describe what the release contains")
    run, env = str(describe["run"]), describe["env"]
    pushed = {
        re.search(r"/(aira-[a-z]+):", str(step["with"]["tags"])).group(1): step["id"]
        for step in _pushes()
    }

    assert len(pushed) == len(_pushes()) >= 3
    for image, step_id in pushed.items():
        found = re.search(rf'--image "{image}=ghcr\.io/\$OWNER/{image}@\$(\w+)"', run)
        assert found, f"{image} is pushed and not described by digest"
        assert _handed_on(env[found.group(1)]) == f"${{{{ steps.{step_id}.outputs.digest }}}}", (
            f"{image} is described by another step's digest"
        )
    assert _handed_on(env["OWNER"]) == "${{ steps.release.outputs.owner }}"
    assert "--source ." in run, "the console's npm packages are described nowhere"
    assert '--out "$RUNNER_TEMP/assets"' in run, "the documents are not where the release looks"
    assert '--syft "$SYFT"' in run and env["SYFT"] == "${{ steps.syft.outputs.cmd }}"


def _handed_on(expression: str) -> str:
    """What a later job's `${{ needs.publish.outputs.<key> }}` is, in the publish job's terms."""
    key = re.fullmatch(r"\$\{\{ needs\.publish\.outputs\.(\w+) \}\}", expression)
    assert key, f"`{expression}` is not a value the publish job handed on"
    return str(_job("publish")["outputs"][key.group(1)])


def test_the_contents_are_described_after_the_last_push_and_before_the_release() -> None:
    """Before the last push there is no digest to scan; after the Release, a window in which the
    version is published without its inventory. The inventory waits for the push, the Release for
    the inventory, and the Release attaches the documents the inventory wrote."""
    names = _names("inventory")

    assert "publish" in _job("inventory")["needs"]
    assert "inventory" in _job("release")["needs"]
    assert names.index("Install Syft") < names.index("Describe what the release contains")
    handed = _step("inventory", "actions/upload-artifact")["with"]
    taken = _step("release", "actions/download-artifact")["with"]
    assert handed["name"] == taken["name"]
    assert handed["path"] == taken["path"] == "${{ runner.temp }}/assets"


def test_syft_is_pinned_to_an_exact_version() -> None:
    """A scanner that changes under the release changes what the inventory says, unannounced."""
    install = _step("inventory", "Install Syft")

    assert re.fullmatch(r"anchore/sbom-action/download-syft@[0-9a-f]{40}", install["uses"])
    assert re.fullmatch(r"v\d+\.\d+\.\d+", install["with"]["syft-version"])


# == what a release is built from, and who could have changed it (`ADR-0043`) ====================

#: A `uses:` line as written: the reference, and the release noted beside a commit.
_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?:\s+#\s*(?P<tag>\S+))?\s*$")
_PINNED = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")


def _uses_lines() -> list[tuple[str, str | None]]:
    return [
        (match["ref"], match["tag"])
        for line in WORKFLOW.read_text().splitlines()
        if (match := _USES.match(line))
    ]


def test_every_action_is_pinned_to_the_commit_of_a_release() -> None:
    """A tag is a pointer that whoever holds the action's repository can move, and the next run
    executes whatever it points at with this workflow's token — the push of the images included.
    A commit cannot be moved. The release it is noted beside, so raising it is a decision somebody
    reads, and one action is pinned once: two commits of it would be two reviews."""
    uses = _uses_lines()
    parsed = [
        str(step["uses"])
        for body in _workflow()["jobs"].values()
        for step in body.get("steps", [])
        if "uses" in step
    ]
    # Every action the jobs run, as written: a `uses:` this pattern missed would be unchecked.
    assert sorted(ref for ref, _ in uses) == sorted(parsed) and len(parsed) >= 10

    pins: dict[str, set[tuple[str, str | None]]] = {}
    for ref, tag in uses:
        assert _PINNED.match(ref), f"`{ref}` is not pinned to a commit"
        assert tag and re.fullmatch(r"v\d+\.\d+\.\d+", tag), f"`{ref}` names no release beside it"
        action, _, commit = ref.partition("@")
        pins.setdefault("/".join(action.split("/")[:2]), set()).add((commit, tag))
    twice = {action: sorted(found) for action, found in pins.items() if len(found) > 1}
    assert not twice, f"pinned to more than one commit: {twice}"


def test_no_checkout_leaves_the_token_in_the_clone() -> None:
    """`actions/checkout` writes the job's token into `.git/config` unless told not to, where every
    later step can read it — the scripts `npm ci` runs for each package among them. Nothing in this
    workflow pushes to the repository, so no checkout keeps it."""
    checkouts = [
        (job, step)
        for job, body in _workflow()["jobs"].items()
        for step in body.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]

    assert len(checkouts) >= len(_workflow()["jobs"]), "a job checks out without the action"
    for job, step in checkouts:
        assert step.get("with", {}).get("persist-credentials") is False, (
            f"{job} leaves the token in its clone, for every later step to read"
        )


#: Whose actions may run beside each permission that can write. GitHub's own are part of the
#: platform the token already trusts; Docker's push the images, which needs the registry token by
#: its nature. Nothing else from outside GitHub runs where anything can be written.
TRUSTED_BESIDE = {
    "packages": {"actions", "docker"},
    "contents": {"actions"},
    "id-token": {"actions"},
    "attestations": {"actions"},
}


def test_a_job_that_can_write_runs_only_code_it_has_to_trust() -> None:
    """**A step can read the token of every step in its job**, so a permission is granted to every
    action beside it. The job that pushes images runs Docker's actions and nothing else from outside
    GitHub; the job that writes the Release and can mint the workflow's identity runs GitHub's
    alone; Syft's installer and uv's run only where nothing can be written."""
    default = _workflow()["permissions"]
    for job, body in _workflow()["jobs"].items():
        granted = body.get("permissions", default)
        owners = {
            str(step["uses"]).split("/")[0] for step in body.get("steps", []) if "uses" in step
        }
        for scope in (scope for scope, level in granted.items() if level == "write"):
            allowed = TRUSTED_BESIDE.get(scope, {"actions"})
            assert owners <= allowed, (
                f"{job} holds {scope}: write beside {sorted(owners - allowed)}, which can read it"
            )


def test_no_release_job_restores_a_cache() -> None:
    """Caches are written by ordinary pushes — any branch, and every pull request that runs here —
    so a release that restores one is built partly from whatever the last of them left. A release
    builds from its own commit: no layer cache for an image, no package cache for a tool."""
    for job in sorted(RELEASE_JOBS):
        for step in _job(job)["steps"]:
            uses, given = str(step.get("uses", "")), step.get("with", {})
            name = step.get("name", uses)
            assert not uses.startswith("actions/cache"), f"{job}: {name} restores a cache"
            if uses.startswith("astral-sh/setup-uv@"):
                assert given.get("enable-cache") is False, (
                    f"{job}: {name} restores uv's cache, which is on by default on GitHub's runners"
                )
            if uses.startswith(("actions/setup-node@", "actions/setup-python@")):
                assert "cache" not in given, f"{job}: {name} restores a package cache"
            if uses.startswith("docker/build-push-action@"):
                assert not {"cache-from", "cache-to"} & set(given), f"{job}: {name} uses a cache"


def test_every_file_and_image_a_release_names_is_signed_before_it_exists() -> None:
    """A checksum beside a file proves the download is the file that was uploaded, not who made it.
    Each file a rollout downloads and each image a deployment pulls is **signed** — a statement of
    which run, commit and workflow produced it — before the Release that names them exists, so no
    version is ever downloadable unsigned. `gh attestation verify` checks one."""
    steps = _job("release")["steps"]
    names = _names("release")
    signing = [
        (index, step["with"])
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/attest-build-provenance@")
    ]

    assert signing, "the release signs nothing"
    assert max(index for index, _ in signing) < names.index("Publish the release notes"), (
        "the Release can exist before its signatures"
    )

    files = [(index, given) for index, given in signing if "subject-path" in given]
    assert files, "the files a rollout downloads are signed by nobody"
    paths = " ".join(str(given["subject-path"]) for _, given in files)
    for pattern in ("${{ runner.temp }}/assets/*.tar.gz", "${{ runner.temp }}/assets/*.cdx.json"):
        assert pattern in paths, f"`{pattern}` is attached and not signed"
    made = max(names.index("Pack the OpenCode plugin"), names.index(_names_of_download()))
    assert made < min(index for index, _ in files), "files are signed before they exist"

    signed = {
        (str(given.get("subject-name")), _handed_on(str(given["subject-digest"])))
        for _, given in signing
        if "subject-digest" in given
    }
    for step in _pushes():
        image = re.search(r"/(aira-[a-z]+):", str(step["with"]["tags"])).group(1)
        subject = (
            f"ghcr.io/${{{{ needs.publish.outputs.owner }}}}/{image}",
            f"${{{{ steps.{step['id']}.outputs.digest }}}}",
        )
        assert subject in signed, f"{image} is published without a signature over its digest"


def _names_of_download() -> str:
    return str(_step("release", "actions/download-artifact").get("name"))
