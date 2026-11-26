# FRD-154 — A release is checked for known vulnerabilities, when it is made and every night after

> Phase: 7 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner — *"können wir es auch so einrichten, dass beim release und jede nacht unsere
> letzte release mit sboms auf schwachstellen geprüft wird, so dass wir wissen dass wir keine große
> schwachstellen in libraries haben"*. Asked, the owner chose:
> - a finding is **reported, not refused**, when a release is made;
> - the night **fails on a critical or high vulnerability with a fix, or on one known to be
>   exploited**.
>
> Related: `FRD-148` (the documents this reads), `ADR-0043` (pinned actions, signed files),
> `ADR-0055` (the decision), `docs/DEPLOYMENT.md` §1b.

## 1. Summary

Every release attaches a CycloneDX document for each image and one for the source (`FRD-148`), and
until now left reading them to whoever downloaded them. `FRD-148` named a vulnerability report a
non-goal. The release now reads them itself, with Grype, twice over.

**When it is made**, a job between the inventory and the Release reads the documents against that
day's vulnerability database:
- the report, `aira-vulnerabilities-<version>.md`, is attached and signed with the other files;
- the release notes carry its counts and everything to act on.

**Every night after**, a workflow of its own reads the newest Release's documents again, against
that night's data. A vulnerability is almost always published after the code that has it, so this
is the half that answers *"are we affected?"* on a day after the release. It **fails** when
something is to act on, and GitHub's mail about a failed scheduled run is the alarm.

## 2. Goals & Non-Goals

**Goals**

- Know every morning whether the newest release carries a known vulnerability that an update would
  remove, or one somebody is already exploiting.
- Every release states what was known about it on the day it was made, in a signed file beside the
  documents.
- Every finding is in one group:
  - *to act on*;
  - *to watch*;
  - *accepted*;
  - *the rest*.
- An acceptance is a reviewed decision: a vulnerability, a package, a reason and a date at most a
  year ahead.
- A check that was not made never reads as a clean one.
- One pinned Grype for the release and the night, and a database fetched when the check starts,
  never restored from a cache.

**Non-Goals**

- **Refusing a release for a finding** (`ADR-0055`): by the time the documents exist, the images
  are pushed.
- Checking releases other than the newest every night. An installation running an older one checks
  its own version (§5.5).
- Updating anything. The check names the package and the version that fixes it; the update is a
  commit and a release.
- The code AIRA writes (static analysis), licences and secrets: other questions, other tools.
- A dashboard. The documents are standard and Dependency-Track reads them; GitHub's code-scanning
  alerts were considered and not built (§13).
- VEX documents. The acceptances are a file of this repository's, read by this tool only.

## 3. User Stories

- As the **owner**, I want a mail on the morning the newest release has a vulnerability we can
  update away, so that the fix is released before somebody else finds the gap.
- As **IT security**, I want each release to state what was known when it shipped, and every
  accepted vulnerability with its reason and date, so that an audit finds the decision and not only
  its absence.
- As an **operator**, I want the notes of the version I am about to deploy to say whether it has
  anything to act on.

## 4. Functional Requirements

- **FR-1** After the inventory and before the Release, a job reads every document the inventory
  wrote with Grype, against the database fetched when it starts.
  - The report `aira-vulnerabilities-<version>.md` and its `.sha256` are attached to the Release and
    signed with the other files.
  - The notes carry a section with the counts per document and every finding to act on.
- **FR-2** Neither what the check finds nor a check that could not be made holds the Release back.
  When the check could not be made, the notes and the report say *not checked*, and why.
- **FR-3** Every night (04:17 UTC) and on demand, a workflow reads the newest Release: the one
  GitHub calls the latest.
  - It downloads that Release's documents and checksums, verifies each document against its
    checksum, and reads it with the same Grype.
  - It **fails** when something is to act on, or when the check could not be made.
  - Its report is in the run's summary, and kept for 30 days as an artifact.
- **FR-4** Every finding is in one group:
  - **to act on**: critical or high with a fixed version, or listed in CISA's catalogue of
    exploited vulnerabilities whatever its severity and fix;
  - **to watch**: critical or high with no fixed version;
  - **accepted**;
  - **the rest**: medium and below, counted per document.
