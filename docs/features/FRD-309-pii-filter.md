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

## 3. Functional Requirements

- **FR-1** A `pii_filter` step, configured with a model, an instruction, a notice and a failure
  policy. Every field optional except that a model must resolve.
- **FR-2 The rewritten prompt is what is dispatched.** Only the last user message's text; its
  attachments and tool parts are kept.
- **FR-3 The rewritten prompt is what is stored.** `request_logs.request_payload` carries the
  rewritten body; where the substitution cannot be applied the payload is **dropped**, never kept.
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
