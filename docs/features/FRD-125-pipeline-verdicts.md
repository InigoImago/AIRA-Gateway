# FRD-125 — The pipeline's own model calls are first class

> Phase: 3 (correction) · Status: **Done (2026-08-06)** · Owner: AIRA · Last updated: 2026-08-06
> Related: `FRD-300`, `FRD-306`, `FRD-405` (fail-closed), `FRD-122` (audit), `docs/adr/ADR-0013.md`

## 1. Summary

A use case configured the LLM prompt-injection filter to **block**. An injection was sent. The
gateway answered `200`, and the model complied with it.

The cause was measured against a running stack, not inferred: the classifier asks the model for a
one-word answer inside a four-token allowance and — because it dispatched straight to the provider,
bypassing the catalog-based thinking resolution the serving path performs — never told the model
not to think. A reasoning model thinks by default. All four tokens went on reasoning, the answer
came back empty, and the verdict was a `bool`: `"INJECTION" in ""` is `False`.

A security control that is configured, displayed as active, and does nothing is the worst failure
shape this project knows. The same bug silently disabled `model_route`, which returned "no category
matched" for every request.

Counting rather than reading found the second half of it. One caller request with an LLM step makes
**two** model calls and left **one** audit row: the classifier's tokens were unaudited, unbudgeted
and unpriced. Against the real model that call costs roughly as much as the answer it guards, so a
use case running an LLM filter was reporting a little over half its actual spend.

The two halves are one feature — *the pipeline's own model calls are first class* — because they
have one cause. A step that dispatches straight to the provider skips everything the serving path
does around a model call: the catalog, the audit, the budget. Part (a) is the decision it got wrong;
part (b) is the money it never mentioned.

## 2. Goals & Non-Goals

**Goals**
- A verdict is three-valued. "I could not tell" is never folded into "clean".
- A classifier call asks for no thinking and has an allowance a one-word answer fits in.
- An unreachable verdict blocks a blocking filter by default; availability remains choosable but
  must be chosen.
- A step that ran and *passed* is recorded, so it is distinguishable from no step at all.

**Goals (part b — accounting)**
- A model call a pipeline step makes leaves its own audit row, priced.
- Its tokens and money count against the use case's budget.
- A step that *blocked* still records what deciding to block cost.

**Goals (part c — the refusal must not be paid for)**
- A caller who is already over budget, or over a rate limit, is refused **before** any pipeline
  step can call a model.

**Non-Goals**
- Making the LLM filter accurate. That is the classifier model's job — see §9.
- Reserving classifier tokens in advance. They are already spent by the time their size is known,
  because the pipeline runs *before* the reservation — routing has to choose the model the
  reservation is made against. Booking after the fact is the honest description of that.
- Preventing a *small* overshoot. See §4.6: the owner's decision is that a bounded overshoot is an
  acceptable price for the security step running, and this FRD does not try to remove it.

## 3. Functional Requirements

- **FR-1** `Verdict` is `injection | clean | undetermined`. `undetermined` covers an upstream
  failure, an empty reply, a reply containing neither expected word, and a reply containing **both**.
- **FR-2** With `action: block`, an `undetermined` verdict blocks. `on_undetermined: allow` restores
  the old behaviour explicitly.
- **FR-3** With `action: flag`, nothing blocks — an unreachable verdict must not quietly promote a
  flagging step to a blocking one.
- **FR-4** The refusal message distinguishes "blocked by the filter" from "could not be checked":
  one is a caller to talk to, the other is a classifier to fix.
- **FR-5** A classifier request sets thinking to `disabled` explicitly.
- **FR-6** Every filter outcome reaches the audit row, including a clean pass.
- **FR-7** The dry run shows the `undetermined` case, so an operator meets it in the builder rather
  than in production.

## 3a. Functional requirements (part b)

- **FR-8** Every model call a pipeline step makes is recorded as its own audit row, named
  `pipeline:<step>`, priced like any other.
- **FR-9** Those tokens and that money are booked against the use case's budgets — with
  `requests=0`, because the caller made **one** request and counting the classifier as a second
  would inflate every request figure and could trip a *request* limit for traffic nobody sent.
- **FR-9a** Booked into **both** stores: the system of record *and* the shared counter the guard
  reads. Postgres alone makes the spend visible to reporting and invisible to enforcement.
- **FR-10** The row is written for a blocked request too.
- **FR-11** The row never carries the prompt. The classifier is *sent* the caller's text; storing
  it again under a second row would double every retention and redaction question (`FRD-404`,
  `FRD-406`).
- **FR-12** A heuristic filter records nothing, because it spent nothing.

## 4. Design

### 4.1 Why blocking is the default, and why that is a reversal

The classifier previously failed **open**, documented in one line as "a classifier outage must not
take down legitimate traffic". That is a real concern and it is the wrong answer, for the reason
`FRD-405` already gave when it settled the identical question for rate limits: *the moment a control
stops working is the worst moment to stop applying it.* A filter that passes everything while the
builder shows it as active is not a degraded control — it is an absent one wearing the badge of a
present one.

The old behaviour is still reachable as `on_undetermined: allow`. The difference is that it is now a
choice somebody made, and it is on the audit row.

### 4.2 Both words is also undetermined

`"SAFE — no injection attempt here"` contains both. Picking a winner would be a precedence rule
nobody could predict from outside — the same argument `FRD-111` makes for a `thinkingConfig`
carrying two spellings. The model was asked for one word and gave two; that is not an answer.

### 4.3 The heuristic is never undetermined

A regex matches or it does not, and nothing it depends on can be unavailable. That asymmetry is
why the heuristic remains the default and why the LLM mode is the one that needed a policy.

