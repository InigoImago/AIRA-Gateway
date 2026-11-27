# FRD-157 — Every image says which release it is

> Phase: 7 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: a ticket from Stage T (vux8092) on v0.1.17 — *"Prozesse melden Version 0.1.0
> statt der Release-Version"*: every process of the running `aira-gateway:v0.1.17` and
> `aira-management:v0.1.17` said `"version": "0.1.0"` in its `process_started` line.
>
> Related: `ADR-0029`, `ADR-0030` (the version lives in the tag), `FRD-137` (publishing the images),
> `FRD-138` (running a published release), `FRD-117` (`/version-info`), `FRD-631` (the first line),
> `FRD-148` (what a release contains), `ADR-0018` (what `/version-info` discloses).

## 1. Summary

The version of a release lives in its Git tag and nowhere else (`ADR-0030`). The images never
received it: every process stated `0.1.0` — the number written into the three packages' `__init__`
and `pyproject.toml` before the first release — in its first line, as `service.version` on every
span, metric and exported line, and the gateway's `/version-info` answered `null` for every build
fact. After an update, nothing in a log store or a dashboard told from when the new version ran, and
in an incident nothing showed which version wrote a line (`docs/GAP-ANALYSIS.md` §4 had listed it:
*"A published image does not say which build it is"*).

Now the release job hands each of the three images the tag's version, the commit and the build
time as build arguments; each image keeps them in its environment and as its labels; every process
of both planes reads them in one place (`aira_common.build`) and states them in its first line, on
every signal and on `/healthz`; and the release job reads each pushed image back by digest and
refuses to publish a Release whose images say otherwise.

## 2. Goals & Non-Goals

**Goals**
- A published image's processes state the release they are, in `process_started`, as
  `service.version`, on `/healthz` and `/version-info` — the tag's version, without its `v`.
- `/healthz` names the version, the commit and the build time; no `null` on a published image.
- `docker inspect` names the version without starting the image, the console's included.
- The release fails when an image says another version than its tag.

**Non-Goals**
- The packages' own metadata. `pyproject.toml` still says `0.1.0`, which an image's SBOM lists for
  the three workspace packages: setting it per release would change the lock file per release, and
  `uv sync --frozen` holds a workspace member to the version the lock records. The runtime's
  version is no longer read from it, nor from any number in the code.
- A build number and a branch for a release. `/version-info` keeps both fields for the predecessor's
  shape and reads them when somebody sets them; a release writes neither.
- Local builds. A build from a checkout says `0.0.0+local`, and `null` for commit and time.

## 3. User Stories
- As an **operator** after an update, I want each process's first line to name the release, so
  that the log store shows from which line on the new version ran.
- As **IT Security** in an incident, I want every exported line and span to carry the version and
  the first line the commit, so that I can say which code wrote what I am reading.
- As the **release**, I want to refuse images that would state another version, so that a version
  that cannot be seen is not published again.

## 4. Functional Requirements
- **FR-1 The release hands its identity to the build.** The publish job builds each image with
  `AIRA_BUILD_VERSION` (the tag without its `v`), `AIRA_GIT_COMMIT` (the commit) and
  `AIRA_BUILD_TIME` (one RFC 3339 time for all three images).
- **FR-2 The image keeps it.** Each image's last stage takes the three as build arguments — the
  version defaulting to `0.0.0+local` — writes them into its environment, and into the labels
  `org.opencontainers.image.version`, `.revision` and `.created`. Declared after every instruction
  that builds anything, so a new commit rebuilds no layer.
- **FR-3 One reading.** `aira_common.build.this_build()` reads the version, commit, time, build
  number and branch from the process's environment: a leading `v` before a number is the tag's and
  dropped, an empty fact is `None`, a missing version is `0.0.0+local`. Not a setting of either
  plane: nothing about it is configured, a settings file would make a claim the image did not, and
  no compose file passes any of the five (a test refuses one that would).
- **FR-4 Every statement of the version is the image's.** The first line, `process_started`, states
  `version` and `commit` (`null` for a build that was not told one); both planes' OpenTelemetry resource
  carries the version as `service.version`; the gateway's API documentation names it. The three
  `__version__ = "0.1.0"` constants are gone.
- **FR-5 `/healthz` says which build answers**, on both planes:
  `{"status": "ok", "version", "commit", "built"}`, still without I/O.
- **FR-6 `/version-info`**, on the gateway and the KIRA surface, reads the same identity and adds
  `version` beside the predecessor's fields.
- **FR-7 The release reads it back.** A job of its own, `identified`, needs `publish`; the Release
  needs it. It pulls each image **by the digest just pushed** (`tools/release_identity.py`), holds
  its three labels to the tag, the commit and the time, and for the gateway and Management asks the
  image itself (`python -m aira_common.build`, what every process reads) for the same three. Any
  difference — or a check that could not be made — fails the job, and no Release is created.

## 5. Design & Architecture

