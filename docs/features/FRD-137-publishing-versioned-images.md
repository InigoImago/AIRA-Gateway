# FRD-137 — Publishing versioned images

> Phase: 7 · Status: **Built** · Owner: AIRA
> Related: `ADR-0029` (the decision), `ADR-0030` (the trigger and the notes),
> `FRD-127` (several gateway instances),
> `FRD-130` (the demo), `docs/DEPLOYMENT.md` §7

## 1. Summary

The three application images — `aira-gateway`, `aira-management`, `aira-frontend` — existed only on
the machine that built them. `docs/DEPLOYMENT.md` §7 said so plainly: *"Images are not published …
there is no registry push or tagging scheme beyond `AIRA_IMAGE_TAG`."* The consequence was not only
that a deployment had to build; it was that **nobody could say which AIRA was running.**

A release is now an **annotated Git tag** `vX.Y.Z`: pushing it publishes the three images to GitHub
Container Registry under that version, once every quality gate has passed on the tagged commit — and
**the tag's message is the release notes.**

> **Revised 2026-10-11 (`ADR-0030`).** The first shape was a dispatch form with a `bump` dropdown,
> and it worked: `v0.1.0` was published by run #86. What it left behind was a tag whose annotation
> said, in full, `Release v0.1.0`, and no GitHub Release at all. Not a bug — the mechanism had
> **nowhere to put a description**, so there was none. A dropdown cannot be asked what a release
> contains. The trigger is a tag now, because `git tag -a` opens an editor at the one moment
> somebody has the whole change in mind.

## 2. Goals & Non-Goals

**Goals**

- A version number with one home, and one piece of code that spells it.
- A release that a person starts deliberately, with one command: `git push origin vX.Y.Z`.
- **Every release carries a description, enforced rather than hoped for.**
- Nothing published that the test suites did not pass **on that commit, in that run**.
- A version that can never be reused or moved once it exists.
- No new credential: no personal access token, no stored registry secret.
- Runnable locally too — `make next-version` says what to tag, `make release-check` gives the same
  refusal CI would.

**Non-Goals**

- A version recorded in `pyproject.toml`, `package.json` or a chart. `ADR-0029` rejects it: the
  same fact in three files is this repository's oldest defect shape. A `CHANGELOG.md` is refused for
  the same reason (`ADR-0030`).
- Notes generated from the commit list. That answers *what moved* and never *why it matters*, and it
  would make every release look described while describing nothing. The commits are included as
  **evidence, after** the description.
- Backports. A tag older than the newest release is refused, because `latest` would move backwards.
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
- **FR-5** Pushing an annotated tag matching `v*.*.*` starts a release, and nothing else does. The
  publish job declares `needs: [python, frontend, stack, described]`, so a published version is one
  four jobs agreed about, on that commit, in that run.
- **FR-6** A release is refused unless the tagged commit is an **ancestor of `main`**. "Which
  branch" is not a property of a tag push; whether the commit is in the history `main` names is, and
  a version naming a commit nobody deploys is worse than no version.
- **FR-7** A version that is **already released** is refused. The tag cannot be the lock any more —
  it exists before the job starts, by definition — so the GitHub Release is: re-running a run whose
  images already landed would otherwise push over a published version.
- **FR-8** All three images are published, each at the release version, plus `sha-<commit>` and
  `latest`. Two of three would produce a deployment whose console is a version older than its
  gateway — the state a version number exists to make impossible.
- **FR-9** The **GitHub Release** is created after the images. Reversed, it would claim images that
  do not exist. A failed build therefore leaves a tag with no Release against it — visibly
  unfinished in the one list a person reads, which is how `ADR-0029`'s objection to a pre-test tag
  is answered: not by preventing it, but by what the failure leaves behind.
- **FR-10** Authentication is the workflow's built-in `GITHUB_TOKEN`, with `packages: write` scoped
  to the release job alone. No personal access token exists.

- **FR-11** A tag whose message is not a description is refused **before anything is published**.
  Four rules: it is annotated (a lightweight tag has no message — one missing `-a`); it has a
  subject; the subject does not merely restate the version; and it has a body after a blank line.
  The restatement check strips the words a restatement is made of rather than matching one string,
  because a guard against a single spelling is one somebody walks around by accident — including
  **the project's own name**, which was added after writing `v0.1.1`'s notes with this very tool
  produced the subject `AIRA Gateway v0.1.1` and it passed. That is the same non-answer as
  `Release v0.1.0` with the product's name in place of the word, and anybody opening a release of
  this repository already knows which project it is. Only the *words* are removed, so a subject
  that uses them to say something keeps a subject.
