# FRD-122 — A complete audit trail: what was asked, what was decided, what was served

> Phase: 8 · Status: **Done** · Owner: Vadim Scheibe
> Origin: a review against `ADR-0013`'s "auditable brains" standard, and against the owner's feature definition
> (PRD §1.1) — *"welches System wann was womit aufgerufen hat"*.
> Extends `FRD-103` (request log). Touches `FRD-300`, `FRD-405`, `FRD-601`, `FRD-115`.

## 1. Problem

`ADR-0013` states the gateway's purpose as providing **auditable** model access. Reviewed against
that word, the request log has four gaps. Each is small to fix and each defeats a question the
audit trail is supposed to answer.

**1. A refused request leaves no record at all.**
`_enforce_pre_dispatch` raises, the route returns a 429, and `record_request` is never reached. The
same is true of a 404 for an unknown model and of validation failures. So `request_logs` contains
**what was served, not what was asked** — and the questions that go unanswered are exactly the ones
someone asks after an incident: who was throttled, how often, starting when, and was that why the
application misbehaved on Tuesday. It also means `FRD-601`'s `failed_requests` column can only ever
show upstream failures; a use case hitting its budget wall all day reports as perfectly healthy.

This is the significant one. A control that leaves no trace when it fires is a control nobody can
review.

**2. Only the served model is recorded, never the requested one.**
`dispatch_with_fallback` tries candidates in order and the row records
`canonical_response.model` — correct, but partial. With cross-vendor fallback (`ADR-0012`) a
request asking for Gemini can be answered by Claude, and nothing in the durable record says a
substitution happened. "Why did this month's Anthropic spend triple" has no answer in the data.

**3. The pipeline's decisions live on a span, not on the row.**
`aira.pipeline.model` is a span attribute. Spans are **sampled** (`AIRA_OTEL_SAMPLE_RATIO`) and
kept for days; the request log is the record of truth and is kept for as long as retention says. So
*why* a model was chosen — an LLM classifier's routing decision, a flag from the injection filter —
is durably recorded nowhere. For the one component that makes a judgement about a caller's prompt,
that is the wrong way round.

**4. The calling *system* is not identifiable.**
An API key carries a `prefix` (its identity) and a `subject` (the person who issued it). The audit
row records the subject and never the prefix. So five keys issued for one use case by one
administrator produce five indistinguishable identities in the log, and the question the owner's
own definition puts first — *which system called what, when, with which model* — cannot be answered
about the system.

The consequence is sharpest exactly when it matters most: a leaked key can be revoked, but the
blast radius cannot be assessed. Which requests came from it, what they asked, what they got back,
over what period — none of it is reconstructable, because the log cannot separate that key's
traffic from its siblings'.

**5. Degradation is a global state, not a per-request fact.**
`DegradationLog` tells `/readyz` that rate limiting is on its per-instance fallback or that budgets
are on the racy Postgres path (`FRD-405`). It says nothing about *which requests* were handled that
way. A request budgeted without the atomic guarantee is indistinguishable from one with it, so
"were these requests actually under the controls we claim" cannot be answered retrospectively.

## 2. Goals & Non-Goals

**Goals**
- Every request that reaches attribution produces a row — including the ones we refused.
- The row distinguishes **asked**, **decided** and **served**.
- The row records whether the controls were operating fully or degraded.
- Reporting (`FRD-601`) can show refusals, so a wall being hit is visible without reading logs.

**Non-Goals**
- Recording requests that never authenticated. A 401 is an auth event, belongs in the auth log, and
  writing a row per unauthenticated request makes the audit table a denial-of-service target.
- Storing more payload. This is about *decisions*, not content; `FRD-404` and `FRD-406` are
  unchanged.
- A separate audit service. One table, richer rows.

## 3. User Stories
- As **IT Security**, I want to see that a use case was refused 4 000 times last Tuesday, so that
  an incident report has evidence rather than recollection.
