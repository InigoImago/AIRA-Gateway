# FRD-309 — Replacing personal data before the prompt reaches the model

> Phase: 3 (pipeline) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: asked directly — *"I would like the option of inserting an LLM-based PII replacer. You
> pick a trusted model from the ones available in the use case, and you enter a prompt describing
> what should be filtered out. Can the returned message carry a disclaimer, controllable by the
> user?"* Builds on `FRD-300` (the pipeline), `FRD-308` (the release the model is chosen from),
> `FRD-125`/`FRD-125b` (a step's own model call is audited, billed, and fails closed).

## 1. Problem

A use case sends prompts written by people, and people put names, addresses, customer numbers and
phone numbers in them. Everything AIRA already does with such a prompt — send it to a model, store
it, keep it for the retention period, show it to an oversight role — is a decision about data
somebody did not mean to hand over.

`FRD-406` scrubs **credential shapes** from stored payloads and deliberately declines the PII half:
names and customer numbers are *the work*, and a redactor that mangles them produces payloads
nobody uses. That reasoning holds for a pattern-based redactor applied to everything. It does not
hold for one a use case switches on for itself, with an instruction it wrote, using a model it
trusts.

## 2. Goals & Non-Goals

**Goals**
- A pipeline step that rewrites the caller's text with personal data replaced, before dispatch.
- The model is chosen from the ones **released to that use case** (`FRD-308`); the instruction is
  the operator's own words.
- What is **sent** and what is **stored** are both the rewritten version.
- The caller can be told, in the operator's own words, that their input was changed.

**Non-Goals**
- Detecting personal data in attachments. A name inside a PDF survives this step — `FRD-110`'s
  stated blind spot, not a new one.
- Rewriting the system instruction. It is written by the use case rather than typed by a caller,
  and a redactor quietly editing the instructions a use case is built on is a different feature
  with a different risk.
- Guaranteeing that nothing gets through. **The control is exactly as good as the model behind
  it** — measured against `qwen3:0.6b`, which replaced one name and left another. That is why the
  field says *trusted model* and why the step is opt-in per use case.
- Detecting personal data in an **attachment**, including one sent with an embedding. A name
  inside a PDF survives this step — `FRD-110`'s stated blind spot, not a new one.

## 3. Functional Requirements

- **FR-1** A `pii_filter` step, configured with a model, an instruction, a notice and a failure
  policy. Every field optional except that a model must resolve.
- **FR-2 The rewritten prompt is what is dispatched.** Only the last user message's text; its
  attachments and tool parts are kept.
- **FR-3 The rewritten prompt is what is stored.** `request_logs.request_payload` carries the
  rewritten body; where the substitution cannot be applied the payload is **dropped**, never kept.
  Both halves of that, since 2026-08-27: a body whose text cannot be matched is dropped
  (`_rewritten_body`), **and so is one where the redaction never happened** — measured with an
  unreachable redactor, a refused request on two verbs kept the caller's name and address on a row
  nobody was served. `on_failure: allow` drops it as well: that flag says keep *serving*, and
  keeping *storing* is a second decision nobody made.
- **FR-4 A rewrite that cannot be trusted is a failure, not a redaction.** Empty, or far shorter
  than its input — a summary, a refusal or a preamble applied would send the model a different
  question than the caller asked, with a 200.
- **FR-5 A failure blocks by default**, and `on_failure: allow` is recorded on the audit row as the
  choice it is (`FRD-125`).
- **FR-6 The decision records that it redacted, never what.** A step, an action, the model. No
  count: the placeholder shape is whatever the operator's instruction asks for, so counting would
  mean dictating it, and a number nobody measured is worse than none.
- **FR-7 The notice is prepended to plain-text answers only**, and its absence on a structured or
  tool-call answer is recorded (`action: withheld`) rather than passed over.
- **FR-8** The step's model call is audited and billed as `pipeline:pii_filter` with `requests=0`
  (`FRD-125b`).
- **FR-9 It reaches an embedding too** (2026-08-27). `:embedContent`, `batchEmbedContents` and the
  KIRA surface's `/embed` run the steps that are about the **text itself**; a step about the
  *answer* still does not, and that distinction is `TEXT_ONLY_STEPS`. A router chooses a model to
  generate with and an embedding is not generated; an injection filter is about a prompt that will
  be **obeyed** and an embedding never is — blocking there would refuse a corpus for quoting the
  phrases it exists to index.
- **FR-10 Every text of a batch is offered to the redactor, and one that cannot be redacted refuses
  the whole request.** A batch carries *N* texts (`FRD-113` FR-6) and each is its own model call,
  because FR-4's check is per text and one call over a joined batch could not make it. Half a batch
  of vectors is not an answer: serving the texts that redacted while dropping the one that did not
  would send exactly the content this step exists to withhold, with a 200.
- **FR-11 A batch leaves one decision and one priced row.** The decision carries `texts` and
  `changed` — counts about the request's shape, never its content, which is what admits them to
  `SAFE_DECISION_KEYS`. The *N* calls are summed into one `pipeline:pii_filter` row: the same
  figure of money either way, and a request whose own row is not buried under 256 others.

## 4. Design

### 4.1 One dispatch table, which is what made a third step honest

`run` and `dry_run` each carried an `if/elif` chain over the step types, differing only in what
they did with the result. Every step was therefore written twice and kept saying the same thing by
hand. A step is now one function returning a `StepEvaluation`, and the two loops interpret it.

The refactor immediately found a divergence nothing had noticed: a router whose classifier could
not be reached fell through to the configured `default_model` in `run` and reported *unchanged* in
`dry_run` — so the builder's preview named one model while production used another, on the one
screen whose whole job is to say what the pipeline will do.

### 4.2 What the audit stores, and why it is the one place `FRD-122` bends

`FRD-122` holds that the log records what was **asked**. Here it records what was **sent**. That is
the point rather than an oversight: the original exists nowhere afterwards, which is what makes
this a data-protection control instead of a note about one. The row still carries the evidence —
that a redaction happened, with which model — so a review can see the control ran without the
control's own subject matter being in the database.

Measured before it worked: the model was sent the redacted prompt and the audit row kept the
original, because the payload written to `request_logs` is the *wire body* captured at the surface
while the pipeline rewrites the *canonical* request. Two places holding one fact — the body was
also a parameter of `accounting`, passed by nine call sites, beside `trail.body`. There is one now.

### 4.3 The notice is the first time AIRA edits an answer

Text only. A `responseSchema` document with a sentence in front of it is unparseable, and the
caller would get a parse error instead of an answer — worse than not being told. A tool call has no
text: the answer *is* the call. Both cases are recorded rather than skipped, because "no notice
shown" and "nothing was redacted" are different facts and an answer alone cannot distinguish them.

### 4.4 The dry run explains itself, and the panel sits where it is read

Reported after the step shipped: *the pipeline graph is short, the inspector on the right is long,
and the test area ends up right at the bottom — no scrollable areas please — and it would be good
if the dry run showed step by step what the models put out, so the results can be followed.*

Measured with a routing step selected: graph 478 px, inspector 688 px, test panel starting at
y=1232 in a 720 px viewport. The left column held 210 px of dead space while the panel whose whole
job is to say whether the configuration works sat half a screen under the fold. It is in that space
now (graph 397, panel below it starting at 675), and the inspector's `position: sticky` +
`overflow-y: auto` is gone — a scroll container inside a document that already scrolls gives a
reader two scrollbars, one of which only appears sometimes. The height cap it came with had its own
failure: a sticky element taller than the viewport pins its top and leaves its lower half
unreachable, which is what the routing step's default-model field used to do with enough
categories. What made the sticky panel look necessary was the empty left column.

**Two `<fieldset>`s, and the reason is the read-only rule.** A reader who cannot manage a pipeline
may still run it, and `<fieldset disabled>` makes every descendant inert with no way to exempt one
— wrapping the grid took the dry run away from exactly the people the read-only view is for.

The trace used to render `[blocked] injection_filter` per entry: what happened, never why. For all
three LLM-backed steps the why is a model's own answer, and nothing carried it. Each step is now a
card naming the model that was **asked** (never the model routed *to*), what it replied verbatim,
and — for the redactor — the caller's sentence before and after. `undetermined` is the case this
exists for: neither word, both words, an empty reply and a refused call are one verdict and four
different repairs.

**Shown, never stored.** `FRD-122` §5.3 keeps a classifier's prose off the audit row through an
allow-list, precisely so a step cannot start storing it by default. The reply travels in the trace
entry's `detail`, which is a screen; a step's `decision` is the durable record and is unchanged.
The gateway caps a reply at 600 characters, because a reasoning model asked for one word can
deliberate for several hundred tokens.

And a trace stays on screen while somebody keeps editing, so from the first change it describes a
configuration that no longer exists — a confident statement about the wrong thing, which is what
this panel exists to prevent. It is labelled out of date rather than cleared (the last result is
still the most useful thing there), and the browser-side live preview comes back, because at that
moment it is the only thing on the panel describing the pipeline as it now stands. The comparison
includes the **sample text**: a verdict about one sentence sitting under another is just as stale.

### 4.5 What a refused dry run must still tell you

Three defects, reported together from the console.

**The rejection message outlived what it was about.** It stayed until the next run, so a reader who
read it, changed the step it named and looked again was still being told about an attempt that no
longer matched anything on screen. It is now bound to the same subject the trace is — configuration,
sample text, and the option below — and disappears when that changes. Held in a *separate* signal
from the trace's, because a failed attempt and a displayed trace are about different things: one
signal for both stamped the new configuration onto the old trace, marking a stale result fresh.

**A block hid every step behind it.** `dry_run` stops where production stops, which is the truthful
thing for it to do, and it left somebody whose first step blocks unable to see that the rest of the
pipeline is even there. The remaining configured steps now appear as cards marked *not reached*,
taken from the configuration **as it was when the run was made** — the two differ exactly when the
trace is stale, and pairing this trace with a step list edited afterwards labels a card with the
wrong step's name.

**And the steps can be run anyway**, because *"I would like to see it, because then I can check
compatibility for my use case"* asks for results and not for an explanation of their absence.
`past_blocks` keeps evaluating after a refusal. Off by default and opt-in: the answer this panel
gives by default has to be the one production would give, and every step run past a block spends
real tokens on a call the served path never makes (recorded like any other, `FRD-125b`). Those
entries carry `after_block` and the screen badges them *would not run — refused above*; the refusal
that describes production stays the first one, and a later step refusing is a second simulated
outcome rather than a correction.

The console's checkbox was written, tested and **absent from the template** — the tests set the
signal directly and passed over a setting reachable from code and from nowhere a person could click.
The test now goes through the control.