- **FR-12** That check is a **job with no `needs`**, so it reports in seconds. It is the only
  failure a tagger can still fix for free, and behind the gates it would arrive after forty minutes.
  The same refusal is available before pushing (`make release-check`), and `make release-tag`
  deletes the tag it just created when the annotation fails — a rejected attempt must not consume a
  version number.
- **FR-13** The **GitHub Release body** is the tag's message, then the commits since the previous
  release, then the three image references — in that order, because the order carries the claim:
  evidence **after** the description, never instead of it. A section with nothing in it is
  omitted, so a first release's notes are its description and not an empty Commits heading.
- **FR-14** A tag older than the newest release is refused, so `latest` never moves backwards.
## 5. Design & Architecture

Three pieces, and the split is the point.

**`tools/release_version.py`** holds the arithmetic and touches nothing else. `parse`, `latest`,
`bump`, `next_version` and `render` are pure functions over lists of strings; `tags_in` is the one
impure function and does nothing but run `git tag --list`. Testable without a repository in a
particular state, which is what lets FR-2 and FR-3 be asserted at all. Since `ADR-0030` it answers
*what should I tag* (`make next-version`) and backs the forward-only check, rather than minting the
version itself.

**`tools/release_notes.py`** holds the four description rules and the rendering. `read` is pure over
a message string; `annotation`, `previous` and `commits_since` are the impure three and do nothing
but ask Git. `Notes.markdown` puts the prose first and the generated sections after, and a section
with nothing in it is omitted rather than left empty.

**`.github/workflows/ci.yml`**, two jobs, because they answer different questions at very different
prices.

`described` — seconds, **no `needs`**, beside the gates:

| Step | Why |
| --- | --- |
| checkout, `fetch-depth: 0` | the notes quote the commits since the previous release, and `--depth 1` sees no tags at all |
| `release_notes.py --check` | FR-11, the same command `make release-check` runs, so the refusal met in CI is the one that could have been met before pushing |
| ancestor of `origin/main`? | FR-6 |
| newest release? | FR-14 — `latest` must not move backwards |

`publish` — a build, **behind all four**:

| Step | Why |
| --- | --- |
| checkout, `fetch-depth: 0` | the notes need the range |
| name the release | `github.ref_name`; the owner is lower-cased because GHCR rejects an upper-case path and this owner has capitals — a detail that fails only at the push, after a build |
| already released? | FR-7. The tag cannot be the lock any more, so the Release is |
| buildx, GHCR login | `GITHUB_TOKEN`, FR-10 |
| three `build-push-action` steps | one per Dockerfile, contexts as in `docker-compose.apps.yml`; GHA layer cache, scoped per image |
| publish the notes | FR-9 and FR-13 — last, so a failed build leaves a tag with no Release rather than a Release claiming images that were never pushed |

Why publishing lives in *this* workflow rather than one of its own: a separate workflow would have
to ask GitHub whether *some* run was green, and that is a weaker claim than it looks — it can be
about an older commit, a cancelled run, or a job re-run until it passed. `needs` is the strong form.

Why the description check is a **separate job** rather than the first step of `publish`: behind
`needs` it would be reported after forty minutes of gates. A bare annotation is cheap to detect and
cheap to fix, and only while nobody is waiting yet.

## 6. Open

- **A failed release spends a version number.** The tag exists before the gates run, so `v0.1.1`
  cannot be retried as `v0.1.1` unless the tag is deleted on the remote; the ordinary recovery is to
  tag `v0.1.2`. `ADR-0029` named this as the cost of a tag trigger and `ADR-0030` accepted it — a
  patch number is the cheapest thing this project has, and the failure is visible (a tag with no
  Release) rather than silent.
- **A description cannot be corrected after the fact.** Editing the GitHub Release makes it disagree
  with the tag it came from. A real wart; the alternative is notes in a file, which is a second
  source of truth and is what `ADR-0029` refused.
