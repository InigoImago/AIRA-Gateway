# FRD-147 — Reranking, on both surfaces

> Phase: 8 (KIRA parity) · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner's comparison of the predecessor against this gateway: the
> predecessor ranks records (`POST /rerank`, model `3001`) and this gateway did not. The owner wants
> it on the Gemini side as well, under the same controls as every other model call.
> Decision: `ADR-0039`. Related: `FRD-107` (the KIRA surface), `FRD-146`, `FRD-113` (embedding, the
> nearest existing verb), `FRD-611`/`ADR-0034` (a region permitted by name), `FRD-625` (privacy).

## 1. Summary

A reranker orders records by how well each answers a query and gives each a score: the second
stage of retrieval, after a vector search has found candidates. Google offers it as the ranking API
of its Discovery Engine (`semantic-ranker-default-004` and later), not as a Gemini method. This FRD
adds reranking as a **capability of the core** — declared in the catalogue, released per use case,
metered, budgeted, audited, run through the pipeline — and two thin surfaces over it: the
predecessor's `POST /kira/api/external/rerank` and, on the native side, Google's own ranking path,
so Google's client library works against the gateway with its endpoint changed.

## 2. Goals & Non-Goals

**Goals**
- `POST /kira/api/external/rerank` exactly as the predecessor answers it.
- `POST /v1/projects/{project}/locations/{location}/rankingConfigs/{config}:rank` in Google's shape.
- One path below both: the same attribution, use-case release, approval, residency, rate limit,
  budget, pipeline and audit row as any model call. No route to a reranker that skips one.
- A reranker in the catalogue is listed on `/models` as `Reranker`.
- The demo and the tests run on a mock.

**Non-Goals**
- Reranking with a generative model ("score these with Gemini"). Possible later as another adapter;
  the surface would not change.
- Ranking configurations of the caller's own: the path's project, location and configuration name
  select nothing here (§5.3).
- Unauthenticated access of any kind, including to the model list.

## 3. User Stories
- As an **application developer** migrating from the predecessor, I want `POST /rerank` to work
  with model `3001` unchanged.
- As a **developer using Google's SDK**, I want to point `RankServiceClient` at the gateway and keep
  my code.
- As **IT Steuerung**, I want reranking under the same budgets, limits and release as everything
  else, so that it is not the unmetered corner of the gateway.

## 4. Functional Requirements

- **FR-1 Capability.** `rerank` is a capability in the catalogue. A model that does not declare it
  is refused for ranking by name; a model that declares only `rerank` is refused for chat and
  embedding by name.
- **FR-2 KIRA request.** `{"model_id": int, "query": str, "records": [{"id","title","content"}]}`;
  every field filled, as the predecessor requires, at least one record. Otherwise
  `422 VALIDATION_ERROR`. At most `AIRA_RERANK_MAX_RECORDS` (default 200) on both surfaces, refused
  before anything is spent. Google's own path takes a record with a `title` or a `content`.
- **FR-3 KIRA response.** `{"records": [{"id","score","title"|null,"content"|null}]}`, in the
  provider's order.
- **FR-4 KIRA errors.** Unknown id → `422 MODEL_NOT_FOUND`; not a reranker →
  `422 NO_RERANKER_CAPABILITIES`; upstream → `502 EXTERNAL_KI_API_ERROR`, `429` →
  `429 EXTERNAL_KI_API_TOO_MANY_REQUEST`. Every shared refusal answers as on `/chat`.
- **FR-5 Native request and response** in Google's REST shape: `model` (required here — Google's
  default is not this installation's decision), `query`, `records`, optional `topN` and
  `ignoreRecordDetailsInResponse`. Answer `{"records": [{"id","score","title"?,"content"?}]}`.
  Errors in Google's envelope, as on the Gemini surface.
- **FR-6 The controls.** Both surfaces call `prepare_for_dispatch` with a rerank request, which
  applies the use case, the rate limit, the pipeline over the query and every record, the
  declaration, the release and the reservation, in that order — and the adapter is reached through
  `resolve_direct_target`, which applies approval, release, residency and the price condition.
- **FR-6a The weight.** The rate limit takes a ranking at its units — or, where the use case's
  pipeline reads each text (`pii_filter`), at one per text it reads: the query and each record's
  title and content. A redactor calls a model per text, so 200 records are 401 model calls, and an
  embedding batch already weighs one per text for the same reason. The units are reserved at the
  input rate they are booked at.
- **FR-7 Metering.** Google bills a ranking request per **query of up to 100 records**. A request is
  booked as `ceil(records / 100)` units: that many requests against request-shaped limits, and that
  many units as its input figure, priced at the entry's input price (per million units). An entry
  with no price is unpriced, never free, and is refused where spend is capped — as every model.
- **FR-8 Residency.** The ranking API is addressed at a location like any Vertex model:
  `addressing.regions`, checked against the allow-list. Google documents `global`; a reranker at
  `global` is permitted **by name** in `AIRA_ALLOWED_MODEL_REGIONS`, never by opening `global`.
