# ADR-0032 — Every model access is a chain, and a failed attempt is on the record

> Date: 2026-10-11 · Status: **Accepted** · Supersedes: nothing · Amends: `ADR-0012` §3

## Context

`ADR-0012` §3 gave a use case an ordered `fallback_models` chain and one rule for it: a chain must
not degrade a request silently. `dispatch_with_fallback` implements that — it tries the candidates
in order, asks every dispatch condition per hop, and reports which candidate answered.

Three places reach a model **without** that chain, and each was deliberate at the time:

1. **A stream.** `:streamGenerateContent` and KIRA's `/streaming-chat` resolve exactly one adapter
   through `resolve_direct_target`, because the chain returns a finished response and a stream
   cannot be assembled from one. The reason given was that a stream cannot fall back once its
   first chunk is out — which is true, and was taken to mean a stream cannot fall back at all.
2. **A pipeline step.** The injection filter in `llm` mode, the router and the PII redactor each
   call the one model their configuration names. A step whose model cannot be reached degrades in
   its own safe direction: the filter to the heuristic, the router to its `default_model`, the
   redactor to a block.
3. **An embedding.** There is nothing to fall back *to*: another model's vector is not a
   substitute for the one that was asked for, and a batch half-filled from two models is worse
   than a failure.

What (1) and (2) cost in practice is availability, and it is not symmetric with what the
non-streamed path gets. An interactive caller streams; a model that is rate-limited or out for
ten minutes takes every streamed request of that use case with it, while the same request with
`:generateContent` is served by the second candidate. And a `pii_filter` whose single redactor is
unreachable blocks every request of its use case, by design and by default — the chain exists
precisely for the outage that does not care which model a configuration happens to name.

A fourth thing was missing on the recorded side: a candidate **tried and failed** left no trace.
`passed_over` records the candidates a *condition* excluded, and `selection` says a fallback
answered — so the audit trail could say *that* a substitution happened and never *why*.

## Decision

**1. A stream falls back for as long as nothing has reached the caller.**

The chain is opened rather than awaited: for each candidate in turn, the gateway asks the
conditions, opens the upstream stream and pulls its **first chunk**, which is the `__anext__` that
issues the HTTP request. A candidate that fails before that chunk exists is passed over and the
next one is tried. The chunk already in hand is then handed to the surface, which yields it and
keeps iterating.

Two consequences follow, and both are improvements we are taking deliberately:

- the HTTP response does not begin until a model has produced something, so **a stream that no
  candidate can serve is a status** (`502`, or the chain's own refusal) rather than a `200` whose
  body is an empty array;
- the model that answers is the one recorded, billed and reported, which for a fallback is not the
  model the caller named.

**Once a chunk is on the wire the stream is committed.** A later failure ends the stream and is
recorded, as today. This is not a limitation we can lift: the caller already has text from one
model, and continuing it from another would silently splice two answers.

**An upstream that answers with zero chunks has answered.** No fallback: a stream with a finish
reason and no text is how a vendor's own safety filter refuses, and retrying that on three models
spends three times to be refused three times.

**2. A pipeline step reaches a chain, not a model.**

Every step that asks a model takes an optional `fallback_models` in its own configuration, tried
in order, and the first candidate that is **reached** answers. Reached, not agreeable: a model
that answered `UNDETERMINED`, or returned a rewrite too short to trust, has answered — its verdict
stands and the chain stops. Only a candidate that cannot be resolved or whose call fails is passed
over. Otherwise a chain would shop for the verdict the operator wanted, which is the opposite of
what a filter is for.

When no candidate can be reached the step degrades exactly as it does today. The chain adds a
second chance; it removes no safe direction.

**Every model a step's chain names is validated against the use case's release**, in the same
place and by the same rule as the model it names first (`FRD-308`). This is load-bearing: the
reason a step's own model call applies no dispatch condition is that an administrator wrote the
name and the serializer checked it. A fallback name nobody checked would be the hole that
`allow_check`'s removal closed, reopened one field along.

**3. A candidate that was tried and failed is recorded**, with the upstream's own reason, beside
the ones a condition excluded. Both are `step: dispatch` decisions on the audit row; the action
says which happened — `skipped` for a condition, `failed` for an attempt.

**4. An embedding keeps no chain.** Unchanged, and for the reason above.

## Consequences

- Positive: a use case's chain now covers the traffic that actually streams, which is most
  interactive traffic. The availability `ADR-0012` §3 promised is no longer conditional on which
  verb a caller used.
- Positive: a streamed request that nothing can serve answers with a status and a reason instead
  of an empty success. That is a behaviour change on an error path, and the previous behaviour was
  indefensible: `200` plus `[]` reads as "the model had nothing to say".
- Positive: the audit trail can answer *why* a fallback answered.
- Negative: **the response headers of a stream now wait for the first chunk.** Time to first byte
  is unchanged in substance — a caller waits for the model either way — but a client that treats
  the arrival of headers as "the gateway is alive" sees that signal later, and a slow model now
  looks like a slow response rather than a stalled stream. Accepted: the alternative is keeping
  the status code useless on exactly the requests where it matters.
- Negative: a per-step chain can cost more than one classifier call for one request. Each attempt
  is audited and billed as `pipeline:<step>` like any other (`FRD-125b`), so the spend is visible;
  what it cannot do is exceed the chain's length.
- Negative: two chains with one name. `fallback_models` on the pipeline is the chain that serves
  the caller; `fallback_models` inside a step is that step's own. The console labels them
  separately and `FRD-302` says it in one place, but the JSON looks alike.
- Follow-up: a stream still cannot fall back after its first chunk, and nothing retries the *same*
  model. Both are stated in `FRD-302` §6 rather than left to be rediscovered.