- As **IT Steuerung**, I want to see when a request was answered by a different vendor than it
  asked for, so that a spend shift has an explanation.
- As a **use-case administrator**, I want to see that my callers are hitting a limit, without
  needing access to logs.

## 4. Functional Requirements

- **FR-1 Refusals are recorded.** Rate-limited, over budget, unknown model, failed validation,
  blocked by the pipeline — each produces a row with its status and an **outcome reason**.
- **FR-2 Outcome reason is a small closed vocabulary**, not free text: `served`, `rate_limited`,
  `budget_exceeded`, `blocked_by_pipeline`, `model_not_found`, `invalid_request`,
  `upstream_error`, `no_capable_model` (`ADR-0012` §3). A closed set is what makes it groupable in
  reporting; free text would be greppable and nothing more.
- **FR-3 Asked, decided, served.** `requested_model` (what the caller named), `model` (what
  answered), and — where they differ — why: routing decision or fallback position.
- **FR-4 Pipeline decisions on the row.** Which steps ran, and each one's verdict, in a compact
  form. Not the reasoning text, not the classifier's prompt: the decision.
- **FR-5 The calling system is identified.** For an API-key principal the row records the key's
  **prefix** — the key's identity, never any part of its secret. For an OIDC principal it records
  the client id where the token carries one. `subject` keeps its meaning (who the credential
  belongs to); this answers *which credential was used*, which is a different question and the one
  incident response asks first.
- **FR-6 Degradation on the row.** Which controls were operating on a fallback when this request
  was handled.
- **FR-7 Recording never fails a request.** The writer is already off the hot path with a bounded
  queue (`FRD-405`); a refusal row goes through the same path and inherits the same guarantee. A
  full queue must not turn a 429 into a 500.
- **FR-8 Reporting shows it.** `FRD-601` gains a breakdown by outcome, and the Reporting screen
  shows refusals beside successes. A wall being hit is a *number*, not a log search.
- **FR-9 No new payload exposure.** Refusal rows follow the same `store_payloads` and retention
  rules as any other.

## 5. Design & Architecture

### 5.1 Where the refusal row is written

The natural instinct is to add `record_request` at each `return _error(...)`. There are several,
they are easy to miss, and the next verb added would miss them again — this is the same shape as
the bug where `:embedContent` bypassed the pre-dispatch gate entirely because the gate lived inside
one branch.

So the refusal row is written **at one place**: the route's exception boundary. `RateLimited`,
`BudgetExceeded`, `PipelineBlocked` and the validation failures already surface as exceptions or as
a single `_error` helper; the recording hangs off that, not off each call site. Adding a control
later then produces a row without anyone remembering to add one.

### 5.2 Asked, decided, served — three columns because they are three facts

`requested_model` is what the caller named. `model` stays what answered, so every existing query,
report and index keeps working — the new column is additive and the meaning of the old one is
unchanged, which matters because `FRD-601`'s cost breakdown groups by it.

Where they differ, the row records why: `route` (a pipeline classifier chose it) or `fallback:N`
(candidate *N* in the chain answered). With `ADR-0012`'s cross-vendor chains this is what turns "the
Anthropic bill grew" from a mystery into a query.

### 5.3 Decisions, not reasoning

FR-4 records that `injection_filter` ran and returned `pass`, that `model_route` chose category
`analysis` → `claude-…`. It does **not** record the classifier's prompt or its explanation.

Two reasons, and the second is the one that matters: the reasoning is model output about a caller's
prompt, so it inherits every data-protection question the prompt itself has — and it would sit in a
column that redaction (`FRD-406`) cannot meaningfully process. Decisions are short, closed and safe;
reasoning is neither.

### 5.4 Degradation is per-request because that is when it is true

`DegradationLog` is a live view: it says what is broken *now*. An audit needs what was broken
*then*. So the row carries the set of degraded features at the moment it was handled — an empty set
in the normal case, which costs nothing to store and is the answer to a question that otherwise
requires correlating two systems by timestamp and hoping.

