# FRD-137 — Publishing versioned images

> Phase: 7 · Status: **Built** · Owner: AIRA
> Related: `ADR-0029` (the decision), `ADR-0030` (the trigger and the notes),
> `FRD-138` (running what this publishes), `FRD-127` (several gateway instances),
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
- Consuming the published images. That is the payoff and it is a feature of its own:
  [`FRD-138`](FRD-138-running-a-published-release.md).

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
  Five rules: the ref is an **annotated tag object** (a lightweight one has no message of its own —
  one missing `-a`); it has a subject; the subject does not merely restate the version; it has a
  body after a blank line; and no placeholder from `docs/RELEASE-TEMPLATE.txt` survives.
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
- **FR-15** The annotation is written **from a template**. `make release-tag` opens an editor on
  `docs/RELEASE-TEMPLATE.txt` with the version substituted, rather than on an empty buffer: a blank
  annotation is a prompt to improvise, and what gets improvised is a sentence restating the version
  — which is how `Release v0.1.0` happened. The template asks the reader's two questions in order
  (*what changed*, *do I have to do anything*), its guidance lines start with `#` so
  `--cleanup=strip` never publishes them, and a release that still carries its placeholders is
  refused: an unfilled template is the right shape with nothing in it. The placeholder list is read
  **from the template**, because a list beside it is a second copy — add a section, forget the list,
  and the new prompt is the one nobody checks.
- **FR-16** Whatever reads the tag asks Git for the **object type first**, and a ref that is not a
  tag object has no annotation. `%(contents)` of a ref pointing at a commit is *the commit's
  message*, which has a subject, has a body, and says something — so it passes every other rule.
  **This is the defect that published `v0.1.1` with `def905f`'s commit message**; see §6.
- **FR-17** The release job **restores the annotated tag object** before anything reads it, and
  asserts that it is one. `actions/checkout` creates `refs/tags/<name>` pointing at the commit it
  checked out, so the tag object is not in the clone at all. Both release jobs do it, because a
  description check that validates a commit message and reports success is worse than no check.
- **FR-18** The Release's **title and body come from the same place** — `release_notes.py`, which
  means the title goes through the five rules too. It was a second `git tag --format` in the
  workflow, unchecked, so when the tag arrived flattened the title became the commit's subject and
  nothing objected.
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
- **`v0.1.1`'s published Release still carries the wrong text**, and this is that wart being paid.
  The run published `def905f`'s commit message as the notes and `fix(tests): wait for the rows a
  request makes, not for one` as the title, because the ref the job was handed was a lightweight
  tag at the commit and `%(contents)` of such a ref is the commit's message (FR-16, FR-17). The
  **tag on the remote was right all along**, so the correct body is recoverable —
  `make release-notes VERSION=v0.1.1` prints it — but re-publishing it is a `gh release edit`
  somebody runs once, and the already-released guard means the job will not do it. Recorded rather
  than quietly fixed, because the release that was wrong is the evidence for why both rules exist.
- **The published bytes are a rebuild of the tested commit, not the tested bytes.** With a warm
  cache they are the same layers in practice, and "in practice" is not a guarantee this project
  usually accepts. Handing the three images between jobs as artefacts (~1 GB) is the upgrade;
  stated in `ADR-0029` as a known limit rather than left to be discovered.
- ~~**Nothing consumes the images yet.**~~ **Closed** by
  [`FRD-138`](FRD-138-running-a-published-release.md) / `ADR-0031` on 2026-10-11: two overlays —
  `docker-compose.release.yml` takes the three images from the registry and removes their `build:`,
  `docker-compose.external.yml` runs them against infrastructure the operator provides. `RELEASE=`
  on any `up*` target is the interface, and the whole integration suite has been run against
  `v0.1.0` pulled from GHCR. `DEPLOYMENT.md` §7's row is closed both ways.
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
release run is never cancelled mid-publish, the tag object is restored in both release jobs, and
the title comes from the tool. Mutations `PB7`–`PB22` and `RT5`–`RT9`.

`tools/tests/test_a_release_says_what_it_is.py` — the five description rules, each against the
spellings of the same non-answer: a lightweight tag (refused by naming the missing `-a`), a missing
subject, ten ways of restating the version including the `Release v0.1.0` the old mechanism wrote
by itself, and a headline with no body. Plus that a subject *mentioning* its version is accepted
(the rule is *only* restates), that Markdown reaches GitHub unreflowed, and that the prose comes
before the evidence. One test runs the rules against the repository's own `v0.1.0` annotation, so
the guard is checked against the real thing it was written for — and **skips where that tag is
absent**, because the clone CI makes holds only the ref that triggered it. Every other test that
needs a tag creates it, annotated or lightweight, with the tagger identity passed in: a fresh
checkout has no `user.email`, so `git tag -a` there exits 128. Mutations `PB23`–`PB29`,
plus `RT1`–`RT4` for the rules added on 2026-10-11.

**And the five that were added after a release went out wrong** (mutations `RT1`–`RT9`): a real
lightweight tag, created and removed in the test, must report **no** annotation — the assertion
that would have caught the whole defect, and the one nobody wrote because `%(contents)` was
believed rather than asked; the template must exist and must ask for something, since a template
with no placeholders makes rule 5 inert without making it absent; an unfilled template is refused
and a filled one accepted; and the title must come from the same function as the body, so that it
goes through the rules too. On the workflow's side: both release jobs restore the tag object *and
assert it is one*, and the Release's title no longer comes from a second `git tag --format`.

*The last of those five was machine-dependent on its first attempt, and it stopped `v0.1.2`.* It
asked about `v0.1.1`'s annotation — a tag this clone has and CI's does not — so the Python gate
failed on a tag's absence and no image was built. Reproduced in a `git clone --no-tags` holding one
tag and no identity, which is CI's situation; the test makes the two tags it reasons about now.

*Three guards were found by their own mutations.* `RT8` and `RT9` both survived their first draft,
for the third recorded instance of one shape: the test asked whether `docs/RELEASE-TEMPLATE.txt`
and `--cleanup=strip` appeared in the `release-tag` target, and **the comment block explaining why
they are there kept them appearing** after the commands were removed. `PB14` was the same thing in
a workflow's `run:` block and `IM11` in a Compose file one plane along. The helper strips `@#`
lines now: a comment is prose about a rule, and only the recipe is the rule.

*Two more were found the same way earlier.* `PB18` renames `aira-frontend`'s versioned tag and
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
- *Given* `make release-tag VERSION=v0.1.2` and an editor that saves the template unchanged, *when*
  the check runs, *then* the placeholder it still carries is named, the tag is deleted, and the
  version number is not spent. (Run with `EDITOR=true`, which is exactly that editor.)
- *Given* a **lightweight** tag `vX.Y.Z` on a commit with a perfectly good commit message, *when*
  the annotation is read, *then* it is empty and the release is refused naming the missing `-a` —
  not accepted with the commit's message as its notes.
- *Given* a published release, *when* its page is read, *then* the title is the tag's own subject
  line and not a commit's.
- *Given* `make release-tag VERSION=v0.1.2` and an annotation that fails the rules, *when* the check
  runs, *then* the tag is deleted again, so the version number is not spent.
- *Given* an annotated tag with a subject and a body, *when* it is pushed and every gate passes,
  *then* three images exist at that version and a GitHub Release carries the tag's own words above
  the commits since the previous release.
- *Given* an ordinary push to `main`, *when* the workflow runs, *then* both release jobs are skipped
  and nothing is published.
- *Given* a release whose console build fails, *when* the run ends, *then* there is no GitHub
  Release for that tag.
