# FRD-310 — A fallback chain per kind of request, kept by its use case

> Phase: 3 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner, in daily use: there is no fallback model that can be defined as the default —
> at least none visible in the console; the default should be none, returning the error, and it
> should be settable for when things go wrong. And whether an embedding (or the like) falls back
> should be settable, because a generation model cannot be used for an embedding.
> **Revised** by the owner: the first build kept the chains for the whole installation, on the
> Models page; what had been meant was **per use case, in the pipeline** — where none is set, a
> model that cannot answer goes straight to the error. §13 says what changed.
> Decisions: `ADR-0048` (kinds), `ADR-0052` (the chain is the use case's). Related: `FRD-302` (the
> chain), `ADR-0032` (every model access is a chain), `FRD-308` (the release every hop is held to),
> `FRD-113` (embedding options), `FRD-147` (ranking).

## 1. Summary

A use case keeps **one fallback chain per kind of request** — text generation, embedding, ranking
— in its pipeline, each empty by default: a model that cannot answer is then the caller's error and
nothing else is called. The pipeline builder shows the three chains in a node of their own and sets
them from the models released to the use case. Every candidate is held to what the request needs of
it — and first of all to its **kind**: no request is ever sent to a model of another kind, and an
embedding goes only to a model giving vectors of the width the caller's index takes.

## 2. Goals & Non-Goals

**Goals**
- A fallback a use case's administrators can set and see, per kind, in its pipeline, with "none —
  the error" as shipped.
- A chain never reaches a model of another kind, nor one the use case was not released.
- Embedding and ranking get chains, held to their own rules; their default is none.
- An installation that set chains under the first build loses none of them on the upgrade.

**Non-Goals**
- A chain for the whole installation (`ADR-0052`).
- A default per model (on a catalogue entry).
- Making two embedding models' vectors comparable. The width rule keeps an index's shape; nothing
  can make another model's space the same.

## 3. User Stories

- As an **administrator of a use case**, I want to name the models that answer when a model cannot,
  per kind of request, so that an outage or a retired model does not stop my use case — and to name
  none, so that it is answered only by the model it asked for.
- As a **developer** embedding into an index, I want no other model's vectors silently mixed into
  it, and to be told which model answered.
- As a **Global Administrator**, I want no use case answered by a model its administrators did not
  choose.

## 4. Functional Requirements

- **FR-1 A use case's chains.** The pipeline carries `fallback_models` (text generation, speech
  included), `embedding_fallback_models` and `ranking_fallback_models` (`aira_common.fallback.
  CHAIN_FIELDS`). Each names at most 16 models, none twice, every one released to the use case;
  an empty one is no fallback. A save that leaves a chain out keeps what it held; an event that
  leaves one out means none.
- **FR-2 Kind where it is typed.** Management refuses, by name and under its field, a model that
  cannot serve the chain it is put in — `fallback_models` and every model the steps name must write
  text (or speak), `embedding_fallback_models` must embed, `ranking_fallback_models` must rank; an
  undeclared model counts as generation and embedding (`FRD-114` FR-7).
- **FR-3 What a request meets.** A request of a kind falls back, after routing, along its use case's
  chain for that kind (`Pipeline.chain_for`); a use case with no pipeline has none. The dry run
  reports the text chain.
- **FR-4 Kind at every hop.** A text request's candidates must declare text generation (speech for
  a spoken answer); a pipeline step's model too, since every step asks for text; an embedding's
  must embed; a ranking's must rank on an adapter that can. Another kind is passed over by name,
  never called.
- **FR-5 Embeddings.** A candidate other than the model asked for must give vectors of the width
  that model would have given — its declared default, or the caller's — and take the task type and
  the batch as declared; with no declared width there is no stand-in. Texts that carry their task as
  a prompt go only to the model they were written for. The answer is booked, recorded and named
  (`X-AIRA-Model`) as the model that gave it.
- **FR-6 Rankings.** The chain's candidates must be rerankers; the ranking is booked as the one that
  answered.
- **FR-7 The release.** Every candidate of every chain meets the use case's release, approval,
  residency and price condition at its hop (`FRD-308`): a chain is not a release.
- **FR-8 The console.** The pipeline graph ends in a node *When a model cannot answer* with a line
  per kind — its models in order, or *the error*. Its inspector offers, per kind, only the released
  models that can serve it; marks on its chip a stored model that is not released or cannot serve
  the kind; says under an empty chain that a failure is the caller's error; and says beside the
  embedding chain that two models' vectors are not comparable. A reader who may not change the
  pipeline sees the same pickers, greyed. Every other model picker in the pipeline offers only
  models that write text.
- **FR-9 The upgrade.** The installation's chains of the first build are written into every live
  use case that followed them — under `default` every kind, under `own` embedding and ranking — as
  far as the use case was released each model and the model can serve the kind, and each pipeline
  changed is announced. Nothing else changes; an installation that set none sees no difference.

## 5. Design

```
use case pipeline (fallback_models, embedding_fallback_models, ranking_fallback_models)
        ──pipeline.upserted──▶ gateway pipeline_configs
                                   │
request ─▶ prepare ─▶ pipeline.chain_for(kind) ─▶ candidates
                                   │
            every hop: installation conditions + kind + what the request needs
```

