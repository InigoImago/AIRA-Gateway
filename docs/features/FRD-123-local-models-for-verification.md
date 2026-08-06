# FRD-123 — A real model in the stack (Ollama, over the OpenAI dialect)

> Phase: 8 (KIRA parity) · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Depends on: `ADR-0011` (transport × dialect), `FRD-114` (declarations). Unblocks part of `FRD-120`.

## 1. Problem

Every verification layer in this project was added because the one below it structurally could not
see something, and each one immediately found a defect: the integration layer found a cancelled
task losing a settle, the browser layer found an nginx pin, the mutation harness found a refusal
naming the wrong model.

There is one layer still missing, and it is the largest. **The request path has never met a model
that disagrees with us.** The mock is deterministic, it reports the token counts we tell it to, it
truncates when we say so, and it produces documents that match the schema because the same person
wrote the schema handling and the generator. A green suite against it proves the gateway is
self-consistent, which is exactly the failure mode `tools/mutation_check.py` exists to warn about,
one level up.

Two open questions are the concrete cost of that gap, both recorded when they were deferred:

- **`FRD-111` FR-6** — do thinking tokens arrive inside the reported output count, or separately?
  We assumed the former. If it is the latter, every recorded cost for a thinking request is
  understated, and no hermetic test can tell.
- **`FRD-112` FR-6** — a provider that honours a schema only approximately is a real path we have
  only ever simulated.

And a third that nobody wrote down because it seemed too obvious to check: **are the prompts and
responses actually stored, and is what is stored what was sent?** `FRD-103` says yes and the tests
agree, but they agree about a payload the mock also produced.

## 2. Goals & Non-Goals

**Goals**
- A real model, running locally, reachable from the gateway and from CI, with no cloud credential.
- The **whole governed path** exercised against it: attribution, roles, rate limits, budgets, cost
  accounting, the pre-dispatch pipeline, the audit row **including the stored payloads**.
- The **OpenAI wire dialect**, because `FRD-120` needs it regardless (`ADR-0011`).
- `Hosting.SELF_DEPLOYED` finally *exercised* rather than merely declared (`ADR-0012` §5).

**Non-Goals**
- **Answer quality.** A 0.6b model answers badly. Nothing here may assert on the content of a
  completion; see §5.4, because getting this wrong produces a flake generator rather than a test.