- **FR-5** An acceptance in `.github/accepted-vulnerabilities.toml` has these keys:
  - `vulnerability` (its id or an alias Grype related it to);
  - `package`;
  - optionally `documents`;
  - `reason`;
  - `until`, a date at most 365 days after the day the check runs.

  It holds through `until`. From the day after, the finding counts again, and the report names the
  acceptance as one that ran out. An acceptance that matches nothing is named, so it can go. The
  check is not made if an entry has a key it does not know, a reason that says nothing, or an
  `until` that is not a date.
- **FR-6** The check is **not made** (status 2, the report saying so) when:
  - Grype fails or cannot be run;
  - its database cannot be fetched, or Grype calls it invalid;
  - the source document is missing, or every image's is;
  - a document is of another version;
  - a checksum is missing or does not match;
  - Grype's report cannot be read.
- **FR-7** Grype is pinned to an exact version. The action that installs it is pinned to a
  release's commit (`ADR-0043`), the same in both workflows.
  - Its database is never restored from a cache.
  - It is fetched once per check, and every document is read against it with automatic updates off.
- **FR-8** Neither workflow can write anything with the check's token. The nightly workflow is held
  to the rules every workflow here is: actions pinned to commits, no token left in a clone, nothing
  from a third party beside a permission that writes.
- **FR-9** A finding in the source document says which part of AIRA it is about:
  - *development tools only*: a Python package no image ships;
  - *the console's bundle*: an npm package;
  - *the CI workflow*: a GitHub Action.

## 5. Design & Architecture

### 5.1 Why Grype

Syft writes the documents and Grype reads them; they are one project's. Grype reads the package
identities Syft wrote as Syft meant them: package URLs, CPEs, and the distribution an OS package
belongs to.

Its database is built every day from:
- the National Vulnerability Database;
- GitHub's advisories;
- the distributions' own trackers, Debian's and Alpine's among them;
- CISA's catalogue of exploited vulnerabilities, and EPSS.

The alternatives are in `ADR-0055`:
- **Trivy** reads CycloneDX as well.
- **OSV-Scanner** asks a service per package.
- **Dependabot** reads the repository's manifests and so never sees the Debian and Alpine packages
  in the images.

### 5.2 The groups, and why the night fails on one of them

The night fails only on what somebody can act on: **an update to a version that exists, or a
vulnerability already being used**. A critical or high one without a fix is listed as *to watch*
and does not fail the run. Otherwise every Debian advisory without a fix would turn the night red
until somebody wrote an acceptance for it, and a mail that comes every morning is a mail nobody
reads. On the night a fix is published, it moves to *to act on* by itself.

An exploited vulnerability is acted on whatever its severity. CISA lists what is being used in
attacks, which is a stronger signal than a score.

### 5.3 Acceptances

An acceptance is reviewed as code, because it is a file in the repository: the commit says who, the
entry says why and until when. The rules are there so that the file stays a list of decisions:
- **A year at most**, so that a risk accepted is a risk looked at again.
- **The package as well as the id**, because one CVE is often in several packages, and accepting it
  in `libssl3` says nothing about `openssl`.
- **Unknown keys are refused**, because a misspelt `untill` would otherwise accept for ever.

The release reads the acceptances of the commit it was made from, so its report can be reproduced
from that commit. The night reads `main`'s, so an acceptance takes effect without a release.

### 5.4 A check not made

An empty list of findings is the one answer that must come from a real scan.

A database that could not be fetched is refused, whatever `db update` answered, because Grype calls
it invalid. So are:
- a document whose checksum does not match;
- a Grype report without its `matches`;
- a scanner that is not there.

Each is status 2 and a report headed **Not checked**, never one that says *0 to act on*.

### 5.5 What the night does not see

- **Releases other than the newest.** An installation on an older version checks it by hand:

  ```bash
  v=vX.Y.Z
  gh release download "$v" --repo InigoImago/AIRA-Gateway -p 'aira-*.cdx.json*' -D "sboms-$v"
  python tools/release_vulnerabilities.py "$v" --sboms "sboms-$v" --out "report-$v"
  ```

- **What the documents do not list.** The console's npm packages are compiled into its bundle; the
  source document lists them from the lock file (`FRD-148` §5.2). A binary copied into an image
  without a package manager is listed only where Syft recognises it.
- **A schedule GitHub switched off.** In a public repository, GitHub disables a scheduled workflow
  after 60 days without activity, and says so by mail; one click turns it on again.
