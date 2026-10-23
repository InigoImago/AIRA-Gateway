# ADR-0043 — A release is built from pinned code, by jobs that hold only their own token, and signed

- **Status:** Accepted
- **Date:** 2026-10-22
- **Deciders:** AIRA
- **Amends:** `ADR-0029` and `ADR-0030` in how the release jobs are cut — the trigger, the gates, the
  description rules and the order (images, then notes) stand unchanged. `FRD-148`'s non-goal
  *signing* is answered here.

## Context

The security review of 2026-10-22 (finding N-3, with M9 and D-1…D-4 beside it) read the release
chain as an attacker would, asking who other than this repository could put code into a release:

- **Every action was named by a tag** (`actions/checkout@v5`, `anchore/sbom-action/download-syft@v0.24.3`).
  A tag is a pointer the action's owner — or whoever compromises them — can move to other code, and
  the next run executes it with this workflow's token. That is how the 2025 compromise of
  `tj-actions/changed-files` reached the repositories that named it by tag.
- **One job held `contents: write` and `packages: write`** for its whole length, beside a
  third-party action and beside `uv sync`, which installs every package of the workspace. A step can
  read the token of every step in its job: the permission was granted to all of them.
- **The release job restored caches** — uv's package cache and BuildKit's layer cache — which
  ordinary pushes write. Whoever could get a poisoned entry into one, from any branch, would have it
  built into a release.
- **Every checkout left the job's token in `.git/config`**, readable by the install scripts `npm ci`
  runs for each package, although nothing in the workflow pushes.
- **Nothing a release published was signed.** The `.sha256` beside each file proves the download is
  the file that was uploaded, not who uploaded it; BuildKit's provenance travels with each image
  unsigned.
- The images were built with **`uv:latest`**, and the console on a nginx line that had stopped being
  rebuilt (two critical OpenSSL findings).

None of this was a defect anybody had observed. Each was a place where the release's integrity
depended on a party other than this repository behaving.

## Options considered

- **Restrict the steps, keep one job.** GitHub grants permissions per job, not per step, so there is
  no way to hold a token for three steps of twelve. Rejected: it cannot be expressed.
- **A reusable workflow as a "trusted builder"** (SLSA build level 3): the build and the signature in
  a workflow the calling one cannot influence. The strongest form, and a larger change — a second
  workflow file, inputs to validate, and verification against `--signer-workflow` of the reusable
  one. Not ruled out; not needed to close what was found.
- **Sign with `cosign` directly**, keyless through GitHub's OIDC token. Equivalent signatures, but a
  third-party installer in the one job allowed to mint the identity — the opposite of the point.
- **Split the release into jobs by what each may write, pin everything, and sign with GitHub's own
  attestations.** Chosen.

## Decision

1. **Every action is pinned to the full commit its release tag named**, with that tag in a comment
   beside it, and one action has one pin across the workflow. Raising one is a deliberate edit of
   both. The installer the images are built with is pinned the same way — `uv` by version and
   index digest — and every image a Dockerfile names carries a version.
2. **No checkout keeps the token** (`persist-credentials: false` everywhere); the repository is
   public, so the release jobs' fetches need none.
3. **Four release jobs, cut by what each may write:**

   | job | may write | runs beside it |
   | --- | --- | --- |
   | `described` | nothing | uv, the description rules — unchanged |
   | `publish` | `packages` | GitHub's checkout and Docker's buildx, login and build actions |
   | `inventory` | nothing (`packages: read`) | Syft's installer, which reads the images back by digest |
   | `release` | `contents`, `id-token`, `attestations` | GitHub's own actions only; the release tools run on plain Python |

   The release tools are standard-library Python, so the job that writes the Release installs
   nothing from PyPI. The plugin archive is packed there, from the tagged source, so the file
   signed is made where only GitHub's actions run.
4. **No release job restores a cache.** uv's cache is switched off where it is set up, and the
   image builds take no layer cache in either direction. uv's cache was the real route: every push
   to `main` writes it, and a run on a tag may read the default branch's caches. BuildKit's was
   written only by release runs, which cannot read another tag's, so the images already built cold.
5. **Everything a release names is signed before the Release exists**:
   `actions/attest-build-provenance` signs the plugin archive and every SBOM, and each image by the
   digest just pushed, with a Sigstore certificate issued to this workflow run. The statements live
   in GitHub's attestation store; `gh attestation verify <file | oci://image> --repo
   InigoImago/AIRA-Gateway` checks one, and `--signer-workflow` and `--source-ref` narrow it to this
   workflow and one tag.

`tools/tests/test_a_release_is_published_only_behind_the_gates.py` holds every one of these, and
`tools/tests/test_every_image_a_build_starts_from_is_pinned.py` the Dockerfiles.

## Consequences

- **Positive:** a moved tag, a compromised third-party action or a poisoned cache can no longer
  reach a release silently; the token that can push images never sits beside Syft or uv; a
  workstation rollout or a deployment can check where a file or image came from, not only that it
  arrived whole.
- **Negative / trade-offs:** pins go stale. Nothing raises them automatically — a dependency bot
  that understands commit pins is the follow-up if the owner wants one. Each action is pinned to the
  newest release of the major the workflow already used; the three Docker actions of the image job
  (buildx and login 3, build-push 6) declare a Node 20 runtime, which GitHub is retiring, and moving
  them to their next majors is a separate, deliberate step. The release jobs install their tools
  afresh every time.
- **Not covered:** the bytes published are still a rebuild of the tested commit, not the tested
  bytes (`FRD-137` §6); BuildKit's own SBOM and provenance stay unsigned beside the signed
  statement; a signature attests *who built it*, not that what was built is free of defects.
- **Follow-ups:** a trusted reusable workflow if a higher SLSA level is wanted; automated raises of
  the pins; the console's nginx line moved when nginx publishes its next stable line.
