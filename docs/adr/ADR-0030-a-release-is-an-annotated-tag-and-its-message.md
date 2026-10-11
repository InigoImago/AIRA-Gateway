# ADR-0030 — A release is an annotated tag, and its message is the release notes

- **Status:** Accepted
- **Date:** 2026-10-11
- **Deciders:** AIRA
- **Supersedes:** `ADR-0029` **in part** — its trigger. The rest of `ADR-0029` stands unchanged: the
  version lives in the Git tags and nowhere else, the gates gate, the notes come after the images.

## Context

`ADR-0029` chose a manual **dispatch form** as the trigger: tick a box, pick `patch`/`minor`/`major`,
and the job computed the next version and created the tag itself. It worked. `v0.1.0` was published
by run #86 on 2026-10-07 and all three images landed.

Then the owner looked at what it had left behind:

- the tag's annotation, in full: **`Release v0.1.0`**
- GitHub Releases: **none**

Neither is a bug in the implementation; both are the implementation working as designed. The
mechanism had **nowhere to put a description**, so there was none. A dropdown cannot be asked what a
release contains, and the only free-text field in the whole path was a `-m` the job wrote itself.

That reframes the problem. This is not a discipline failure to be solved by resolving to write
better notes next time — it is a **missing field**, and the question is where the field belongs.

`ADR-0029` also listed, as a conscious drawback of a tag trigger, that *"the tag exists before the
tests run"*: a failed build would leave a version naming no image, which the next release then
counts past, so the series skips a number to hide a failure. That objection is still true and has to
be answered, not forgotten.

## Options considered

- **Keep the dispatch, add a notes field to the form.** Minimal change. But a multi-line Markdown
  description typed into a browser form, with no editor, no diff to look at and no way to revise
  before committing to it, is the worst place in the entire toolchain to write prose — and it is
  gone the moment the form is submitted, recorded nowhere a reader can find except the Release.
- **Keep the dispatch, write notes into a `CHANGELOG.md`.** The description then lives in the
  repository, reviewable, which is genuinely better. But it is a second place a version is recorded
  — exactly what `ADR-0029` refused — and it needs its own convention for which section belongs to
  which release, plus a check that the two agree.
- **Generate the notes from the commits.** Free, always present, and answers the wrong question. A
  commit list says *what moved*; release notes say *why it matters and whether it affects you*. It
  would make every release look described while describing nothing.
- **Trigger on an annotated tag, and use its message.** `git tag -a v0.1.1` opens an editor at the
  one moment somebody has the whole change in mind, in the terminal they are already in. The
  description then has a home that is a Git object, immutable and fetchable by anybody. Costs the
  pre-test tag the previous ADR objected to.

## Decision

**A release is an annotated tag `vX.Y.Z` pushed to the repository, and the tag's message is the
release notes.** The dispatch form and its `release`/`bump` inputs are gone; there is one way to cut
a release, because two ways are two ways for a version to be wrong.

- The trigger is `on.push.tags: ["v*.*.*"]`. The version is `github.ref_name`, not computed — the
  bump arithmetic in `tools/release_version.py` remains, now as the thing that **tells you what to
  tag** (`make next-version`) and as the forward-only check below.