- **Results stay out of sight only as far as the repository does.** The reports and run summaries
  of a public repository are as public as the documents they read. Anybody could run the same scan,
  and the report adds no knowledge to it.

### 5.6 The tool

`tools/release_vulnerabilities.py` uses Python's standard library only, so it runs where nothing
from PyPI is installed, as the other release tools do. It takes the Grype command as an argument;
the tests replace it with a stand-in that answers in Grype's JSON.

Exit statuses: 0 nothing to act on, 1 something to act on, 2 not checked. `--report-only` makes
both 1 and 2 exit 0, which is how the release job runs it.

## 6. Data Model

None. The acceptances are a file in the repository.

## 7. API / Interface Contract

- The report: Markdown, `aira-vulnerabilities-<version>.md`, beside its `.sha256` on the Release.
- The notes' section: `## Known vulnerabilities`, after the images and before the attached files.
- `.github/accepted-vulnerabilities.toml`: `[[accepted]]` tables, keys as in FR-5.

## 8. Security & Privacy

- Both jobs hold a token that can read the repository's contents and nothing else. Grype's
  installer, a third party's action, runs nowhere a permission writes.
- The documents are verified against their checksums before they are read.
- Nothing about people is read or written: the privacy notice is unaffected.

## 9. Observability

- The run's summary carries the whole report.
- A failed scheduled run is mailed by GitHub to whoever last changed the schedule.
- A finding to act on is an annotation on the run: a warning at the release, an error at night.

## 10. Testing & Acceptance Criteria

- `tools/tests/test_a_release_is_checked_for_known_vulnerabilities.py` covers the tool:
  - the groups, exploited findings included;
  - the acceptances, by date, package, document and alias;
  - every refusal of FR-5 and FR-6;
  - the report and the notes;
  - the command's exit statuses, through a stand-in Grype run as a program.
- `tools/tests/test_a_release_is_published_only_behind_the_gates.py` covers the release job:
  - after the inventory and before the Release;
  - the report attached, signed and summarised in the notes;
  - `--report-only`;
  - the same pinned Grype in both workflows, with no cached database.

  The rules of `ADR-0043` now hold for every workflow.
- `tools/tests/test_the_newest_release_is_checked_every_night.py` covers the night:
  - the schedule;
  - read-only;
  - the newest Release's own documents, through a stand-in `gh` (a tag spelt like anything but a
    version is refused before it reaches `$GITHUB_ENV`);
  - failing without `--report-only`;
  - the report kept when it fails.
- `make mutants`: `KV1`–`KV46`, each a property above broken on purpose and caught.
- **Not verifiable before it runs on GitHub**:
  - the schedule and the mail;
  - `gh release` and the installer action on a runner.

  The first run is the first night after it reaches `main`, or a run started by hand from the
  *Actions* tab.

## 11. Dependencies & Risks

- **Anchore's database service.** When it cannot be reached, the release says *not checked* and
  the night fails; a rerun of the failed job later answers.
- **GitHub's schedule** can start a run late under load. A night is a window, not a minute.
- **What Grype's JSON says.** The tool reads the fields of the pinned version, and raising Grype is
  a change of the pin, checked like one.

## 12. Rollout / Demo

Nothing to deploy: both halves are CI. A new release carries its report. The night begins once
the workflow is on `main`.

## 13. As built

As specified, with Grype 0.121.3 through `anchore/scan-action/download-grype` v7.4.2. Neither is
the newest release: a release that is days old has had no time to be found out, and both are more
than two weeks old.

**Considered and not built: GitHub's code-scanning alerts.** Grype writes SARIF, and an upload
would list every finding under the repository's *Security* tab and close it when a later release no
longer has it. Three things argue against it for now:
- an accepted finding would have to leave the upload, and its alert would then close as *fixed*,
  which it is not;
- it needs a job that holds a permission to write security events;
- none of it can be tried before it runs on GitHub.

The run's summary and the mail answer the owner's question; the alerts are an addition worth
making once somebody reads them.

**Verified by hand** with Grype 0.121.3 and its own binary, checked against the release's
checksums:
- the tool's behaviour without a database: `grype db update` exits 1 when the database cannot be
  fetched, `db status` answers `"valid": false`, and the check reports *not checked*;
- actionlint 1.7.12 on both workflows: no finding beyond one style note `main` already has.