- **The published bytes are a rebuild of the tested commit, not the tested bytes.** With a warm
  cache they are the same layers in practice, and "in practice" is not a guarantee this project
  usually accepts. Handing the three images between jobs as artefacts (~1 GB) is the upgrade;
  stated in `ADR-0029` as a known limit rather than left to be discovered.
- **Nothing consumes the images yet.** Every `image:` in `docker-compose.apps.yml` is paired with a
  `build:` because `aira-gateway` was on no registry. A deployment overlay that names
  `ghcr.io/<owner>/aira-gateway:vX.Y.Z` and omits `build:` is what closes `DEPLOYMENT.md` §7 all the
  way, and is what `FRD-127` will want.
- **Packages start private** and are not linked to the repository automatically. Making them
  pullable is a one-time step per package in GitHub's own settings, outside this repository.
- **No backports.** A tag older than the newest release is refused (FR-14). Supporting one would
  need a second moving tag per series; named as absent rather than left to be discovered.
- Multi-architecture images, a generated `CHANGELOG.md`, and image signing or provenance attestation
  are all absent, deliberately, and each is a small addition when wanted. `v0.1.0`'s own
  description cannot be recovered — it was never written.

## 7. Testing & Acceptance Criteria

`tools/tests/test_the_release_version_comes_from_the_tags.py` — the arithmetic, hermetically:
numeric ordering through four lists whose lexicographic maximum is not their numeric maximum; the
reset on each part; `None` rather than `(0, 0, 0)` for an unreleased project; the strict tag
pattern; and one speller for a version. Mutations `PB1`–`PB6`.

`tools/tests/test_a_release_is_published_only_behind_the_gates.py` — the workflow's shape, parsed
rather than grepped, because a search for `needs:` passes on a `needs:` that names the wrong jobs:
the tag trigger and its pattern, all four gates on `publish`, both jobs gated on a tag ref, that
`described` has **no** `needs`, full history in both, the ancestor-of-`main` guard, forward-only,
the already-released refusal, the notes **after** the images and from the tool rather than
`--generate-notes`, all three images at the version, a commit tag per image, exactly two permissions
on the job with a read-only default on the workflow, no long-lived token anywhere, and that a
release run is never cancelled mid-publish. Mutations `PB7`–`PB22`.

`tools/tests/test_a_release_says_what_it_is.py` — the four description rules, each against the
spellings of the same non-answer: a lightweight tag (refused by naming the missing `-a`), a missing
subject, ten ways of restating the version including the `Release v0.1.0` the old mechanism wrote
by itself, and a headline with no body. Plus that a subject *mentioning* its version is accepted
(the rule is *only* restates), that Markdown reaches GitHub unreflowed, and that the prose comes
before the evidence. One test runs the rules against the repository's own `v0.1.0` annotation, so
the guard is checked against the real thing it was written for. Mutations `PB23`–`PB28`.

*Two guards were found by their own mutations.* `PB18` renames `aira-frontend`'s versioned tag and
survived the first draft: the test asked whether the image *name* appeared, and the same image's
`:latest` and `:sha-…` lines kept it appearing — green about a release in which two images carried
the version and one did not. And `PB28` made `commits_since` return `[""]` for a first release and
survived, because the test handed `markdown` an empty list directly and so never reached the
function that decides what that list is: **a test that asserts the renderer's behaviour says nothing
about its caller.**

**Acceptance**

- *Given* tags `v0.9.0` and `v0.10.0`, *when* `make next-version` is run, *then* it prints
  `v0.10.1` — not `v0.9.1`.
- *Given* a tag annotated `Release v0.1.1`, *when* it is pushed, *then* the release is refused
  within a minute, naming the subject and saying what to write instead — and no image is built.
- *Given* `make release-tag VERSION=v0.1.2` and an annotation that fails the rules, *when* the check
  runs, *then* the tag is deleted again, so the version number is not spent.
- *Given* an annotated tag with a subject and a body, *when* it is pushed and every gate passes,
  *then* three images exist at that version and a GitHub Release carries the tag's own words above
  the commits since the previous release.
- *Given* an ordinary push to `main`, *when* the workflow runs, *then* both release jobs are skipped
  and nothing is published.
- *Given* a release whose console build fails, *when* the run ends, *then* there is no GitHub
  Release for that tag.
