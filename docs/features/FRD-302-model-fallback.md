# FRD-302 — Model fallback: every model access is a chain

> Phase: 3 · Status: **Built** · Owner: Vadim Scheibe
> Related: `docs/PRD.md` §1.1 row 9 (Modell-Fallback); `ADR-0012` §3, `ADR-0032`; `FRD-300`
> (the pipeline this chain belongs to), `FRD-308` (the release every named model is checked
> against), `FRD-122` (what the audit row says about a substitution), `FRD-125b` (what a
> classifier call costs).

## 1. Summary

A use case names an ordered list of models to try when the one it asked for cannot answer. This
document is where that chain is specified — all of it, because until 2026-10-11 there were two
model accesses the chain did not cover and the reasons were spread across a module docstring, a
structural test's exemption list and `ADR-0012` §3.

**Three chains, one rule.** The *dispatch* chain serves the caller. The *stream* chain is the same
chain, opened rather than awaited, and it runs for as long as nothing has reached the caller. A
*step* chain belongs to one pipeline step and decides which model that step asks about the
request. The rule they share is `ADR-0012` §3's: a chain must not degrade a request silently — a
candidate that would answer a *different question* is passed over rather than quietly accepted,
and a chain that has nothing left to offer fails rather than serving less than was asked for.

This FRD was referenced by `FRD-300`, `FRD-304`, `docs/ROADMAP.md` and `docs/PRD.md` for two
months before it existed. The chain was built; its document was the companion nobody wrote.

## 2. Goals & Non-Goals

**Goals**
- One ordered chain per use case (`fallback_models`), applied wherever this gateway serves a
  caller's generation — streamed or not.
- A chain per model-backed pipeline step, so a classifier outage does not take a use case's
  traffic with it.
- Every hop meets every dispatch condition: residency (`FRD-115`), installation approval
  (`FRD-307`), per-use-case release (`FRD-308`), media types (`FRD-110`), thinking, schemas, tools
  and sampling (`requirements.py`).
- What the chain did is on the audit row: which candidate answered, which were excluded and why,
  which were tried and failed and what the upstream said.

**Non-Goals**
- **Retrying the same model.** A chain is about an alternative, not about patience; a retry policy
  is a different feature with a different failure mode (it multiplies load on a struggling
  upstream).
- **Falling back mid-stream.** Once a chunk has reached the caller the answer is committed.
- **A chain for embeddings.** Another model's vector is not a substitute, and half a batch from
  two models is worse than a failure.
- **Falling back on a verdict.** A classifier that answered has answered; see FR-8.

## 3. User Stories

- As an **administrator of a use case**, I want a second and third model for my traffic so that a
  rate limit or an outage at one vendor does not stop my users — whether they stream or not.
- As an **administrator of a use case**, I want my prompt-injection filter and my PII redactor to
  have a second model so that an outage of one classifier does not block every request I serve.
- As **IT Security**, I want the audit row to say which model answered and why the first one did
  not, so that "the answer came from somewhere else" is evidence and not a guess.
- As a **caller**, I want a streamed request that nothing can serve to fail like a failure, so
  that my client's error handling sees it.

## 4. Functional Requirements

- **FR-1 The chain.** A use case's pipeline carries `fallback_models`, an ordered list of at most
  16 names, each at most 128 characters. The candidates are the request's model followed by the
  chain, with the request's own model removed from the rest of the list so it is never tried twice.
- **FR-2 Conditions per hop.** Each candidate is asked of `requirements.py` before it is called. A
  candidate that fails a condition is **skipped** with the condition's reason; it is not an error.
- **FR-3 What answers.** The first candidate that produces a response answers, and the response is
  recorded, priced and reported under **that** model's name, with `model_selection` saying a
  fallback was taken (`FRD-122` FR-3).
- **FR-4 Nothing to offer.** When no candidate was tried — every one excluded — the request fails
  with `NoCapableModel`, which names each candidate and its reason: a configuration fault reads as
  one, not as an upstream outage. When a candidate *was* tried and failed, the request fails with
  the upstream's own error, which a caller may usefully retry.