### 5.1 Why the environment, and why not settings

The gateway had four settings for this (`AIRA_BUILD_NUMBER`, `AIRA_BUILD_TIME`, `AIRA_GIT_COMMIT`,
`AIRA_GIT_BRANCH`), read like every other setting — from the environment **and from a `.env`
file** — and nothing set them. A build fact read from a settings file is a claim the image did not
make, and a release has to be able to say: this is what the image says. So the identity is no
setting any more: `aira_common.build` reads `os.environ` only, the names are documented in
`docs/CONFIGURATION.md` as what an image is, and the settings classes ignore an old `.env` line
(`extra="ignore"`), so nothing refuses to start.

`AIRA_VERSION` was not reused: it is the version a deployment *runs*
(`docker-compose.release.yml`, `FRD-138`), a choice of the operator's, not a fact of the image.

### 5.2 Read back after the push

The check runs after the push, against the digests a deployment pulls, rather than against a build
made for checking. A failure leaves images in the registry under the version and no Release —
visibly unfinished, as any failure after the push does (`FRD-137`); `release` creates nothing
until `identified` passed. The job holds `packages: read` and runs only Docker's login and GitHub's
actions beside it.

### 5.3 The console's image

nginx states nothing of its own, so its labels are what say which release it is; its environment
carries the three names as well, for whatever runs inside it.

## 6. Data Model
None. No column records a version; the audit row's trace id leads to the spans, which carry
`service.version`.

## 7. API / Interface Contract
- `GET /healthz` on both planes: `{"status": "ok", "version": "0.1.18", "commit": "<sha>", "built":
  "2026-11-27T12:00:00Z"}`; `"0.0.0+local"`, `null`, `null` for a local build. A liveness probe
  reads the status only, so nothing that probes it changes.
- `GET /version-info` and `GET /kira/api/external/version-info`: `version` added.
- `process_started`: `version` as the image says, `commit` added (`null` when unknown).
- `docs/API.md` §2.2, §3.1 and the KIRA table say so.

## 8. Security & Privacy
`/healthz` now discloses the release and the commit to anybody who can reach the port — what
`/version-info` already disclosed, unauthenticated, as `ADR-0018` decided and
`test_every_route_is_guarded.py` lists. No person, no configuration and no secret is in either. No
processing of personal data changes, so the privacy notice does not.

## 9. Observability
`service.version` on every span, metric and exported line is the release; `process_started` names
version and commit, so a log store's query for the first line after an update names the release
that started.

## 10. Testing & Acceptance Criteria
- `libs/tests/test_a_build_says_which_release_it_is.py` — the reading: tag spelling, empty facts,
  `0.0.0+local`, the statement the release job reads.
- `libs/tests/test_a_process_says_when_it_starts_and_stops.py` — the first line's version and
  commit, and none for a local build.
- `gateway/tests/test_app.py`, `test_diagnostics.py`, `test_kira_surface.py`;
  `management/backend/tests/test_health.py` — `service.version`, `/healthz`, `/version-info`.
- `tools/tests/test_every_image_says_which_release_it_is.py` — every Dockerfile takes, keeps and
  labels the identity after everything it builds; the release hands it to every image; `identified`
  reads it back before the Release; no compose file sets it.
- `tools/tests/test_a_release_refuses_images_that_say_otherwise.py` — the check refuses another
  version in a label or in the processes' statement, a missing commit or time, a reference by tag,
  and a check that could not be made.
- Mutations `VR-1` – `VR-23`.

**Acceptance (the ticket's):**
- *`process_started` meldet `"version": "0.1.18"`* — FR-1, FR-2, FR-4.
- *`service.version` in Metriken und Logs entspricht dem Tag* — FR-4.
- *`/healthz` nennt Version, Commit und Build-Zeit, keine `null`-Werte bei veröffentlichten
  Images* — FR-5.
- *Der Release schlägt fehl, wenn Image-Version und Tag nicht übereinstimmen* — FR-7.

## 11. Dependencies & Risks
- The first real run of `identified` is the next tag. What it does was measured locally (§12) with
  the real images and a local registry; GitHub's runner, GHCR and the private packages' sign-in are
  the ones `inventory` already uses.
- Images published before this release keep saying `0.1.0`.

## 12. As built (2026-11-27)

Built as specified. Measured on the three images built from this tree on Debian 13 with the
release's arguments (`0.1.18`, the commit, a fixed time):

- the labels of all three name the version, the commit and the time, and the environment of the
  gateway's and Management's carries them, as `python -m aira_common.build` states;
- the gateway, started without a network, wrote `"version": "0.1.18"` and the commit in its
  `process_started` line and answered all three on `/healthz`;
- `tools/release_identity.py` passed the three pushed to a local registry, read by digest; it
  refused the gateway built without the arguments — six differences, three labels and three
  statements — and refused v0.1.17's published gateway, which has no identity to state, as a check
  that could not be made.