### 4.4 Why the hook is inside `run_pipeline`

`run_pipeline` is the only thing that produces these calls, and both surfaces call it — so one
`finally` there covers the served path, the blocked path, the Gemini surface and the KIRA surface
at once. The alternative was a hook at each surface's boundary, which is the shape that let
`:embedContent` slip past the pre-dispatch gate.

The collector is **passed in**, exactly as `decisions` already is, so the spend survives the
exception a blocking step raises. That symmetry is not decoration: the two facts have the same
lifetime and the same failure mode.

### 4.4a Recording it is not enforcing it

The first version of this booked into Postgres alone. Postgres is the system of record, so
reporting was correct — and `FRD-405`'s guard reads the **shared counter**, which never saw the
spend until it expired and rebuilt, up to `COUNTER_TTL_SECONDS` later.

Found by asking the question rather than by a test: *does the filter cost count against the budget?*
A small cost cap and four requests answered it — the counter read 41 000 against a limit of 40 000
and the next request was served. Both stores are written now, and a live re-run refuses the third
request at 40 200.

The degraded case keeps the safe direction: if the counter is unreachable, Postgres still has the
figure and the counter rebuilds from it on expiry. The window is bounded and the counter is *low*,
so a caller is under-charged rather than refused for spend that never happened.

### 4.5 What it turned out to cost

Measured against the real model: the classifier's call costs **roughly as much as the answer it
guards**. A use case running an LLM filter was therefore reporting a little over half its actual
spend, and its budget counters never saw the difference at all.

### 4.6 The overshoot, bounded — and the owner's decision

Booking after the fact means a use case can end a period slightly over its limit. Asked directly,
the owner's answer was that this is acceptable: *going a little over the budget is worth it to
have the security step run.* Recorded here so nobody later "fixes" it into refusing requests before
their filter has run, which is the trade in the other direction.

What was **not** acceptable, and was the state of the code, is an *unbounded* overshoot. The
pipeline ran before the budget guard, so a use case one request past its limit kept running its LLM
filter on every subsequent request — all refused with a 429, all billed for the classifier. Measured
against a 20 000 cost limit: one served request, seven refused, **72 400 spent**, still climbing. A
client with a retry loop spends without bound; that is a denial-of-wallet, not a budget.

`guard_before_work` closes it: the two controls that need no model — the rate limit, and *is this
use case already over* — run before the pipeline. The same probe now stops at **25 600** and stays
there across six further refusals. The remaining overshoot is what was in flight when the limit was
crossed, which is the "little over" the owner agreed to.

Two details cost a draft each, and both are old lessons:

- The gate sits **before the verb branch**, not inside `run_pipeline`. Embeddings have no pipeline,
  so the tidier placement would have left `:embedContent` unlimited — the same verb, the same way,
  as `FRD-405` B3.
- `units` is computed **before** the gate. The first draft took one unit early and claimed in a
  comment that the batch weight was taken again later. It was not; a batch of 500 was metered as
  one request. A comment asserting a rule the code does not have, caught by a test that already
  existed.

## 5. Testing

- Hermetic: `test_pipeline_classifiers.py`, `test_pipeline_engine.py`.
- Mutation: `Z1`–`Z10`, plus `P1`/`P2` **re-anchored** — they pointed at a line this change moved,
  and a mutation whose anchor has moved protects nothing.
- Integration: the injection that was served is now refused, against the real model.

### 5.1 What the mutation harness caught that the tests did not

Two properties came back undefended on the first full run.

The budget booking was asserted nowhere: every accounting test looked at the **audit row**, and the
app under test had no budget configured, so booking zero changed nothing under observation. A test
that configures one and counts now exists.

A third gap did not come from the harness at all, and is the more interesting one: the *first*
version of the enforcement test passed against the broken code. On a cold counter the guard seeds
from Postgres, so a Postgres-only write is visible anyway — the test never reached the path it was
named after. It warms the counter first now. This is the trap `CLAUDE.md` §3 already names, met
again: *a test whose setup never reaches the path it is named after*.

The classifier's upstream-failure branch was undefended because part (b) **moved the line the
mutation was anchored to**. The harness said so rather than passing quietly, which is what makes
"a mutation whose anchor has moved protects nothing" a checkable claim rather than a maxim. Chasing
it also found a second copy of the router's logic left behind by the same refactor — `classify`
re-implementing `classify_text`, with the untested error branch in the copy. It delegates now.

## 6. Rollout

Behaviour change for anyone running `mode: llm` with `action: block`: requests the classifier
cannot label are now refused instead of served. That is the point. `on_undetermined: allow` restores
the previous behaviour for a deployment that prefers it.

## 7. Open Questions

- Should `undetermined` fall back to the **heuristic** classifier rather than to a policy? It is
  cheap, always available, and never undetermined. Attractive; not built, because "the filter you
  configured was replaced by a different one" is its own kind of silent substitution and deserves
  its own decision.

## 8. Dependencies & Risks

None new. The risk is availability: a deployment whose classifier model is unreachable now refuses
traffic on blocking filters. That is the intended trade and it is configurable.

## 9. An operational finding, not a defect

Against `qwen3:0.6b` the LLM filter answers `INJECTION` to *everything*, including "What is 2 + 2?".
The gateway is behaving correctly; the model is not a usable security classifier at that size.

Worth stating plainly because the builder makes the LLM mode look like the stronger option: **the
LLM filter is exactly as good as the model behind it, and a small model produces a filter that
blocks everything.** The heuristic has no such failure mode. An operator choosing `mode: llm` should
point it at a model they would trust with the judgement, and should use the dry run to see what it
actually does before enabling `block`.