- **The message is written from a template and checked, and a tag that says nothing is refused
  before anything is published.** `make release-tag` opens an editor on
  [`docs/RELEASE-TEMPLATE.txt`](../RELEASE-TEMPLATE.txt), not on an empty buffer — amended
  2026-10-11, after `v0.1.0`'s `Release v0.1.0` and a first draft of `v0.1.1` that described the
  tooling rather than the release. A blank annotation is a prompt to improvise; a template asks the
  reader's two questions in order, and its guidance is `#`-commented so `--cleanup=strip` never
  publishes it. A fifth rule follows from having one: a release still carrying the template's
  placeholders is refused, because an unfilled template is the right shape with nothing in it.
  Four rules, each one a thing that happened or would: the tag is annotated (a lightweight tag has
  no message at all — one missing `-a`); there is a subject; the subject does not merely restate the
  version (`Release v0.1.0` is indistinguishable from silence, and the check strips the words a
  restatement is made of — the project's name among them — rather than matching that one string); and there is a body after a blank
  line, because a headline is what the version number already gave the reader.
- **The check is a job of its own with no `needs`.** It costs seconds and it is the only failure a
  tagger can still fix for free. Behind the gates it would be reported after forty minutes, by which
  time everybody is waiting. The same refusal is available before pushing, as `make release-check`,
  and `make release-tag` deletes the tag it just created if the annotation does not pass — so a
  rejected attempt does not consume a version number.
- A **GitHub Release** is created as the last step, from the rendered notes: the tagger's prose
  first, then the commits since the previous release, then the three image references. The order
  carries the claim — evidence after the description, never instead of it.
- **`ADR-0029`'s objection is answered by what the failure leaves behind, not by preventing it.**
  The tag does now exist before the gates run. What does *not* exist, if a build fails, is the
  GitHub Release — so the outcome is a tag with no Release against it: visibly unfinished, in the
  one list a person actually reads. The version number is spent, which is the price, and a patch
  number is the cheapest thing this project has.
- Two guards replace the ones the dispatch had, because a tag changes what is answerable. "Which
  branch" is not a property of a tag push, so the check is whether the tagged commit is an
  **ancestor of `main`**. And the tag can no longer be the lock against republishing, since it
  exists by the time the job starts — the **GitHub Release** is the lock instead, so re-running a
  run whose images already landed is refused.
- **Releases go forward only.** A tag older than the newest release is refused, because `latest`
  would otherwise move backwards. A backport would need a second moving tag per series; that is a
  feature, and it is named as absent rather than left to be discovered.

**Amended 2026-10-11 — rule 1 did not work, and a release went out wrong because of it.** The
decision above says the tag's message *is* the notes. The implementation asked Git for
`%(contents)` of `refs/tags/<v>` and a comment claimed that was empty for a lightweight tag. It is
not: for a ref pointing at a commit, `%(contents)` is **the commit's message** — which has a
subject, has a body, and says something, so it passed every other rule. And `actions/checkout`
leaves the triggering tag as exactly such a ref, pointing at the commit it checked out.

`v0.1.1` was therefore published carrying `def905f`'s commit message as its release notes and
`fix(tests): wait for the rows a request makes, not for one` as its title, while the annotated tag
on the remote held the real description throughout. Three things changed, and the order matters:
the release jobs **restore the tag object and assert that it is one**; whatever reads a tag asks
for `%(objecttype)` **first**; and the Release's title now comes from the same function as its
body, so it goes through the rules too — it had been a second, unchecked `git tag --format`, which
is why the wrong title looked like a consequence of the wrong body rather than a second instance of
the same cause.

The lesson is not about Git. **A mechanism whose own rule depends on another tool's behaviour has
no rule**, and the way to find that out is to ask the tool rather than to believe a comment about
it.

## Consequences

- Positive: every release has a description, enforced rather than hoped for, written where prose is
  actually writable and stored as a Git object anybody can fetch.
- Positive: `git push origin v0.1.1` is the whole interface. No browser, no form, no `gh`.
- Positive: one trigger. The dispatch form is gone rather than kept as a second path.
- Positive: `FRD-137` §6's "no changelog or GitHub Release body" closes.
- Negative: **a failed release spends a version number.** `v0.1.1` cannot be retried as `v0.1.1`
  unless the tag is deleted on the remote first; the ordinary recovery is to tag `v0.1.2`. Accepted
  knowingly — `ADR-0029` named this cost and it has not gone away, only become visible in a place a
  reader looks.
- Negative: the version is typed by hand, so `release_version.py`'s numeric ordering is advice
  rather than a guarantee. The forward-only check is what keeps it from mattering: a typo that goes
  backwards is refused, and a typo that skips forward costs a number and nothing else.
- Negative: the tag's message cannot be edited after the fact in a way that changes the published
  Release. Fixing a description means editing the Release on GitHub, which then disagrees with the
  tag. A real wart; the alternative is notes in a file, which is a second source of truth.
- Follow-ups: nothing still generates a `CHANGELOG.md`; the published images are still a rebuild of
  the tested commit rather than the tested bytes (`ADR-0029`); and no Compose file pulls them yet.