**One walk.** Answers, streams, embeddings and rankings walk the same candidate list
(`answer_with_fallback`, `_candidates`), each with its verb as a function that marks its own call
(`FRD-619`).

**The width.** Prepare validates an embedding against the model asked for, which fixes its
dimensions to that model's default where the caller set none. A stand-in is then validated against
its own declaration with that width asked for — so it must declare it — and the same task type and
batch. An addressing fault on every candidate keeps the model's own refusal (`MODEL_NOT_FOUND` on
the KIRA surface), as the verbs answered it before they had a chain.

**Two versions at once.** The gateway's `fallback_chains` table and `pipeline_configs.fallback_mode`
are read by no code of this release and stay for one: the release before it reads them on every
request and serves beside this one during a rolling update (`FRD-127` §5.4). The consumer writes the
mode empty, which that release reads as a pipeline from before modes — its own chain where it names
one. A `fallback_chain.upserted` still held by the compacted topic is ignored, knowingly.

## 6. Data Model

- Management: `PipelineConfig.embedding_fallback_models` and `ranking_fallback_models` (JSON lists);
  `fallback_mode` and `catalog.FallbackChain` removed — migrations `pipelines/0005` (with the
  carry-over of FR-9) and `catalog/0014`.
- Gateway: `pipeline_configs.embedding_fallback_models` and `ranking_fallback_models`, nullable,
  NULL read as none — migration `0059`. `fallback_chains` and `pipeline_configs.fallback_mode` are
  dropped by the next release.
- Neither holds anything about a person (`FRD-625` register).

## 7. API / Interface Contract

See `docs/API.md`: the pipeline's three chains on `GET`/`PUT /api/v1/use-cases/{slug}/pipeline/`
and on `pipeline.upserted`. `/api/v1/fallback-chains/` and `fallback_chain.upserted` are gone.

## 8. Security & Privacy

A chain sends a request only to models the use case was released, which the privacy notice already
names as its recipients. Setting one is the use case's administration — the permission that already
decides its pipeline and its release.

## 9. Observability

The row's `model_selection` (`fallback:<n>`) and its `step: dispatch` decisions say which candidate
answered and why the others did not, for every kind; each attempt leaves its own model-access span.

## 10. Testing & Acceptance Criteria

- `gateway/tests/test_a_fallback_chain_per_kind.py` — the use case's chain on both verbs; none set
  is the error, a chain of another kind included; the table the previous release wrote read by
  nothing; a chain held to the release; another kind passed over by name; steps; embeddings by width,
  by kind and with no width, on both surfaces; rankings; booked as the model that answered; the
  consumer, the store, the retired event; the bounds.
- `management/backend/tests/test_fallback_chains.py` — the API, the kind refusal per chain and in the
  steps, the bounds, who may set it, the removed installation API, and the upgrade run backwards and
  forwards on the database.
- `pipeline-editor.spec.ts` — the graph node, the pickers per kind through real clicks, the marks,
  the warnings, read-only, an unknown release and catalogue, an older server's answer.
- `tools/tests/test_the_console_reads_the_fallback_rule.py` — the console's rule and fields are the
  server's; `test_both_planes_find_the_same_models_in_a_pipeline.py` — both planes' release checks
  see every chain.
- Mutations `FB-1`–`FB-42`.

**Acceptance**

- *Given* a use case with no chain, *when* its model fails, *then* the caller gets the error and
  nothing else is called.
- *Given* the text chain `[backup-1]` in a use case's pipeline, *when* its model fails, *then*
  `backup-1` answers and the row says `fallback:1`.
- *Given* an embedding chain naming a text model and a model of another width, *when* the embedding
  model fails, *then* neither is called and the row names both with their reasons.
- *Given* an installation chain set under the first build and a use case that followed it, *when*
  the upgrade runs, *then* the use case's pipeline holds that chain, as far as it was released, and
  is announced.

## 11. Dependencies & Risks

- An embedding stand-in of the same width still answers in another model's space; the console says
  so where the chain is set, and the default is none.
- A pipeline that names a model of the wrong kind will not save until it is changed.
- A model an installation wants every use case to fall back to has to be set in each.

## 12. Rollout / Demo

Images only. The migrations run in the migrate containers; a use case keeps the fallback it had. The
next release drops the two gateway columns this one no longer reads.

## 13. Revision — 2026-11-03

The first build (2026-10-28) kept **one chain per kind for the installation**, set on the Models
page with `catalog.write`, and gave each use case a mode: `default` followed the installation's
chains, `own` kept its own for text generation, `none` refused fallback. The owner meant something
narrower and said so: the chains belong to the use case, in its pipeline, and an empty one is the
error. The installation's chains, the mode, the Models page card, `/api/v1/fallback-chains/` and
`fallback_chain.upserted` are gone; the use case gained chains for embedding and ranking, which it
could before only follow from the installation. What `ADR-0048` decided about kind, width, release
and the one walk is unchanged.
