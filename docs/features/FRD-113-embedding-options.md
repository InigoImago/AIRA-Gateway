# FRD-113 — Embedding: task types, batches and dimensions

> Phase: 8 (KIRA parity) · Status: **Done** · Owner: Vadim Scheibe
> Origin: the predecessor's contract, programme: `ADR-0010`.
> Depends on: `FRD-114`. Touches `FRD-401`/`FRD-405` (a batch is one request and many tokens).

## 1. Problem

AIRA's embedding path is one text in, one vector out:

```python
async def embed(self, model: str, text: str) -> list[float]: ...
```

The predecessor takes a list or a single string, an **optimisation task type** from eight values,
and exposes models that differ in **vector dimensionality** (3072 and 768 for the same underlying
model). All three matter to the consumer:

- The task type materially changes retrieval quality. Indexing a corpus with `RETRIEVAL_QUERY`
  instead of `RETRIEVAL_DOCUMENT` produces vectors that work, sit in the right space, and retrieve
  measurably worse — the failure is silent, which is the worst kind.
- Batching is the difference between one HTTP round trip and a thousand when indexing a corpus.
- Dimensionality is a storage and index decision the consumer has already made in their vector
  database; we cannot decide it for them.

## 2. Goals & Non-Goals

**Goals**
- A caller supplies one text or many, a task type, and gets vectors back.
- Task types are validated against what the model declares (`FRD-114`).
- Vector dimensionality is selectable where the model supports it.
- A batch is metered honestly against rate limits and budgets (§5.3 — this is the requirement with
  a hole in it if it is got wrong).

**Non-Goals**
- **Embedding documents.** `FRD-110` adds attachments to generation, not to embedding; embedding a
  PDF means chunking it, which is a decision belonging to the consumer.
- Storing vectors. AIRA is not a vector database and returns them.
- Cross-provider dimension normalisation.

## 3. User Stories
- As an **application developer**, I want to embed a batch of 500 chunks in one call with
  `RETRIEVAL_DOCUMENT`, so that indexing is neither slow nor subtly wrong.
- As a **use-case administrator**, I want a batch of 500 to count as 500 against my limits, so that
  batching is a performance choice and not a way around the controls.

## 4. Functional Requirements

- **FR-1 Batch input.** One text or a list. A list yields results in the order submitted.
- **FR-2 Task type.** Optional, defaulting to `RETRIEVAL_QUERY` as the predecessor does. The eight
  values of `EmbeddingTaskTypeEnum`. A type the model does not declare is refused
  (`INVALID_EMBEDDING_TASK_TYPE`).
