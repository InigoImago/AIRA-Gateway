# FRD-310 — A fallback chain per kind of request, the installation's by default

> Phase: 3 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner, in daily use: there is no fallback model that can be defined as the default —
> at least none visible in the console; the default should be none, returning the error, and it
> should be settable for when things go wrong. And whether an embedding (or the like) falls back
> should be settable, because a generation model cannot be used for an embedding.
> Decision: `ADR-0048`. Related: `FRD-302` (the chain), `ADR-0032` (every model access is a chain),
> `FRD-308` (the release every hop is held to), `FRD-113` (embedding options), `FRD-147` (ranking).

## 1. Summary

An installation keeps **one fallback chain per kind of request** — text generation, embedding,
ranking — on the console's Models page, empty by default. A use case **follows** those chains,
keeps **its own** for text generation, or has **none**. Every candidate is held to what the
request needs of it — and first of all to its **kind**: no request is ever sent to a model of
another kind, and an embedding goes only to a model giving vectors of the width the caller's index
takes.

## 2. Goals & Non-Goals

**Goals**
- A default fallback an administrator can set and see, per kind, with "none — the error" as shipped.
- A use case can follow it, replace it for text generation, or refuse fallback altogether.
- A chain never reaches a model of another kind, nor one the use case was not released.
- Embedding and ranking get chains, held to their own rules; their default is none.

**Non-Goals**
- A use case's own chain for embeddings or rankings: a use case follows the installation's there,
  or refuses it.
- A default per model (on a catalogue entry).
- Making two embedding models' vectors comparable. The width rule keeps an index's shape; nothing
  can make another model's space the same.

## 3. User Stories

- As a **Global Administrator**, I want to name the models that answer when a model cannot, per
  kind of request, so that an outage or a retired model does not stop every use case.
- As an **administrator of a use case**, I want to keep my own chain, or refuse any fallback, so
  that my use case is answered only by models I chose.
- As a **developer** embedding into an index, I want no other model's vectors silently mixed into
  it, and to be told which model answered.

## 4. Functional Requirements

- **FR-1 The installation's chains.** `GET /api/v1/fallback-chains/` answers every kind's chain to
  anyone signed in; `PUT /api/v1/fallback-chains/{kind}/` replaces one with `catalog.write`. A
  chain names at most 16 catalogued, approved models, none twice, each able to serve the kind:
  `generate` takes a model declaring text generation or speech, `embed` one declaring embedding,
  `rerank` a reranker — an undeclared model counts as generation and embedding (`FRD-114` FR-7).
  Each change is announced as `fallback_chain.upserted`; a model removed from the catalogue leaves
  every chain, and the changed chains are announced.
- **FR-2 A use case's choice.** The pipeline carries `fallback_mode`: `default` (the installation's
  chains), `own` (`fallback_models` for text generation, the installation's for the other kinds) or
  `none`. `own` needs a chain; any other mode takes none. A save or an event without a mode means
  `own` where it names a chain and `default` otherwise; a word the gateway does not know is `none`.
  The migration keeps every pipeline doing what it did.
- **FR-3 What a request meets.** A request of a kind falls back along the chain its use case's mode
  resolves to (`aira_common.fallback.chain_for`), after routing; the pipeline answer tells the
  console that chain per kind (`fallbacks`), and the dry run reports it.
- **FR-4 Kind at every hop.** A text request's candidates must declare text generation (speech for
  a spoken answer); a pipeline step's model too, since every step asks for text; an embedding's
  must embed; a ranking's must rank on an adapter that can. Another kind is passed over by name,
  never called. Management refuses, by name, a pipeline naming any model that cannot write text.
- **FR-5 Embeddings.** A candidate other than the model asked for must give vectors of the width
  that model would have given — its declared default, or the caller's — and take the task type and
  the batch as declared; with no declared width there is no stand-in. Texts that carry their task as
  a prompt go only to the model they were written for. The answer is booked, recorded and named
  (`X-AIRA-Model`) as the model that gave it.
- **FR-6 Rankings.** The chain's candidates must be rerankers; the ranking is booked as the one that
  answered.
- **FR-7 The release.** Every candidate of every chain meets the use case's release, approval,
  residency and price condition at its hop (`FRD-308`): a chain is not a release.