- **FR-5 Addressing moves with the model.** A hop re-reads the catalogue, so a candidate is sent
  to its own platform, publisher and region, never the primary's (`ADR-0011`, `FRD-507`).
  - **FR-5a And so do the defaults.** The chain starts from the request **as the caller made it**
    (`Prepared.asked`) and fits it to each candidate the way the model routing chose is fitted
    (`prepare.fitting`): the output cap that candidate declares as its default when the caller set
    none (`FRD-114` FR-2), its own default thinking (`FRD-111`), and a cache marker only if it
    declares prompt caching (`FRD-133`). A fallback once received the primary's — a primary
    declaring a default cap of 5 tokens cut the fallback's answer at 5 with a `200`, its default
    thinking became a condition the fallback failed, and its cache marker went to a model that
    caches nothing. The conditions of FR-2 ask about what the caller asked for; a candidate the
    fit cannot take — a thinking budget that does not fit inside its own default cap — is skipped
    with that reason.
  - **FR-5b The caller's cap is a condition per hop** (`OutputCapAccepted`). A cap the caller set is
    checked against the model routing chose before dispatch, as the caller's `400`; a fallback
    declaring a lower maximum is skipped by name rather than called, where its vendor would refuse
    it after the call or answer with less than was asked for.
- **FR-6 A stream uses the chain** (`ADR-0032`). For each candidate in turn: conditions, open the
  upstream stream, and pull its first chunk — the `__anext__` that issues the HTTP request. A
  candidate that fails before producing that chunk is passed over, and its iterator is closed. The
  chunk is then handed to the surface together with the chosen model, so the recorded, billed and
  reported model is the one that answered.
  - **FR-6a** The HTTP response begins once a candidate has produced a chunk — or after
    `AIRA_STREAM_KEEPALIVE_SECONDS` (15), whichever comes first (`ADR-0046`). A streamed request
    that no candidate can serve within that time is an error **status**, recorded like any other
    refusal — not a `200` carrying an empty array. A chain still looking after it goes on looking
    while the response begins, still passing over a candidate that fails before its first chunk,
    and a refusal it ends in is the stream's last element, recorded with the status it would have
    had. A model that reads a large context or thinks before its first word is minutes of silence,
    which a proxy between caller and gateway ends by closing the connection.
  - **FR-6b** After the first chunk the stream is committed: a later failure ends it and is
    recorded, with whatever was delivered.
  - **FR-6c** A candidate whose stream ends with **no chunk at all** has answered. The chain stops
    there, because a stream with a finish reason and no text is how a vendor's own safety filter
    refuses.
- **FR-7 A step reaches a chain.** `injection_filter` (in `llm` mode), `model_route` and
  `pii_filter` take an optional `fallback_models` in their own step configuration — same bounds as
  FR-1 — tried in order after the model the step names.
- **FR-8 Reached, not agreeable.** A step's chain moves on only when a candidate could not be
  **reached**: no provider resolves it, or the call failed. A candidate that answered keeps its
  answer, including an `UNDETERMINED` verdict or a rewrite too short to trust. A chain that
  shopped for a better verdict would be a filter that weakens under load.
