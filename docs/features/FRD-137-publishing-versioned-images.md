# FRD-137 — Publishing versioned images

> Phase: 7 · Status: **Built** · Owner: AIRA
> Related: `ADR-0029` (the decision), `FRD-127` (several gateway instances),
> `FRD-130` (the demo), `docs/DEPLOYMENT.md` §7

## 1. Summary

The three application images — `aira-gateway`, `aira-management`, `aira-frontend` — existed only on
the machine that built them. `docs/DEPLOYMENT.md` §7 said so plainly: *"Images are not published …
there is no registry push or tagging scheme beyond `AIRA_IMAGE_TAG`."* The consequence was not only
that a deployment had to build; it was that **nobody could say which AIRA was running.**

This adds an **optional, manually triggered** release: it reads the last version from the Git tags,
increments the part you ask for, and publishes the three images to GitHub Container Registry under
that version — but only once every quality gate has passed on the commit being released.

## 2. Goals & Non-Goals

**Goals**

- A version number with one home, and one piece of code that spells it.
- A release that a person starts deliberately, from the GitHub UI, choosing patch, minor or major.
- Nothing published that the test suites did not pass **on that commit, in that run**.
- A version that can never be reused or moved once it exists.
- No new credential: no personal access token, no stored registry secret.
- Runnable locally too — `make next-version` answers the same question the workflow asks.

**Non-Goals**

- A version recorded in `pyproject.toml`, `package.json` or a chart. `ADR-0029` rejects it: the
  same fact in three files is this repository's oldest defect shape.
- Automatic releases on merge. The owner asked for a manual step, and an automatic one would
  publish before anybody had decided the commit was worth a version.
- Multi-architecture images. `linux/arm64` needs QEMU and roughly triples the console build; worth
  doing when somebody needs it, not before.
- A changelog, a GitHub Release body, or signing. §6 keeps them open.
- Consuming the published images. A deployment Compose overlay that pulls instead of builds is the
  payoff and the next step — see §6.

## 3. User Stories

- As the **owner**, I want to publish a new version when I decide a commit is worth one, so that a
  release is a decision rather than a side effect of merging.
- As whoever **operates** an installation, I want to pin `aira-gateway:v1.4.2` so that a restart
  cannot quietly bring up different code.
- As whoever performs a **rolling update** (`FRD-127`), I want an immutable reference per build so
  that "one instance at a time" names a specific artefact.
- As a **reader** trying AIRA out, I want `:latest` to exist so that I need not read a tag list
  first.

## 4. Functional Requirements

- **FR-1** The next version is derived from the Git tags matching `vX.Y.Z` and nothing else. A
  pre-release (`v1.2.3-rc1`), a two-part tag (`v1.2`) and an unprefixed one (`1.2.3`) are not
  releases of this project and do not take part — a looser pattern would make the next version
  depend on somebody's scratch tag.
- **FR-2** The newest release is chosen by **numeric** comparison of the triple, never by string
  order. `v0.10.0` is newer than `v0.9.0`.
- **FR-3** A bump resets everything less significant: `v1.4.7` by minor is `v1.5.0`, never
  `v1.5.7`, which would claim a patch history the new minor does not have.
- **FR-4** With no release tag visible, the next version is `v0.1.0` — returned as it is, not
  bumped from `v0.0.0`. A project's first published images are an early version of something, not a
  patch to nothing. The requested part is still validated on this path.
- **FR-5** The release is an input to the existing CI workflow (`release`, default `false`; `bump`,
  default `patch`), and its job declares `needs: [python, frontend, stack]`. A dispatch with the
  default runs exactly the workflow that ran before this feature existed.
- **FR-6** A release is refused from any ref but `main`. A version tag on a feature branch names a
  commit that is not in the history anybody deploys, and the tag outlives the branch.
- **FR-7** A version that is already tagged is refused. The tag is the lock: two dispatches racing,
  or a rerun of a release that got as far as pushing images, would otherwise move a published
  version in place.
- **FR-8** All three images are published, each at the release version, plus `sha-<commit>` and
  `latest`. Two of three would produce a deployment whose console is a version older than its
  gateway — the state a version number exists to make impossible.
- **FR-9** The Git tag is created and pushed **after** the images. Reversed, a failed build leaves a
  version that names no image, and the next release counts from it.
- **FR-10** Authentication is the workflow's built-in `GITHUB_TOKEN`, with `packages: write` scoped
  to the release job alone. No personal access token exists.

## 5. Design & Architecture

Two pieces, and the split is the point.

