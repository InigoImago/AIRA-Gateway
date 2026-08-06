# FRD-122 — A complete audit trail: what was asked, what was decided, what was served

> Phase: 8 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Origin: a review against `ADR-0013`'s "auditable brains" standard, 2026-08-06.
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

**4. Degradation is a global state, not a per-request fact.**
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
- **FR-5 Degradation on the row.** Which controls were operating on a fallback when this request
  was handled.
- **FR-6 Recording never fails a request.** The writer is already off the hot path with a bounded
  queue (`FRD-405`); a refusal row goes through the same path and inherits the same guarantee. A
  full queue must not turn a 429 into a 500.
- **FR-7 Reporting shows it.** `FRD-601` gains a breakdown by outcome, and the Reporting screen
  shows refusals beside successes. A wall being hit is a *number*, not a log search.
- **FR-8 No new payload exposure.** Refusal rows follow the same `store_payloads` and retention
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
| `degraded` | JSON? | features on a fallback at the time (FR-5) |

`model` keeps its meaning — what answered — so nothing downstream changes.

## 7. API / Interface Contract

No request-facing change. `FRD-601`'s report gains a breakdown by `outcome` and per-row refusal
counts; the Reporting screen shows them.

## 8. Security & Privacy

- FR-8: refusal rows carry no more payload than any other row and obey the same retention.
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
- **Unit (FR-6)** — with the writer queue full, a rate-limited request still returns **429** and
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