- **FR-3 Batch capability.** A model that does not declare batch support refuses a list input
  (`EMBEDDING_AGGREGATION_NOT_SUPPORTED`, keeping the predecessor's code).
- **FR-4 Dimensionality.** Where the model supports it, the caller may request an output
  dimensionality from the model's declared set; otherwise the model's default applies.
- **FR-5 Bounds.** A maximum batch length and a maximum total input size, refused with a message
  naming the bound.
- **FR-6 Metering.** A batch of *n* texts counts as **n** against request-shaped limits, not one
  (§5.3).
- **FR-6a Not every vendor embeds.** Anthropic models have no embedding endpoint. The request is
  refused by `FRD-114`'s capability declaration **before dispatch**, naming the model — never by an
  adapter raising deep in the call stack, and never by a routing decision that quietly sends an
  embedding to a model that cannot serve one.
- **FR-7 Empty input is refused.** Neither an empty string, nor an empty list, nor a list
  containing an empty string — the predecessor's rule, and it prevents a class of accidental
  no-op billing.

## 5. Design & Architecture

### 5.1 The upstream protocol changes

```python
async def embed(self, request: EmbeddingRequest) -> list[list[float]]: ...
```

with `EmbeddingRequest = {model, texts: list[str], task_type, dimensions | None}` — a single text
is a list of one, so there is one code path rather than two. Both the mock and the Gemini adapter
implement it; the Gemini adapter uses `batchEmbedContents` for lists and `embedContent` for one,
because the single-item endpoint has lower latency.

Changing a `Protocol` is a small breaking change and this is the moment to take it: doing it once,
here, is cheaper than adding a second method and living with two.

### 5.2 Task type is not a passthrough string

It is an enum, validated against the model's declared set, because the whole value of the field is
that the wrong one fails *loudly* instead of producing quietly worse vectors. A free-text
passthrough would move the failure to a provider error message at best and to retrieval quality at
worst.

### 5.3 A batch must not be a way around a rate limit

This is the requirement most likely to be implemented wrong, and it is a control bypass.

`FRD-405`'s token bucket takes **one** token per request. `FRD-401`'s budget counts requests and
tokens. If a batch of 500 texts is admitted as one request, then a caller limited to 10 requests
per minute can embed 5 000 texts per minute — the limit is intact on paper and gone in practice.
The same argument applies to a request-count budget.

So the pre-dispatch gate takes **`len(texts)` tokens from the bucket**, all-or-nothing, exactly as
`FRD-405`'s multi-scope decision is all-or-nothing: a batch that does not fit is refused with
`Retry-After` rather than partially embedded. The budget reservation likewise estimates for the
whole batch — the input token estimate scales with the submitted text, which for embeddings is
knowable up front, unlike generation.

This is also the reason FR-5's batch-length bound exists: without it, one request can demand more
than any bucket could ever hold, and every such request fails. The bound and the limit have to be
chosen together.

### 5.4 Dimensionality is a parameter here and an identity there

Worth writing down because it is a modelling trap. The predecessor lists **two model entries with
the same name** (`gemini-embedding-2`, ids 2003 and 2004) differing only in dimensions — dimension
as part of the model's identity. Google's API takes `outputDimensionality` as a request parameter.

We follow the API: one catalog entry, dimensionality a request parameter constrained by the
model's declared set (FR-4). The predecessor's two ids become two *aliases* onto one model with
different defaults, which is `FRD-107`'s problem to solve if that surface is built — and not
something to bake into the model catalog for everyone else.

## 6. Data Model

No gateway migration. `FRD-114` carries `task_types`, `supports_batch`, `dimensions` per model.
The audit row records the batch length and the task type; the texts follow the existing
`store_payloads` rules.

## 7. API / Interface Contract

Gemini surface gains `batchEmbedContents` alongside `embedContent`, matching Google:

```json
POST /v1beta/models/{model}:batchEmbedContents
{ "requests": [ { "content": {"parts": [{"text": "…"}]},
                  "taskType": "RETRIEVAL_DOCUMENT",
                  "outputDimensionality": 768 } ] }
→ { "embeddings": [ { "values": [0.12, -0.34, …] } ] }
```

Errors: `INVALID_EMBEDDING_TASK_TYPE`, `EMBEDDING_AGGREGATION_NOT_SUPPORTED`, empty input, bound
exceeded — `400`/`422` shaped per surface, plus `429` from FR-6 with `Retry-After`.

## 8. Security & Privacy

Unchanged in kind: embedding inputs are prompts and follow the same storage, retention and
redaction rules. FR-6 is the security-relevant requirement here — it closes a limit bypass.

## 9. Observability

`aira.embedding.batch_size`, `aira.embedding.task_type`, `aira.embedding.dimensions` on the span,
and batch size on the audit row.

**The three span attributes were built on 2026-08-31**, in `prepare_for_dispatch`; the batch size
has been on the row since the verb shipped. `task_type` and `dimensions` are absent where the
caller named neither — an unset option is not the same fact as a default one, and a span that says
`dimensions = 0` for a request that asked for nothing invents a figure.

## 10. Testing & Acceptance Criteria

- **Unit** — a single text and a list both work; order is preserved; an undeclared task type is
  refused; a model without batch support refuses a list; empty inputs refused in all three forms;
  bounds enforced at their boundary; dimensionality outside the declared set refused.
- **Unit (the bypass)** — a batch of *n* takes *n* tokens from the bucket, and a caller with 5
  tokens left is **refused** a batch of 10 rather than admitted. Written to fail first against a
  one-token-per-request implementation; this is the pair that proves the control.
- **Integration** — a real batch returns the right number of vectors of the right dimensionality,
  and two task types produce different vectors for the same text.
- **Mutation** — the metering takes `len(texts)` and not `1`; the task-type validation actually
  restricts.

**Acceptance**
- *Given* a use-case rate limit of 10 per minute, *when* a caller submits a batch of 50, *then* it
  is refused with 429 and `Retry-After`, and no upstream call was made.
- *Given* a model declaring 768 and 3072, *when* a caller requests 768, *then* the returned vectors
  have 768 components.

## 10a. What was built (2026-08-06)

The `Upstream.embed` protocol now takes a request and returns one vector per text; a single text is
a list of one, so there is one code path. The Gemini surface gains `batchEmbedContents`; the KIRA
surface accepts a list.

**§5.3, the control bypass, is the requirement with teeth** and it is implemented as a `cost` on the
token bucket rather than as a loop: `take(buckets, n)` debits n from every applicable bucket or from
none, in the same Lua pass that was already all-or-nothing across scopes. The budget reservation and
settlement count n requests likewise. Its test is written to fail against the one-token version.

The two bounds and the configured limits interact, so a batch larger than a bucket's *capacity* is
refused with a message naming which of the two said no — rather than a `Retry-After` that would
still be wrong an hour later (§11's risk, closed).

One correction to this FRD's plan, found by a test: **the predecessor's default task type is a
surface's, not the validator's.** Filling it in unconditionally refuses every embedding against a
model nobody has declared task types for, and starting to send `RETRIEVAL_QUERY` where AIRA has
always sent nothing would move every existing consumer's vectors. So `validate` takes
`default_task_type`, applies it only where the model declares it, and an *explicit* undeclared type
is still refused. The asymmetry is deliberate: naming a type we cannot verify is a request, naming
none is not.

§11's open question is unresolved and is now **visible in the wire format**: a list answers under
`vectors` on the KIRA surface, since the documented singular `vector` cannot express n of them.
Returning only the first would be silent data loss; a distinct key makes the assumption checkable
by whoever confirms it against the running predecessor.

Mutations **E1–E8**.

## 11. Dependencies & Risks

- **`FRD-114`** for `task_types`, `supports_batch` and `dimensions`.
- **Open — the predecessor's aggregation semantics.** The contract names the flag
  `supports_aggregation` and shows a **singular** `{"vector": [...]}` response for an input that
  may be a list. Two readings: a list yields one vector per text (ordinary batching), or a list is
  combined into a single vector. This FRD assumes the first, which is what the provider API offers
  and what consumers of a chunk-indexing flow need. **Confirm against the running predecessor
  before `FRD-107` maps this surface** — if it is really the second, that is a gateway-side
  reduction and a separate, small requirement.
- **Risk** — FR-5's bound and the configured rate limits interact (§5.3). Choosing a batch bound
  larger than any bucket makes large batches permanently fail; the default must be chosen with the
  default limits in view, and the failure message must say which of the two refused.

## 12. Rollout / Demo

The mock returns deterministic vectors whose values depend on the text, the task type and the
requested dimensionality — so batching, task types and dimensions are all demonstrable, and a test
can prove two task types differ without a cloud call.