**`tools/release_version.py`** holds the arithmetic and touches nothing else. `parse`, `latest`,
`bump`, `next_version` and `render` are pure functions over lists of strings; `tags_in` is the one
impure function and does nothing but run `git tag --list`. The arithmetic is therefore testable
without a repository in a particular state, which is what lets FR-2 and FR-3 be asserted at all.

**`.github/workflows/ci.yml`**, job `publish`. The steps, in order, and the order *is* the design:

| Step | Why it is where it is |
| --- | --- |
| checkout, `fetch-depth: 0` | the tags **are** the version; a shallow clone sees none, and "no tag" reads as "never released" — which would publish `v0.1.0` over whatever is already out |
| refuse a non-`main` ref | FR-6, checked here rather than trusted to whoever opens the dispatch form |
| `uv sync`, then `release_version.py` | the same command `make next-version` runs; a second implementation in YAML is a second place for the ordering bug |
| refuse an existing tag | FR-7 |
| buildx, GHCR login | `GITHUB_TOKEN`, FR-10 |
| three `build-push-action` steps | one per Dockerfile, contexts as in `docker-compose.apps.yml`; GHA layer cache, scoped per image |
| tag and push | FR-9 — last, so a failure leaves nothing behind |
| summary | the version, the previous one, the three image references, and the note that packages start private |

Why an input to this workflow rather than a workflow of its own: a separate workflow would have to
ask GitHub whether *some* run was green, and that is a weaker claim than it looks — it can be about
an older commit, a cancelled run, or a job re-run until it passed. `needs` is the strong form.

**The owner's name is lower-cased** in the version step. GHCR rejects an upper-case path and this
repository's owner has capitals in it — a detail that fails only at the push, after a build.

## 6. Open

- **The published bytes are a rebuild of the tested commit, not the tested bytes.** With a warm
  cache they are the same layers in practice, and "in practice" is not a guarantee this project
  usually accepts. Handing the three images between jobs as artefacts (~1 GB) is the upgrade;
  stated in `ADR-0029` as a known limit rather than left to be discovered.
- **Nothing consumes the images yet.** Every `image:` in `docker-compose.apps.yml` is paired with a
  `build:` because `aira-gateway` was on no registry. A deployment overlay that names
  `ghcr.io/<owner>/aira-gateway:vX.Y.Z` and omits `build:` is what closes `DEPLOYMENT.md` §7 all the
  way, and is what `FRD-127` will want.
- **Packages start private** and are not linked to the repository automatically. Making them
  pullable is a one-time step per package in GitHub's own settings, outside this repository. The job
  summary says so rather than leaving a reader to wonder why a pull is denied.
- Multi-architecture, a changelog or GitHub Release body, and image signing or provenance
  attestation are all absent, deliberately, and each is a small addition when wanted.

## 7. Testing & Acceptance Criteria

`tools/tests/test_the_release_version_comes_from_the_tags.py` — the arithmetic, hermetically:
numeric ordering through four lists whose lexicographic maximum is not their numeric maximum; the
reset on each part; `None` rather than `(0, 0, 0)` for an unreleased project; the strict tag
pattern; and one speller for a version. Mutations `PB1`–`PB6`.

`tools/tests/test_a_release_is_published_only_behind_the_gates.py` — the workflow's shape, parsed
rather than grepped, because a search for `needs:` passes on a `needs:` that names the wrong jobs:
all three gates present, the dispatch gate and its `false` default, the `main` guard, full history,
the existing-tag refusal, the tag **after** the images, all three images at the version, a commit
tag on each, exactly two permissions on the job and a read-only default on the workflow, and no
long-lived token anywhere. Mutations `PB7`–`PB16`.

*One of those guards was found by its own mutation.* `PB12` renames `aira-frontend`'s versioned tag
and survived the first draft: the test asked whether the image *name* appeared, and the same image's
`:latest` and `:sha-…` lines kept it appearing. It now requires the **version** tag per image — the
property FR-8 is actually about.

**Acceptance**

- *Given* a repository with tags `v0.9.0` and `v0.10.0`, *when* `make next-version` is run, *then*
  it prints `v0.10.1` — not `v0.9.1`.
- *Given* no release tag at all, *when* a release is dispatched with `bump: major`, *then* the
  version is `v0.1.0`.
- *Given* a dispatch with `release: false`, *when* the workflow runs, *then* nothing is published
  and the run is identical to an ordinary push's.
- *Given* a dispatch with `release: true` on a branch that is not `main`, *when* the publish job
  starts, *then* it fails before logging in to any registry.
- *Given* a release in which the console's build fails, *when* the run ends, *then* no Git tag was
  created and the same version is still available to the next attempt.
