# ADR-0029 — A release is a Git tag, and the tag is the only place a version lives

- **Status:** Accepted
- **Date:** 2026-10-07
- **Deciders:** AIRA

## Context

`docs/DEPLOYMENT.md` §7 has carried the same gap since the deployment guide was written:
*"Images are not published — `make up-full` builds them locally; there is no registry push or
tagging scheme beyond `AIRA_IMAGE_TAG`."* Every `image:` in `deploy/compose/docker-compose.apps.yml`
is therefore accompanied by a `build:`, and the file says why: *"`aira-gateway` is on no registry,
and a service with `image:` and no `build:` is one Compose tries to pull."* So the only way to run
AIRA anywhere is to build it there — which also means nobody can say *which* AIRA is running.

Two things are being decided at once, and they are easy to confuse:

1. **Where a version number lives**, so that "the last version" is a question with an answer.
2. **What must be true before a version exists**, since a published image tag is the one artefact
   this project cannot take back: somebody may already be running it.

Constraints that shaped the answer:

- `FRD-127` wants a rolling update that takes one gateway instance at a time. That needs an
  immutable reference per build, not only a moving one.
- The owner asked for a release that is **optional and manually triggered**, takes the last version
  and increments it.
- `.github/workflows/ci.yml` states its own rule in its header: *every step calls the same target a
  developer runs locally, so CI and a local run cannot drift.*

## Options considered

**Where the version lives**

- **A version in the tree** — `pyproject.toml`, `package.json`, a future Helm chart. Readable
  without Git, and conventional. But it is the same fact in three files, which is this repository's
  oldest defect shape; it needs a commit to bump, so the commit that *is* the release does not carry
  its own number; and nothing stops the three from disagreeing.
- **A Git tag** — already the thing that ties a version to a commit, cannot disagree with itself,
  and is what somebody reads to answer *"what is deployed?"*. Needs full history to be visible,
  which a shallow CI checkout does not have.
- **The registry as the source** — ask GHCR for the highest published tag. No local answer at all,
  depends on a network call and on the registry's retention, and a deleted package resets the
  series.

**What gates a release**

- **A workflow of its own**, triggered manually, that asks the API whether CI was green for this
  commit. Flexible, but "some run was green" is a weaker claim than it looks: it can be about an
  older commit, a cancelled run, or a job re-run until it passed.
- **An input to the existing workflow**, with the publish job depending on the three check jobs.
  Publishing then cannot happen except behind gates that ran on *this* commit in *this* run. Costs
  a full CI run per release.

**What builds the published image**

- **Rebuild in the publish job** with a layer cache. Simple; the images are what a clean build of
  the tested commit produces, which is not quite the same as the bytes that were tested.
- **Hand the tested images over as artefacts.** Guarantees the published digests are the tested
  ones, at the price of moving roughly a gigabyte between jobs.

## Decision

**A release is a Git tag `vX.Y.Z` and the three images published under it. The tag is the only place
a version is recorded; nothing in the tree carries one.**

- The next version is computed from the tags by `tools/release_version.py`, which is **numeric**:
  tags are strings, `v0.10.0` sorts below `v0.9.0` in every string comparison there is, and a
  release cut from the lexicographic maximum goes backwards the first time a minor number reaches
  ten. The first release is `v0.1.0`, returned as it is rather than bumped from a zero.
- Publishing is **an input to `ci.yml`**, not a workflow of its own: the `publish` job declares
  `needs: [python, frontend, stack]` and `if: ${{ inputs.release }}`. A dispatch with the default
  `release: false` runs exactly the workflow that ran before, and nothing is published that the
  unit tests, the browser tests and the live stack did not pass on that commit.
- Each image carries **three** tags: the version, which never moves and is what a deployment pins;
  `sha-<commit>`, which answers *"is this instance running what I think it is?"* without trusting
  that a version tag was never moved (`FRD-127`); and `latest`, for a reader trying it out.
- **The Git tag is pushed after the images.** A tag first and a failed build leaves a version that
  names no image, and the next release counts from it — so the series would skip a number to hide a
  failure. In this order a failed publish leaves nothing behind and can be run again.
- **Authentication is the built-in `GITHUB_TOKEN`** with `packages: write` on that job alone. GHCR
  accepts it for the repository's own namespace, so no personal access token is stored anywhere; a
  long-lived token with package scope is a credential this project should not be holding, and
  `ADR-0007`'s rule about secrets applies to CI as much as to the services.
- The publish job **rebuilds** with buildx and a layer cache, and that is a trade-off taken
  knowingly (below).

## Consequences

- Positive: *"which AIRA is running?"* has an answer, and it is the same string in the repository
  and in the registry — `tools/release_version.py` is the only speller of a version.
- Positive: a deployment Compose file can name `ghcr.io/<owner>/aira-gateway:vX.Y.Z` and drop
  `build:`, which closes the `DEPLOYMENT.md` §7 gap and is the actual payoff.
- Positive: `FRD-127`'s rolling update has an immutable reference to advance to.
- Positive: nothing new to configure. No secret, no PAT, no external registry account.
- Negative: a release costs a full CI run — roughly forty minutes — because the gates are the
  gate. Deliberate: the alternative is publishing on a claim about a different run.
- Negative: **the published bytes are a rebuild of the tested commit, not the tested bytes.** With a
  warm cache they are the same layers in practice, but "in practice" is not a guarantee, and this
  project does not usually accept one. Recorded as a known limit rather than hidden; handing the
  images over as artefacts is the upgrade, and `FRD-137` §6 keeps it open.
- Negative: GHCR packages start **private** and are not linked to the repository automatically.
  Making them pullable is a one-time manual step per package, outside this repository.
- Follow-ups: multi-architecture images (`linux/arm64` needs QEMU and roughly triples the console
  build) are not built; a release has no changelog or GitHub Release body; and nothing yet consumes
  the published images — a deployment Compose overlay that pulls instead of builds is the next step,
  and it is what `FRD-127` will want anyway.
