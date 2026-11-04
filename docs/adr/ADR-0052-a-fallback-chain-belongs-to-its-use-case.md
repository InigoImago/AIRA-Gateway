# ADR-0052 — A fallback chain belongs to its use case, one per kind of request

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** Vadim Scheibe
- Supersedes `ADR-0048` decisions 1 and 2 (the installation's chains and a use case's mode); keeps
  its decisions 3 to 6.

## Context

`ADR-0048` answered the owner's report — *there is no fallback model that can be defined, at least
none visible in the console; the default should be none, returning the error* — with a chain per
kind of request kept by the **installation**, on the Models page, which a use case followed, replaced
for text generation, or refused. The owner then said what had been meant: the fallback models are
set **per use case, in its pipeline**; where none is set, a model that cannot answer goes straight to
the error. An installation-wide chain decides for every use case at once which other model answers
its requests, and is set by whoever writes the catalogue rather than by the people who answer for
the use case.

The parts of `ADR-0048` about **kind** were not in question: a request is never sent to a model of
another kind, an embedding falls back only to a model of the same width, every hop is held to the
use case's release, and all kinds walk one chain of candidates. The owner had also asked that
whether an embedding falls back be a choice — which, with no installation chain, can only be the
use case's.

## Options considered

- **Keep the installation's chains and add a visible per-use-case setting.** Two places answer one
  question, and the mode that chose between them (`default`, `own`, `none`) is what the owner did
  not want: a use case that says nothing would still be answered by models chosen elsewhere.
- **One chain per use case, for text generation only**, as before `ADR-0048`. Simple, and drops the
  embedding and ranking fallback the owner had asked to be able to set.
- **One chain per kind of request, per use case** — text generation, embedding, ranking — each empty
  by default. Chosen: it is the owner's "per use case, in the pipeline", and keeps every rule of kind
  `ADR-0048` introduced.

## Decision

1. **A use case keeps a chain for each kind of request in its pipeline** — `fallback_models` (text
   generation, speech included), `embedding_fallback_models`, `ranking_fallback_models` — set by its
   administrators in the pipeline builder, from the models released to it that can serve the kind.
   **Empty is the default and means no fallback**: a model that cannot answer is the caller's error,
   and nothing else is called. There is no mode: a chain is the whole answer.
2. **The installation keeps no chain.** The Models page's chains, their API and the
   `fallback_chain.upserted` event are gone. A gateway ignores that event where a compacted topic
   still holds one.
3. **The upgrade keeps every use case doing what it did.** Management's migration writes each chain
   an installation had set into every live use case that followed it — under `default` every kind,
   under `own` embedding and ranking — as far as the use case was released the model and the model
   can serve the kind, and announces each pipeline it changed. An installation that set none — the
   default — changes nothing.
4. **Two releases for what an older gateway reads.** The gateway's `fallback_chains` table and
   `pipeline_configs.fallback_mode` stay, read by nothing and the mode written empty, so the
   previous release can still serve beside this one during a rolling update (`FRD-127` §5.4). The
   next release drops both.
5. `ADR-0048` decisions 3 to 6 stand unchanged: a chain is not a release; a candidate of another
   kind is passed over by name; an embedding falls back only at the same width, task type and batch;
   every kind walks one chain.

## Consequences

- Positive: the fallback is decided where the use case is configured, by the people who answer for
  it, and is visible there — a node in the pipeline graph says per kind what a failure meets.
- Positive: an installation can no longer make a use case answer with a model its administrators
  did not choose.
- Negative / trade-offs: a model an installation wants every use case to fall back to has to be set
  in each; the release a use case already needs is where that list comes from.
- Negative: an embedding chain is per use case now, which is also where its warning — two models'
  vectors are not comparable — has to be read.
- Follow-ups: the next release drops `fallback_chains` and `pipeline_configs.fallback_mode` from the
  gateway's schema.
