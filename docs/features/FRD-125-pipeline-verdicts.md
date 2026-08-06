# FRD-125 — A classifier that did not answer has not said "clean"

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

## 2. Goals & Non-Goals

**Goals**
- A verdict is three-valued. "I could not tell" is never folded into "clean".
- A classifier call asks for no thinking and has an allowance a one-word answer fits in.
- An unreachable verdict blocks a blocking filter by default; availability remains choosable but
  must be chosen.
- A step that ran and *passed* is recorded, so it is distinguishable from no step at all.

**Non-Goals**
- Making the LLM filter accurate. That is the classifier model's job — see §9.
- The accounting half (a pipeline's own model calls are unbudgeted and unaudited). Separate work.

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

## 5. Testing

- Hermetic: `test_pipeline_classifiers.py`, `test_pipeline_engine.py`.
- Mutation: `Z1`–`Z5`, plus `P1`/`P2` **re-anchored** — they pointed at a line this change moved,
  and a mutation whose anchor has moved protects nothing.
- Integration: the injection that was served is now refused, against the real model.

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