- **A supported production upstream.** It is registered behind a Compose profile and a setting, and
  it is not part of the reference deployment. That said, self-hosted models are a real story
  (`ADR-0012` names Nemotron on Model Garden's self-deploy side), so what is built here is a
  genuine adapter, not a stub.
- Replacing the mock. The mock stays: it is what makes the hermetic suite hermetic and fast.

## 3. User Stories
- As the **owner**, I want to see a request go in and find the prompt, the response, the cost and
  the calling role in the database afterwards — because that is the product, and a screenshot of it
  is worth more than a passing test.
- As a **developer**, I want `make up` to stay fast and `make verify-local` to bring up a real
  model when I want one.
- As **whoever writes `FRD-120`**, I want the OpenAI dialect already built and tested.

## 4. Functional Requirements

- **FR-1 An OpenAI-dialect upstream.** `upstreams/openai/` — request/response/stream mapping for
  `/v1/chat/completions` and `/v1/embeddings`, pure functions, no I/O, in the same shape as
  `gemini_mapping.py` and `anthropic_mapping.py`.
- **FR-2 An Ollama transport.** Base URL, no credential, no region by default. Registered only
  when `AIRA_OLLAMA_URL` is set, exactly as the Vertex and Gemini adapters are.
- **FR-3 Declared like any other model.** Capabilities, prices, output caps and (where the dialect
  supports them) thinking and embedding blocks come from the catalog over Kafka. **No special
  case**: if a local model needs code outside `upstreams/`, the canonical core is less
  provider-agnostic than claimed and that is worth finding out here.
- **FR-4 `hosting: self_deployed`.** A model that is not loaded takes seconds to first token, and
  its 429 means *no capacity*, not *quota*. The readiness probe must **not** wake a cold model.
- **FR-5 Invented prices are declared as invented.** A local model costs no money, so any price in
  the catalog is a **test fixture**. It is what makes `FRD-403` end-to-end demonstrable, and the
  seed says so in the display name so that no report is ever built on it by accident.
- **FR-6 What the dialect cannot carry is refused, not approximated.** The same rule as everywhere:
  if Ollama's OpenAI surface does not expose thinking or a response schema, a request asking for
  one against a local model is refused by capability (`FRD-114`), never silently served without it.
- **FR-7 Skips honestly.** Every test that needs the model skips with a reason when it is not
  running. A suite that silently passes when the thing under test is absent is worse than a
  failing one.

## 5. Design & Architecture

### 5.1 The dialect is the point, not the model

```
    OllamaTransport            base URL, timeouts, errors — no credential, no region
    └── OpenAIDialectAdapter   /v1/chat/completions, /v1/embeddings
        └── (later) FoundryTransport reuses the dialect unchanged
```

Ollama's *native* API (`/api/chat`) is richer — it takes `format` for schemas and `think` for
reasoning models. Building against it would be a **fourth dialect that serves only us**. Building
against its OpenAI-compatible surface gives a real model *and* the dialect `FRD-120` needs, which
is the same work counted once instead of twice.

The cost of that choice is stated in §11: some of what we most want to verify may not be reachable
through the compatibility surface. That is measured before the adapter is written, not assumed.

### 5.2 What a real model tests that the mock cannot

| Property | Mock | Local model |
|---|---|---|
| `FRD-111` FR-6 — where thinking tokens are counted | we decide | **measured** |
| `FRD-112` FR-6 — a document that does not match | simulated | a real path |
| Stored payloads (`FRD-103`, `FRD-404`) | our own bytes, round-tripped | bytes a third party produced |
| Token counts, latency, cost (`FRD-403`) | invented | real, and the arithmetic is checkable |
| Cold start, capacity 429 (`ADR-0012` §5) | never happens | happens on the first request |
| `FRD-110` — a model that truly cannot read a PDF | declared | true |

### 5.3 Residency: local is not automatically anywhere

`RegionAllowed` treats a model with no declared region as "this deployment has no residency posture
to violate", which is right for the mock and would be *wrong* to leave here by default. A
self-hosted model is the strongest residency story there is — and an audit row that records nothing
cannot say so. So the seed declares a region for it (configurable, `on-premises` by default) and
the provenance columns carry it like any other.

### 5.4 Why no test may assert on an answer

A 0.6b model is stochastic, and pinning it with a seed makes the test pass until the day the model
file changes. Tests here assert what the *gateway* did: the row exists, the payload matches what
was sent, the token counts are positive and consistent with the response, the cost equals
tokens × price, the outcome and the role are what they should be. If a test would fail because the
model said something else, it is testing the wrong thing.

## 6. Data Model

None. Catalog declarations and prices travel the existing `aira.models` path.

## 7. API / Interface Contract

No new surface. Local models appear in `GET /v1beta/models` and — where they have a numeric id —
in the KIRA `/models` list, like any other.

## 8. Security & Privacy

- No credential to leak, which removes the largest risk the other adapters carry.
- **It must not become a way to bypass anything.** It goes through the same pre-dispatch gate,
  pipeline, dispatch chain and audit writer as everything else; `api/serving.py` is what makes that
  structural rather than a promise.
- The endpoint is unauthenticated by design, so it must not be exposed outside the Compose network.
  Documented in `DEPLOYMENT.md`, and it is a development/verification setting.

## 9. Observability

Unchanged in mechanism — the provenance columns record `ollama` as the provider and the declared
region, so a report can separate local traffic from cloud traffic. That separation is what stops a
demonstration figure being mistaken for a spend figure.

## 10. Testing & Acceptance Criteria

- **Unit** — the OpenAI mapping in both directions, streaming assembly, error mapping, and every
  option (`max_tokens`, `temperature`, and whichever of thinking/schema the surface exposes),
  against `httpx.MockTransport`. This is where the adapter is actually *proved*; the live model is
  what stops us proving the wrong thing.
- **Integration** — against the running model: a generation, a stream, an embedding, each producing
  an audit row whose stored request payload equals what was sent and whose cost equals
  tokens × the declared price. Skipped with a reason when Ollama is not up.
- **The two open questions** — a `disabled` and a large-budget thinking request, compared on their
  recorded output tokens (`FRD-111` FR-6); a schema request checked for whether the document
  actually conforms (`FRD-112` FR-6). Whatever the answers are, they get written down here.

**Acceptance**
- *Given* a running local model and a use case with a budget and a rate limit, *when* a member
  sends a request, *then* the answer comes back, the request log holds the prompt and the response,
  the cost is the declared price applied to the real token counts, and the audit row names the
  caller and their role.

## 11. Dependencies & Risks

- **Network.** Model layers come from `registry.ollama.ai`, which a locked-down sandbox denies by
  default. It has to be allowed, and if it cannot be, this FRD is not deliverable — which is worth
  knowing before the adapter is written rather than after.
- **Open — what the OpenAI surface exposes.** Thinking and response schemas are the two features
  most worth verifying and the two least certain to be reachable through a compatibility layer.
  **Measured first.** If they are absent, the honest outcome is that local models declare neither
  capability and the two questions stay open against the cloud — not that we quietly test them
  against the native API and call it verified.
- **CI weight.** An image plus a model is several hundred megabytes and a cold start. It belongs in
  its own cacheable job, not in `make test`.
- **Risk — a demonstration mistaken for evidence.** Invented prices produce real-looking spend
  figures. FR-5's naming is the mitigation, and §9's provider column is the second one.

## 12. Rollout / Demo

`docker compose --profile verify up ollama`, a seed that declares the models with fictitious
prices, and a documented walk-through: send a request as one role, read the row back, change the
role, watch the scope change. That walk-through is the demo the whole gateway exists for, and until
now it could only be run against a mock.
