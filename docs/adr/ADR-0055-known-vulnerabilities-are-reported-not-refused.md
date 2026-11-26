# ADR-0055 — Known vulnerabilities are reported by the release and alarmed by the night, not refused

- **Status:** Accepted
- **Date:** 2026-11-26
- **Deciders:** Vadim Scheibe (owner), with Claude

## Context

Every release attaches one CycloneDX document per image and one of the source (`FRD-148`). Reading
them for known vulnerabilities was left to the reader, and nobody did. The owner wants to know,
at the release and every night after, that the newest release carries no serious known
vulnerability (`FRD-154`).

Three facts shape where a check can sit:

- **The documents exist only after the images are pushed.** The inventory scans the pushed digests,
  by design: a document of a local rebuild describes something nobody deploys. By then the images
  are in the registry under the version and `latest`.
- **Removing a finding takes an update**: a changed lock file or base image, a commit, the gates,
  a new tag. That holds whatever a check at the release answers.
- **A vulnerability is almost always published after the code that has it.** A check at the
  release answers for that day. The question *"are we affected?"* is asked on every day after it.

## Options considered

**Where the check sits, and what a finding does**

- **A: refuse the Release after the inventory.** Simple to add. But it refuses after the push:
  - the images are already out under the version and `latest`, unsigned, and no Release names them;
  - the only recovery is a new commit and a new tag, which is what an update needs anyway.
- **B: refuse before anything is pushed.** A job builds the three images beside the gates and
  reads them. It catches a finding before it reaches the registry. But:
  - it doubles every release's image builds;
  - it reads a rebuild, not the bytes published;
  - every acceptance becomes a commit and a re-tag behind forty minutes of gates;
  - a scanner or database outage stops a release whose code is fine.
- **C: report at the release, alarm at night** (chosen).
  - The release reads its own documents and attaches the report, signed, with its counts in the
    notes; nothing found holds the Release back.
  - A scheduled workflow reads the newest release every night and fails on what is to act on.
- **D: only the night.** It loses the record of what was known when a release shipped, which is
  what an audit asks for.

**What fails the night**

- **Every critical or high finding.** The red is often unactionable, because Debian lists advisories
  with no fix for months. Each would need an acceptance written for it, or the mail comes every
  morning and stops being read.
- **Critical or high with a fixed version, or exploited** (chosen). It fails exactly when an update
  exists or somebody is using the vulnerability. The rest is listed *to watch*, and moves to *to act
  on* by itself on the night its fix appears.
- **Critical with a fix only.** Quieter, and blind to most of what is fixable.

**Which scanner**

- **Grype** (chosen). It is the same project as Syft, which writes the documents, so it reads the
  package identities as written. Its database is built daily from NVD, GitHub's advisories, the
  distributions' trackers, CISA's exploited catalogue and EPSS. It reads a document without the
  image.
- **Trivy.** It reads CycloneDX too, and would do. It is a second project's view of the package
  identities Syft wrote, and its database is fetched from a container registry rather than a
  plain download.
- **OSV-Scanner.** It asks a web service about every package instead of reading a database fetched
  once, so the answer for one document is not tied to one dated set of data.
- **Dependabot.** It reads the repository's manifests, not the images, so never a Debian or Alpine
  package.
- **Dependency-Track.** It is a server to run, which this repository cannot add. It remains what an
  installation's security team can feed the same documents to.

## Decision

**Option C, with Grype**:
- The release reads its documents between the inventory and the Release, attaches the report and
  signs it, and is never refused by what is found, nor by a check that could not be made; its notes
  then say *not checked*.
- A workflow of its own reads the newest Release every night and fails when something is critical
  or high with a fixed version, or listed as exploited, and is not accepted.
- **An acceptance is a reviewed decision**: an entry in `.github/accepted-vulnerabilities.toml`
  naming the vulnerability and the package, why, and until when, at most a year ahead. It is read
  from the tagged commit at the release and from `main` at night.
- Grype and its installer are pinned (`ADR-0043`), identically in both places; the database is
  fetched when the check starts and never restored from a cache.

## Consequences

- Positive:
  - Every release states what was known about it on its day, in a signed file.
  - The newest release is checked every day, and the mail comes only when there is something to do.
  - An accepted risk has an author, a reason and an end.
  - No release waits on a third party's database service.
- Negative / trade-offs:
  - A release can ship with something to act on; its notes say so, and the night repeats it.
  - Only the newest release is checked at night: an installation on an older one checks its version
    by hand (`FRD-154` §5.5).
  - In a public repository the reports and run summaries are public, as the documents already are.
  - GitHub disables a schedule after 60 days without activity in the repository.
- Follow-ups:
  - A refusal before the push (Option B) if a release ever must not carry a known fixable
    vulnerability.
  - GitHub's code-scanning alerts, once somebody reads them (`FRD-154` §13).
