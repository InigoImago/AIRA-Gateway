# FRD-148 — A release says what it contains

> Phase: 7 · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner — *"können wir auch bei releases auch sbom erstellen?"* — and, asked which
> format, CycloneDX, from the next release on.
> Related: `FRD-137` (publishing the images), `FRD-144` §6 (the first file a Release attaches),
> `ADR-0030` (the tag is the release), `docs/DEPLOYMENT.md` §1b

## 1. Summary

A release publishes three images and an archive, and until now said nothing about what is inside
them. Whoever has to answer *"are we affected by CVE-…?"* about a running installation had to pull
the image and scan it themselves, and for the console could not: its npm dependencies are compiled
into one JavaScript bundle, and a scanner reading the image finds nginx and Alpine and nothing of
Angular.

Every release now carries a **software bill of materials** in two places:

- **beside each image in the registry** — BuildKit's SBOM (SPDX) and provenance attestations,
  attached to the image index, so they describe the exact digest a deployment pulls;
- **as files on the GitHub Release** — one CycloneDX 1.6 document per image, plus one of the
  source's locked dependencies, each with its `.sha256`, so a security team or a procurement
  process reads them without registry access.

## 2. Goals & Non-Goals

**Goals**

- An SBOM for every published image, describing the **pushed digest**, not a tag that can move and
  not a local rebuild.
- The console's own dependencies described somewhere: the image cannot show them, so the source's
  lock files must.
- CycloneDX, the format the owner chose; JSON, specification 1.6, which the BSI's TR-03183-2 accepts
  and which Dependency-Track, Trivy and Grype read.
- A release that cannot produce a plausible SBOM does not publish its notes: an empty document is a
  release that claims to contain nothing.
- A new image added to the release job cannot ship without one — checked by a test, not a reviewer.

**Non-Goals**

- **Signing** (cosign, Sigstore). The attestations are BuildKit's, unsigned beyond the registry's
  own integrity. Signing is a feature of its own, with its own key or identity question.
- **A vulnerability report.** An SBOM is the inventory a scanner reads; scanning it, and deciding
  what a finding means for an installation, is the reader's.
- **Reproducible SBOMs.** Syft writes a timestamp and a serial number into each document, so two
  runs differ; the checksum states what was published, not what a rerun would produce.
- **Republishing past releases.** From the next release on; `v0.1.7` and older have none.
- **Separating Python development tools out of the source SBOM.** `uv.lock` locks the workspace's
  development group with everything else, and Syft reads it whole (§5.2).

## 3. User Stories

- As **IT security**, I want the list of packages in the gateway an installation runs, so that I can
  answer whether a published vulnerability concerns us without pulling and scanning the image.
- As **procurement or compliance**, I want an SBOM in a standard format attached to the version we
  run, so that a supplier assessment has the evidence it asks for.
- As an **operator** pulling from GHCR, I want the SBOM to describe exactly the digest I pulled.

## 4. Functional Requirements

- **FR-1** Each `Build and push` step of the release job sets `sbom: true` and
  `provenance: mode=max`, so the image index carries both attestations.
- **FR-2** After the last image is pushed and before the Release is created, the job writes one
  CycloneDX JSON document per image, scanned **by digest** from the registry
  (`ghcr.io/<owner>/<image>@sha256:…`, the build step's `digest` output) and named
  `<image>-<version>.cdx.json`.
- **FR-3** It writes one more, `aira-source-<version>.cdx.json`, from the tagged commit's checkout:
  the lock files of the Python workspace, the console and the GitHub Actions the workflow uses. The
  browser suite and the client harnesses (`e2e/`, `tools/clients/`) are test tooling and excluded.
- **FR-4** A document is refused, and the release stops, unless it is CycloneDX, specification 1.6,
  names the component it describes, and lists at least one package.
- **FR-5** Each document has a `.sha256` beside it, in `sha256sum` format.
- **FR-6** Every file in the release's asset directory is attached to the GitHub Release and named in
  its notes, so an SBOM for an image added later is attached without a second edit.
- **FR-7** Syft is pinned to an exact version, as is the action that installs it.
- **FR-8** The tool refuses an image reference that is not pinned by digest and a version that is
  not a release version, before running anything.

## 5. Design & Architecture

### 5.1 Two places, because they serve two readers

The registry attestation is what tooling that already speaks OCI uses
(`docker buildx imagetools inspect <image> --format '{{ json .SBOM }}'`); it travels with the image
and cannot drift from it. The Release files are what a person downloads. BuildKit writes SPDX only,
so the CycloneDX files are Syft's — two scanners, two formats, and a reader who compares them will
find small differences in what each counts as a package. That is stated rather than hidden.

### 5.2 Why the source SBOM exists

Measured with Syft 1.54.1 against this repository's images:

| Document | Packages | What it sees |
| --- | --- | --- |
| `aira-gateway` | 159 | 97 Debian, 61 Python, the interpreter |
| `aira-management` | 154 | 97 Debian, 56 Python, the interpreter |
| `aira-frontend` | 68 | Alpine only — nginx and its libraries |
| `aira-source` | 117 | 98 Python (`uv.lock`), 12 npm (console runtime), 7 GitHub Actions |

The console image holds a compiled bundle; no scanner can recover the packages compiled into it.
The console's `package-lock.json` can, and Syft reads only its runtime dependencies (development
dependencies are excluded by default). `uv.lock` is read whole, development group included — so
`pytest` and `ruff` appear in the source document, while the images' documents show what runs.

### 5.3 The tool

`tools/release_sbom.py` runs Syft once per target and checks each document before writing its
checksum. It takes the Syft command as an argument, which is what the workflow passes from
`anchore/sbom-action/download-syft` and what the tests replace.

## 6. Open

- Signing the images and attestations.
- Whether the Release files should also be attached as attestations of their own (CycloneDX in the
  registry), so one place answers both readers.
- `v0.1.7` and earlier have no SBOM.

## 7. Testing & Acceptance Criteria

- `tools/tests/test_a_release_says_what_it_contains.py` — the tool: names, checksum, the four
  refusals of FR-4, the refusals of FR-8, the Syft command it runs, with a stand-in Syft that
  writes a fixture document.
- `tools/tests/test_a_release_is_published_only_behind_the_gates.py` — the workflow: FR-1 for every
  build step, FR-2 for every pushed image by its own step's digest, the order (after the last push,
  before the Release), FR-6 and FR-7.
- Verified by hand with Syft 1.54.1: the three local images and a source export (§5.2), and a
  BuildKit push with both attestations to a local registry, scanned back by index digest.
- Not verifiable before a release: GHCR's handling of the attestation manifests, and the job itself.
  The next tag is the first run.

## 13. As built

As specified. Syft 1.54.1 through `anchore/sbom-action/download-syft@v0.24.3`.
