# ADR-0048 — A fallback chain per kind of request, the installation's by default

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Vadim Scheibe
- Amends `ADR-0032` §4 ("an embedding keeps no chain") and `ADR-0012` §3.

## Context

A use case could name a fallback chain in its pipeline — a node in the pipeline graph, where the
owner did not find it — and nothing else could: an installation had no chain of its own to fall back
on, so a use case nobody had configured answered every outage, quota and retired model with the
error. The owner asked for a default that can be defined, **none** as shipped (the error), set where
somebody looks for it.

And a chain had no idea of kind. Its candidates were asked whether they could read the attachment,
take the schema, think and use the tools — never whether they could write text at all. The pickers
offered every released model, so an embedding model could stand in a generation chain and a step's;
the gateway would call it for an answer it has none of, and the vendor's refusal read as an outage.
The owner's concern: *you cannot use a generation model for an embedding*, and whether an embedding
falls back at all should be a choice.

An embedding is the hard case. `ADR-0032` §4 refused it a chain because another model's vector is
no substitute: two models' vectors live in two spaces, and a search against an index built with one
returns wrong matches for the other's — with no error, at any width. A different width is worse:
the caller's index cannot take the vectors at all.

## Options considered

- **One default chain for every request.** Simple, and wrong at the first embedding: the kinds do
  not substitute for each other.
- **A default per model** (on its catalogue entry). Precise, but answers "what replaces this
  model", not the owner's "what is the default", and a hundred entries to keep rather than three.
- **The installation's chain per kind, which a use case follows, replaces or refuses.** The
  owner's choice.
- For embeddings: **any embedding model**, **the same model elsewhere only**, or **an embedding
  model of the same width**. The owner chose the last, off by default.

## Decision

1. **One chain per kind of request** — `generate` (text, and speech for a spoken answer), `embed`,
   `rerank` — kept by the installation, set on the Models page with `catalog.write`, **empty by
   default**: a model that cannot answer is then the caller's error, as before. A chain names only
   catalogued, approved models that can serve its kind (`aira_common.fallback.SERVES`), at most 16.
2. **A use case chooses**, in its pipeline (`fallback_mode`): **default** follows the installation's
   chains; **own** keeps its own chain for text generation and follows the installation's for the
   other kinds; **none** substitutes no model of any kind. A value, not a flag: three answers.
   A pipeline that says nothing means what it always meant — its own chain where it names one.
3. **A chain is not a release.** Every candidate meets every condition at every hop, the use case's
   release among them, so the installation's chain reaches only the models each use case may call.
4. **A candidate of another kind is passed over by name** at every hop and in every step: a text
   chain's candidates must declare `generate` (or `speech` for a spoken answer), an embedding's
   `embed`, a ranking's `rerank`. Management refuses such a model where it is typed — in an
   installation chain, in a use case's chain and anywhere in a pipeline, all of whose models write
   text — and the console offers only the models that can serve.
5. **An embedding falls back only to a model giving vectors of the width the model asked for would
   have given** — its declared width, or the caller's — and taking the task type and the batch as
   that model would; an undeclared width admits no stand-in. Texts that carry their task as a
   prompt go only to the model they were written for. The console says, where the chain is set,
   that two models' vectors are not comparable; the response's `X-AIRA-Model` and the row name the
   model that gave them.
6. **One walk for every kind.** Embeddings and rankings walk the chain's candidate list
   (`answer_with_fallback`) as answers and streams do, and are booked as the model that answered.

## Consequences

- Positive: an installation can set what answers when a model fails, visibly, once; a use case that
  must never be answered by another model says so.
- Positive: no request is ever sent to a model of another kind, and the defect is refused where it
  is written rather than discovered as an outage.
- Negative / trade-offs: an embedding fallback can still answer with vectors that do not match an
  index built with the model asked for — the width rule keeps the shape, not the space. Off by
  default, and said where it is switched on.
- Negative: a pipeline naming a model that cannot write text no longer saves until it is replaced —
  a change only for a configuration the gateway would have refused at every hop anyway.
- Follow-ups: a use case cannot keep an embedding or ranking chain of its own, only follow the
  installation's or refuse it; per-model defaults remain unbuilt.