### 5.5 The volume question, answered before it is asked

Recording refusals means a caller in a retry loop against a rate limit writes a row per attempt.
That is a real increase and it is the right one: a retry storm is *precisely* the event the audit
trail should show, and a limit that leaves no trace when it fires is a limit nobody can review.

The bounded queue (`FRD-405`) already protects the request path, `FRD-404`'s retention already
bounds the table, and reporting aggregates rather than scanning. If a specific deployment finds the
volume genuinely excessive, the answer is a retention period for refusal rows shorter than for
served ones — a configuration change, not a reason to record nothing.

## 6. Data Model

`request_logs` gains (one migration, all nullable so existing rows stay valid):

| Field | Type | Notes |
|---|---|---|
| `outcome` | string(32) | closed vocabulary, FR-2; indexed for reporting |
| `requested_model` | string(128)? | what the caller named (FR-3) |
| `model_selection` | string(32)? | `direct` / `route` / `fallback:N` |
| `pipeline_decisions` | JSON? | step → verdict (FR-4) |
| `credential` | string(64)? | API-key prefix or OIDC client id — *which system called* (FR-5) |
| `degraded` | JSON? | features on a fallback at the time (FR-6) |

`model` keeps its meaning — what answered — so nothing downstream changes.

## 7. API / Interface Contract

No request-facing change. `FRD-601`'s report gains a breakdown by `outcome` and per-row refusal
counts; the Reporting screen shows them.

## 8. Security & Privacy

- FR-9: refusal rows carry no more payload than any other row and obey the same retention.
- §5.3: decisions, never reasoning — the one place this feature could have quietly widened what is
  stored about a caller's prompt.
- Positive for security: **refusals become reviewable.** Today a caller probing for an unrestricted
  model, or grinding against a limit, leaves nothing durable. That is a detection gap as much as an
  audit one.

## 9. Observability

The row becomes the durable counterpart of what spans already show. Spans stay as they are —
sampled, detailed, short-lived; the row is unsampled, compact and kept.

## 10. Testing & Acceptance Criteria

- **Unit** — each refusal path writes a row with the right `outcome`: rate-limited, over budget,
  unknown model, invalid request, blocked by pipeline, no capable model. Each written to fail first
  against today's code, where none of them writes anything.
- **Unit** — a fallback answer records `requested_model` ≠ `model` and `fallback:1`; a routed
  answer records `route`; a direct answer records neither as a difference.