- **FR-9 The safe direction is unchanged.** When no candidate of a step's chain can be reached,
  the step degrades exactly as it did with one model: the LLM filter to the heuristic, the router
  to its `default_model` recorded as `not_asked`, the redactor to a block (or to `on_failure:
  allow`, the operator's recorded choice).
- **FR-10 Every named model is released.** Management validates **every** model a pipeline names —
  each step's model, each category's target, each `default_model`, the dispatch chain and now each
  step's own chain — against the use case's release (`FRD-308`), and the gateway's dry-run surface
  applies the same list. A name it did not see would be a hole. And because a release withdrawn or
  an approval revoked after the save leaves the saved pipeline as it was, a step also asks the
  installation's conditions of each model **before calling it** (`installation_requirements`),
  passing over one it may not call with the reason (`ADR-0032`, amended).
- **FR-11 A failed attempt is recorded.** A candidate that was tried and failed leaves a
  `step: dispatch`, `action: failed` decision carrying the model and the upstream's reason, beside
  the `action: skipped` rows a condition produces. Both reach the audit row's
  `pipeline_decisions` — **whether a later candidate answered or none did**: the error an
  exhausted chain raises carries both lists (`PassedOver`), and the refusal's row records them.
  Until the documentation was read against the code, only a chain that ended in an answer did;
  the row of a refused request carried no decisions at all
  (`gateway/tests/test_an_exhausted_chain_says_what_it_tried.py`, mutations `DA-1`, `DA-2`).
- **FR-12 Bounded in both planes.** The gateway re-applies Management's bounds on a step's chain
  when it parses a config (`ADR-0018`), because the read-model is also reachable by a direct write
  and by an unvalidated dry-run payload.

## 5. Design

**Where the code is.** `gateway/src/aira_gateway/pipeline/dispatch.py` holds both forms of the
chain — `dispatch_with_fallback` returns a finished response, `stream_with_fallback` returns the
chosen model plus an iterator whose first chunk has already been proven. They share the candidate
list, the conditions, the catalogue lookup and the record of what was skipped or failed; a
difference between them would be a difference between two verbs nobody asked for.

```
caller ──▶ prepare (pipeline runs; model may be re-routed; chain is read)
            │
            ├─ :generateContent ─▶ dispatch_with_fallback ─▶ response
            └─ :streamGenerateContent ─▶ stream_with_fallback ─▶ (model, first chunk, rest)
                                              │
                                     conditions · catalogue · open · first __anext__
```

**Why the first chunk is the test.** A streaming adapter issues its HTTP request on the first
`__anext__`, not when the iterator is built (`telemetry.model_call_chunks` says so, and it is why
that wrapper exists). So pulling one chunk is exactly the point at which a candidate has proven it
can serve, and it is the last point at which switching is invisible to the caller.

**The wrapper owns what it wrapped.** The iterator handed to the surface is a generator that
yields the proven chunk and then the adapter's own. That wrapper is a layer, and a layer in a
stream inherits a contract nobody writes down: a caller that hangs up closes the iterator *it* is
reading, and closing an async generator does **not** close the one it was iterating. Before the
chain the surface read the adapter directly, so a disconnect released the upstream's response; the
wrapper quietly took that away until it was given back in a `finally`. The leak was found by
asking for it in a test, not by reading the code.

**The span that identifies the attempt.** Each attempt is wrapped in `model_call_span` where the
request is issued, so each tried model leaves its own model-access record (`FRD-619`), and the
contextvar it sets is entered and left in one context — an entered context manager handed across
an async-generator boundary resets a token in a context that never created it.

**A step's chain.** `StepEvaluator` builds the ordered candidate list (the step's own model, then
its chain, each one that resolves to a provider) and asks each in turn through one helper, so the
three steps cannot drift apart. Each classifier reports whether it **reached** its model:
`Classification`, `Routing` and `Redaction` each carry `reached`, set to `False` only where the
call raised. That is the signal the chain moves on, and it is a field rather than an inference
from an absent `ModelCall` — the heuristic classifier also has no call, and reading "no call" as
"not reached" would have made the heuristic retry a chain it never asked for.

## 6. Open

- **A stream cannot fall back after its first chunk.** Stated, not deferred: splicing two models'
  text into one answer is worse than the failure.
- **Nothing retries the same model.** A chain of one is still a chain of one.
- **A chain can be slower than no chain.** Every hop costs its own timeout before the next is
  tried, so a dead-but-not-refusing upstream delays the answer by its connect timeout per
  candidate. The adapters' timeouts bound it; nothing shortens the chain under pressure.
- **A step's chain has no notion of a cheaper classifier.** The order is the operator's; the
  engine does not reorder by price or latency.
- **The two `fallback_models` look alike in the JSON.** One is the use case's, one is a step's.
  The console labels them; the payload does not.
- **The reservation is made against the model routing chose** (`FRD-405`). A fallback that is sent
  a larger cap of its own (FR-5a), or costs more per token, is settled at what it used — the
  reservation is an estimate, and a chain does not widen it to its largest candidate. Under
  concurrency that lets requests in flight count for less than a fallback then spends.

## 7. Testing & Acceptance Criteria

`gateway/tests/test_pipeline_dispatch.py` — the chain's own behaviour: order, the primary tried
first, a condition skipping a candidate, `NoCapableModel` when every candidate is excluded, the
upstream error when one was tried, addressing re-read per hop.

`gateway/tests/test_a_stream_falls_back_until_a_chunk_is_out.py` — the stream chain: a primary that
fails before its first chunk is replaced and the caller sees the second model's text; the recorded
and billed model is the one that answered; a primary that fails *after* its first chunk is not
replaced; a candidate excluded by a condition is skipped without being called; every candidate
failing is a status and not a `200` with an empty body; an empty stream is an answer; the
addressing is re-read per hop; each attempt marks its own span. Then what a passed-over candidate
leaves behind — its stream closed, an iterator with no `aclose` tolerated, a close that itself
raises not costing the caller their answer — and what the caller leaves behind: **a reader who
stops closes the stream that answered them**. Through the surfaces: a withheld model replaced by
the chain with both halves on the row, the cost priced as the model that answered, a step's notice
still leading the fallback's stream, and the same chain on the predecessor's SSE contract.