- **FR-8 The console.** The Models page shows the chains, offers per kind only the models that can
  serve it, marks a stored one that no longer can, and says beside the embedding chain that two
  models' vectors are not comparable. The pipeline editor offers the three modes, shows what each
  kind of the use case's requests then meets — marking a model the use case was not released — and
  offers, for a chain and for every step, only models that write text.

## 5. Design

```
installation chains (Models page) ──fallback_chain.upserted──▶ gateway fallback_chains
use case pipeline (fallback_mode, fallback_models) ──pipeline.upserted──▶ pipeline_configs
                                   │
request ─▶ prepare ─▶ chain_for(kind, mode, own, installation) ─▶ candidates
                                   │
            every hop: installation conditions + kind + what the request needs
```

**One walk.** `_candidates` was the walk the answer and the stream shared; embeddings and rankings
walk it too now (`answer_with_fallback`), each with its verb as a function that marks its own call
(`FRD-619`). The function that used to give those two verbs their conditions without a chain is
gone, and the structural guards name the new gate.

**The width.** Prepare validates an embedding against the model asked for, which fixes its
dimensions to that model's default where the caller set none. A stand-in is then validated against
its own declaration with that width asked for — so it must declare it — and the same task type and
batch. An addressing fault on every candidate keeps the model's own refusal (`MODEL_NOT_FOUND` on
the KIRA surface), as the verbs answered it before they had a chain.

## 6. Data Model

- Management: `catalog.FallbackChain(kind unique, chain JSON)` — migration `catalog/0013`;
  `PipelineConfig.fallback_mode` — migration `pipelines/0004`, which sets `own` where a chain is
  named.
- Gateway: `fallback_chains(kind, models)` and `pipeline_configs.fallback_mode` — migration `0056`.
- Neither holds anything about a person (`FRD-625` register).

## 7. API / Interface Contract

See `docs/API.md`: `GET/PUT /api/v1/fallback-chains/`, the pipeline's `fallback_mode` and
`fallbacks`, and the events `fallback_chain.upserted` and `pipeline.upserted`.

## 8. Security & Privacy

A chain sends a request only to models the use case was released, which the privacy notice already
names as its recipients. Setting a chain is `catalog.write`, the permission that decides what every
use case may call.

## 9. Observability

The row's `model_selection` (`fallback:<n>`) and its `step: dispatch` decisions say which candidate
answered and why the others did not, for every kind; each attempt leaves its own model-access span.

## 10. Testing & Acceptance Criteria

- `gateway/tests/test_a_fallback_chain_per_kind.py` — the installation's chain on both verbs; none
  set is the error; `none` and `own`; a chain held to the release; another kind passed over by name;
  steps; embeddings by width, by kind and with no width; KIRA's embedding; rankings; booked as the
  model that answered; the consumer's bounds; how a stored mode is read.
- `management/backend/tests/test_fallback_chains.py` — the API, its permission and refusals, the
  cleanup on delete, the pipeline's modes, the kind refusal anywhere in a pipeline, the migration.
- `fallback-chains-card.spec.ts`, `pipeline-editor.spec.ts` — the card and the editor's modes.
- `tools/tests/test_the_console_reads_the_fallback_rule.py` — the console's rule is the server's.
- Mutations `FB-1`–`FB-28` and `FB-30`–`FB-37`; seven re-anchored onto the chain.

**Acceptance**

- *Given* no chain set, *when* a model fails, *then* the caller gets the error and nothing else is
  called.
- *Given* the text chain `[backup-1]` released to a use case that follows it, *when* its model fails,
  *then* `backup-1` answers and the row says `fallback:1`.
- *Given* an embedding chain naming a text model and a model of another width, *when* the embedding
  model fails, *then* neither is called and the row names both with their reasons.

## 11. Dependencies & Risks

- An embedding stand-in of the same width still answers in another model's space; the console says
  so, and the default is none.
- A pipeline that named an embedding model or a reranker anywhere will not save until it is changed.

## 12. Rollout / Demo

Images only. No chain is set after the upgrade, and every pipeline keeps the mode its chain implied,
so nothing falls back that did not before.