- **FR-9 Audit.** One row per request, `operation="rerank"`, the record count, and the scores'
  description — not the records, unless the use case stores payloads, and then under the same rules
  as a prompt.
- **FR-10 `/models`.** A reranker is listed with `"capabilities": ["Reranker"]`.

## 5. Design & Architecture

### 5.1 The core
- `Capability.RERANK`.
- `CanonicalRerankRequest(model, addressing, query, records, top_n)`, `RerankRecord(id, title,
  content)`; the answer `RankedRecords` (a list of `RankedRecord(id, score, title, content)`, with
  `served_region`).
- `Upstream.rerank()`; the base raises, so a dialect without one refuses by name.
- `prepare_for_dispatch(..., rerank=...)`: shape check before the rate limit (empty, too many), units
  for the gate, the pipeline over the texts (`run_pipeline_over_texts`'s sibling), the declaration
  check for `method="rerank"`, the reservation with the units as its input estimate.
- `accounting.ranked()` books the units and the description.

### 5.2 The upstream
`VertexGeminiAdapter.rerank` posts to the Discovery Engine:

```
POST https://discoveryengine.googleapis.com/v1/projects/{p}/locations/global/rankingConfigs/default_ranking_config:rank
X-Goog-User-Project: {p}
{"model": "semantic-ranker-default-004", "query": "…", "records": [{"id","title","content"}]}
```

The host follows the location: `global` → `discoveryengine.googleapis.com`, `eu`/`us` →
`{eu,us}-discoveryengine.googleapis.com`. The model name on the wire is the entry's upstream name
(`addressing.model`, `FRD-146` FR-10). The project's own identity authenticates; an API key does not
reach this API.

### 5.3 The native path
Google's client addresses its own project, location and ranking configuration. Here the gateway
calls Google with **its** project and the configured location, so those three path segments are
accepted and not used — the use case is what selects, as on every route. The `model` field selects
the catalogue entry.

## 6. Data Model
No new table. The audit row's `operation` is `rerank`; the stored request body is the request.

## 7. API / Interface Contract
As FR-2 to FR-5. `docs/API.md` lists both routes.

## 8. Security & Privacy
- Authenticated on both routes; attributed to a use case; the model released to it (`FRD-308`).
- **A new recipient**: records are sent to Google's Discovery Engine, a different Google service
  from Vertex. The privacy register and every text name it, and the edition changes.
- Records are content: stored only where the use case stores payloads, redacted by its pipeline,
  read by role.

## 9. Observability
- A model-call span `purpose="rerank"`; `aira.rerank.records` on the request span.

## 10. Testing & Acceptance Criteria
- Request and response pinned for both surfaces; the upstream body pinned against §5.2.
- Every error case of FR-4 and FR-5 has a test, and every control of FR-6 one that refuses a rerank.
- Metering: 250 records book 3 units; the price applies; unpriced is refused under a spend cap.
- Residency: `global` refused unless permitted by name.
- The demo runs on the mock.
- Mutations for each property.

## 11. Dependencies & Risks
- **Location.** Google's ranking documentation shows only `global`. Whether `eu` serves the ranking
  API is to be checked against the real service; until then the reranker is permitted at `global`
  by name.
- **Price.** Google's figure — $1 per 1 000 queries at the time of writing — is the operator's to
  enter; nothing here assumes it.

## 12. Rollout / Demo
The mock ranks: a record scores the share of the query's words it contains, so an ordering is
visible and stable, and every test above runs on it or on the Vertex adapter with Google replaced.
The showcase is not given a reranker: it serves local models through an OpenAI-compatible server,
which has no ranking API. An installation with Google configured catalogues
`semantic-ranker-default-004` under `vertex`/`google` with the location `global`, gives it id `3001`
and a price, and permits it `global` by name (`AIRA_ALLOWED_MODEL_REGIONS=global=semantic-ranker-default-004`).

## 13. As built

- **Both surfaces, one path.** `api/kira/routes.py` `rerank` and `api/ranking/routes.py` parse;
  `prepare_for_dispatch(rerank=…)` runs use case, shape, rate limit at `units`, the pipeline over
  query, titles and contents (`run_pipeline_over_rerank`), the declaration (`NotAReranker`) and the
  reservation with nothing held for output; `serving.rank` reaches the adapter through
  `resolve_direct_target` and refuses one that cannot rank (`upstreams.base.Ranks`).
- **Upstream.** `VertexGeminiAdapter.rerank` posts `ranking_mapping.rank_body` to
  `VertexTransport.ranking_url`, which checks residency for the entry's own name like every URL;
  `X-Goog-User-Project` names the project. An id Google returns that was not sent is a `502`.
- **Booked** by `Accounting.ranked`: `ceil(records / 100)` as requests and as `prompt_tokens`,
  priced at the input price; the stored answer is a count and the best and worst score.
- **`/models`** lists `Reranker`; Gemini's listing carries the verb `rank`.
- **Mock**: a record scores the share of the query's words it contains, best first.
- **Not checked against Google**: the location question of §11 stays open, and the body was pinned
  from Google's documentation, not sent.