`gateway/tests/test_every_candidate_is_sent_its_own_defaults.py` — FR-5a and FR-5b on both surfaces
and both forms of the chain: a fallback is sent no cap where it declares none and its own where it
does, the caller's cap reaches every hop, a fallback that cannot give that cap is passed over by
name, a primary's default thinking is no condition on its fallback while thinking the caller asked
for still is, a budget that does not fit a fallback's own cap passes it over, and the cache marker
goes only where caching is declared.

`gateway/tests/test_both_chains_choose_the_same_candidate.py` — **the comparison**, which is the
test `LESSONS.md` §7 asks for when two things are meant to be alike: twelve cases — an outage, a
withheld candidate, a region nobody permits, a chain that names the primary again, a name nothing
serves, a third candidate, everything excluded — run through *both* forms of the chain, asserting
the same choice, the same reasons and the same refusal. Two suites, one each, is how the streamed
verb came to apply no condition at all. Plus the structural half: both walk `_candidates`, and
`permits` is asked in exactly one place.

`gateway/tests/test_a_step_asks_a_chain_of_models.py` — the step chains: the second classifier
answers when the first cannot be reached; an `UNDETERMINED` verdict from a *reached* model stops
the chain, and so does a rewrite too short to trust; the router's chain; the redactor's chain; a
chain nobody can reach degrades the way one model does; a name written twice is asked once; the
bounds the gateway re-applies, and the ordinary chain it leaves alone.

`gateway/tests/test_a_redactor_reaches_an_embedding.py` — and the chain reaches the **embedding**
path, which is the branch this repository has forgotten before: with the first redactor down the
second rewrites, and what is embedded and stored is the rewrite.

`gateway/tests/test_pipeline_dryrun.py` — a step chain is approval-checked like the model in front
of it (asked of the `pii_filter`, whose fallback would be sent the caller's personal data), and a
reachable chain's trace names **which** model answered, which is what an operator testing a chain
is looking at.

`gateway/tests/test_ratelimit_routes.py` — the allowance is given back when a streamed request is
refused after it was reserved, and a ledger that cannot take it back does not turn the caller's
`503` into a `500`.

`management/backend/tests/test_pipelines.py` (`test_a_step_chain_of_released_models_saves`,
`test_a_step_chain_is_bounded_where_it_is_authored`) — every model a step's chain names is
checked against the release, and the bounds are refused where they are authored.

`tools/tests/test_both_planes_find_the_same_models_in_a_pipeline.py` — Management's
`_models_named_in` and the gateway's `models_named_in` see the same names, step chains included.

`gateway/tests/test_every_dispatch_applies_the_conditions.py` — `stream_with_fallback` is a gate,
so the streamed verbs are no longer the unconditioned ones.

**Mutations** `MF1`–`MF18` (`tools/mutation_check.py`): a failed candidate not being passed over,
a committed stream falling back anyway, an abandoned stream left open, an empty stream treated as a
failure, the conditions not asked, the chosen model not reaching the audit row on either surface, a
failed attempt not recorded, a reservation not given back, a step that ignores its chain, a step
chain that moves on after a *reached* model, a chain asked twice for one name, a step chain
unbounded, a step's chain missing from the release check, an exhausted streamed chain reported as
an outage, and an attempt marked with the wrong model.

Three of them were found by running them, and one found a defect. `MF3` survived its first guard: the double was an async
generator, which is *finished* by the exception it raises, so its `finally` ran whether or not the
chain closed anything — the assertion was about Python and not about the chain. `MF16` survived
because nothing asserted the span's model on a streamed attempt, which is the record `FRD-619`
exists for. `MF17` is the one that was not a test problem: asking whether a caller who stops
reading releases the stream found that it did not — see §5's *the wrapper owns what it wrapped*.

**Acceptance**

- *Given* a use case with `fallback_models: [b]` and a model `a` whose upstream is down, *when* a
  caller streams, *then* the answer comes from `b`, the row names `b` with `model_selection`
  saying a fallback was taken, and a `step: dispatch`, `action: failed` decision names `a` with the
  upstream's reason.
- *Given* the same use case and `b` also down, *when* a caller streams, *then* the response is a
  `502` with a reason — not a `200` whose body is `[]`.
- *Given* a `pii_filter` whose model is unreachable and whose chain names a reachable one, *when* a
  request arrives, *then* the redaction is applied by the second model and the request is served.
- *Given* a step chain naming a model the use case has not released, *when* the pipeline is saved,
  *then* Management refuses it and names the model.