- **Unit** — pipeline decisions land on the row; **the classifier's reasoning text does not**
  (asserted on the row's contents, §5.3).
- **Unit** — a request handled while a feature is degraded records it; a healthy one records an
  empty set.
- **Unit (FR-5)** — two keys of the same use case issued by the same person produce **different**
  `credential` values on their rows, and the value is the prefix and never any part of the secret.
  Written to fail first against today's code, where the two are indistinguishable.
- **Unit (FR-7)** — with the writer queue full, a rate-limited request still returns **429** and
  not 500. The audit must never become a way to fail a request that was correctly refused.
- **Integration** — a real throttled request appears in the report's refusal count.
- **Mutation** — the refusal path actually records (mutate the boundary away and the tests go red);
  `outcome` is actually the closed vocabulary; degradation is actually per-request and not read live
  at report time.

**Acceptance**
- *Given* a use case at its request budget, *when* a caller makes 50 requests, *then* the report
  shows 50 refusals with reason `budget_exceeded`, and no upstream call was made.
- *Given* a chain `[gemini-…, claude-…]` whose primary is unavailable, *when* a request is served
  by the fallback, *then* the row records both models and `fallback:1`.
- *Given* a use case with two API keys, *when* one is reported compromised, *then* every request
  made with **that** key can be listed — with its model, time and outcome — and the other key's
  traffic is not in the list.

## 10a. What was actually built (2026-08-06)

All five gaps closed. `aira_gateway/audit.py` holds the closed vocabulary and the `AuditTrail` a
route fills in as it goes; `request_logs` gains six nullable columns (migration `0012`), indexed on
`outcome` and `credential`; the refusal row is written at the route's exception boundary, so the
branches now *raise* where they used to `return _error(...)`.

Two things came out of building it that were not in the plan:

- **The full-queue test found a real defect.** With the writer failing, a correctly refused request
  returned **500** instead of 429 — the audit write itself was turning a valid refusal into an
  error, and a client would have retried into the limit it had just hit. FR-7 is now enforced by a
  guard around the refusal write, which is deliberately *not* extended to the success path: there,
  a failed write means a served request went unrecorded, and failing loudly is the right answer.
- **A refusal must name the model that was actually attempted.** A request routed elsewhere and
  then refused was recording the model the caller typed, which blames a model that was never
  called. Fixed and pinned by its own test.

Coverage: 20 hermetic tests, 4 integration tests (the migrated schema is asserted separately,
because the hermetic suite builds its schema with `create_all` and would pass with an empty
migration), 3 frontend tests, and mutations **T1–T8**, each verified to be caught. The existing
`M23` anchor needed repairing — the pre-dispatch gate lost its `try/except` when refusals began
raising — which is the mutation harness doing its job.

## 11. Dependencies & Risks

- `FRD-103` (the row), `FRD-405` (the writer and the degradation log), `FRD-601` (reporting),
  `FRD-300` (pipeline decisions).
- **Risk — volume.** §5.5, answered.
- **Risk — the closed vocabulary ossifies.** A new control needs a new value, and a value added
  without updating reporting shows up as an unlabelled bucket. Mitigated by deriving the reporting
  labels from the same enum rather than restating them.
- Low risk overall: additive columns, one recording site, no request-path change.

## 12. Rollout / Demo

Demo mode gains a use case with a deliberately tiny limit, so the Reporting screen shows refusals
beside successes out of the box — which is also the fastest way to see that the feature works.


## 12. Extension (2026-08-06) — the refusal that ran before the boundary

`FR-1` says the log records what was **asked**, not only what was served, and it was closed at the
route's exception boundary: one site, because a fact repeated at every exit is a fact eventually
forgotten at one of them. One refusal never reaches that boundary. The request-body ceiling is pure
ASGI middleware and answers **before any route runs**, so a 20 MB body was refused with a 413 and
left no trace whatsoever.

Found by posting one at a running gateway and counting rows — not by reading the code, which is
consistent about this rule everywhere it can be read.

Both exits from that decision (a declared `Content-Length` over the ceiling, and a body that
declared none and was cut off mid-read) now record through **one** function, `record_oversized`.
The row carries a new closed-vocabulary outcome, `request_too_large`, rather than reusing
`invalid_request`: "somebody keeps posting 20 MB" and "somebody sent malformed JSON" are different
operational facts, and a shared bucket hides the first inside the second.

**The row is deliberately unattributed.** The credential in the header has not been verified at
that point, and recording it would let anybody write another system's identity into the audit trail
by sending one oversized request. An unverifiable claim is not evidence; the source IP, the target
and the outcome are. Same rule as "unpriced is not free" and "undeclared is not permitted", applied
to identity. The body is not stored either — it is over the ceiling, and keeping what we refused to
read would undo the reason for refusing it.

### What is still not recorded, and why

**Authentication failures.** A 401 leaves no row. That is a decision, not an oversight: a request
that never presented a valid credential is a *security* event, and it belongs with anomaly detection
and incident response (`FRD-500`/`FRD-501`/`FRD-503`) rather than in the usage log, where it would
appear in spend reports as a refusal attributed to nobody. Recorded here so that whoever builds
those features finds the question already asked.

Mutations `Y9`–`Y11`.
