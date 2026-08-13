# FRD-120 — Microsoft Foundry: Azure OpenAI and Microsoft's own models

> Phase: 8 (KIRA parity) / Phase 3 backlog · Status: **Built, hermetically verified only**
> Owner: Vadim Scheibe
>
> `FoundryTransport` × the **unchanged** OpenAI dialect × `AzureRoutes` exist and are tested
> (`gateway/tests/test_foundry.py`). What has never happened is a request to a real Azure
> subscription — so this is *built*, not *proven*, and the two are recorded apart on purpose.
> Architecture: `ADR-0011` (platform × dialect × identity). Requires `FRD-114`, `FRD-115`
> (shared `TokenSource`). Named as an intended adapter since `FRD-304`.

## 1. Problem

A third vendor platform is wanted: **Microsoft Foundry**, serving Azure OpenAI models (GPT family,
the reasoning series) and Microsoft's own models (Phi, MAI), plus other vendors hosted there.

It is not urgent, and it is the platform that decides the shape of the upstream layer — so it is
specified now and built later. `ADR-0011` explains why: with two vendors any difference can be
absorbed by a conditional; the third is where a wrong abstraction becomes a rewrite.

Foundry brings three things the first two did not:

- **The OpenAI Chat Completions wire format** — a third dialect, and the same format the deferred
  OpenAI *surface* (`FRD-106`) would need. The mapper arrives either way.
- **Entra ID authentication** (or managed identity in Azure) — a third credential mechanism.
- **Deployments instead of models.** Azure addresses a customer-named deployment in a resource in a
  region. The name a caller uses and the name Azure uses are different strings, and the same
  deployment name in two resources can be two different models.

## 2. Goals & Non-Goals

**Goals**
- Dispatch canonical requests to Foundry-hosted models: generation, streaming, attachments,
  reasoning effort, structured output, embeddings.
- Authenticate with Entra ID or a managed identity, reusing `FRD-115`'s token behaviour rather than
  reimplementing it.
- Keep model naming stable for callers while Azure deployments change underneath (`ADR-0011` rule 2).
- Regional constraint enforced the same way as for Vertex — one mechanism, not two.
- The vendor stays invisible above `upstreams/`.

**Non-Goals**
- **The OpenAI-compatible inbound surface** (`FRD-106`). Still deferred. This FRD builds the dialect
  in the *upstream* direction only; that it makes the surface cheaper later is a consequence, not a
  scope change.
- Foundry's agent, evaluation and fine-tuning surfaces. We consume model inference.
- Calling OpenAI directly. Under `ADR-0011` that would be a transport reusing this dialect — a
  small piece of work, and a separate residency and contract decision.
- Other vendors hosted on Foundry beyond the OpenAI-compatible inference API. They are reachable
  through the same dialect where they speak it; anything else is its own dialect.

## 3. User Stories
- As a **use-case administrator**, I want a fallback chain spanning Google, Anthropic and
  Microsoft, so that one vendor's outage is not my outage.
- As **IT Steuerung**, I want spend across three vendors in one report, priced per model.
- As an **operator**, I want to move an Azure deployment without editing every use case.

## 4. Functional Requirements

- **FR-1 OpenAI dialect.** Chat completions and embeddings against Foundry's inference endpoints,
  with the API version pinned in configuration rather than in code.
- **FR-2 Entra ID / managed identity**, through `FRD-115`'s shared `TokenSource` (`ADR-0011`
  rule 1). An API key remains possible for development.
- **FR-3 Caller-facing names, catalog addressing.** The caller says `gpt-5`; the catalog resolves
  transport, resource, deployment and the **underlying model for pricing** (`ADR-0011` rule 2).
  A pipeline configuration never contains a deployment name.
- **FR-4 Region allow-list**, using the shared policy unchanged — `AIRA_ALLOWED_REGIONS`
  already carries Azure's EU regions, so a Foundry model in `westeurope` is permitted by the
  default and one outside the EU refuses to start, with no new setting.
- **FR-5 Reasoning maps to effort levels.** §5.3.
- **FR-6 Structured output via `response_format: json_schema` with `strict`.** A schema field with
  no faithful equivalent is a refusal, not a silent drop — the rule `FRD-119` §5.5 established.
- **FR-7 Attachments** as OpenAI content parts, subject to what the model declares (`FRD-114`).
- **FR-8 Usage mapping**, including reasoning tokens (§5.3) and cached input tokens, each recorded
  distinctly rather than folded into a number we then cannot explain.
- **FR-9 Errors and quota** mapped to `UpstreamError` with status preserved, so the existing
  429/503/504 pass-through keeps working across all three vendors.

## 5. Design & Architecture

### 5.1 Composition, per `ADR-0011`

`FoundryTransport` (endpoint, Entra token source, API version, retries, Azure error shapes) ×
`OpenAIDialect` (bodies, streaming chunks, usage). The dialect is written to be **platform-free** —
it must not know the word "Azure" — because that is what makes it reusable by a direct-OpenAI or
on-prem transport later, which is most of `ADR-0011`'s justification.

The immediate test of that: the dialect's unit tests construct requests and responses with no
transport at all, exactly as `upstreams/gemini_mapping.py` is tested today.

### 5.2 Deployments, and why the indirection earns its place

Azure's addressing is `{resource}/openai/deployments/{deployment}`. The deployment name is chosen
by whoever created it and says nothing reliable about the model. Consequences if we let it be the
model name:

- Every use case's pipeline configuration would embed Azure resource naming, so a deployment
  migration would be a configuration migration across every use case.
- **Pricing would break quietly.** `FRD-403` prices by model name; a deployment called `production`
  has no price, and unpriced traffic is *counted apart rather than as zero* — so the effect would
  be a spend figure that silently stops being complete, which is precisely the failure that rule
  exists to prevent.

So `FRD-114` carries, per catalog entry: the caller-facing name, the platform, the platform's
addressing, and the **underlying model** the price attaches to. One dictionary lookup on a
read-model already consulted for pricing.

### 5.3 Reasoning: a third shape, and it fits

Azure's reasoning models take `reasoning_effort` — an abstract level, with no token budget. Gemini
takes a budget; Anthropic takes a budget bounded by `max_tokens`.

`FRD-111`'s canonical model is `mode` + optional `tokens`, with modes
`disabled|limited|auto|high|medium|low|minimal`. The abstract levels map straight onto
`reasoning_effort`; `limited` with an explicit count has no equivalent and is refused by capability
(`FRD-114` declares which modes a model accepts), rather than being silently approximated. That is
the design working: the canonical model was taken from the predecessor's vocabulary and turns out
to cover a vendor it was not written for.

Two specifics for the reservation and the bill:

- Reasoning tokens are billed as output and are **also reported separately**
  (`completion_tokens_details.reasoning_tokens`). FR-8 records both, which is what finally answers
  `FRD-111` FR-6's open verification for at least one vendor.
- Effort levels have no numeric budget, so `FRD-111` §5.3's reservation uses the model's declared
  estimate per level (`FRD-114`) — the same table that maps levels to budgets elsewhere.

### 5.4 Structured output: the third mechanism

Gemini has `responseSchema`; Anthropic needs a forced tool call; OpenAI has
`response_format: {type: "json_schema", json_schema: {schema, strict: true}}`.

Three unrelated mechanisms behind one capability flag is exactly what `ADR-0011` rule 3 is about.
`FRD-112`'s subset translates to JSON Schema much as it does for Anthropic; `strict` mode restricts
what is expressible (no open-ended objects, all properties required), so a schema that cannot be
expressed strictly is either sent non-strict or refused — **declared per model**, never chosen
silently, because the difference is whether the guarantee holds.

### 5.5 What this does to `FRD-106`

Once canonical ⇄ OpenAI exists for the upstream direction, the deferred OpenAI *inbound* surface is
largely the same mapping reversed plus a router. The decision to defer it stands; the estimate
behind that decision does not survive this FRD, and should be revisited when it is next discussed.

## 6. Data Model

No new tables. `FRD-114` gains the platform addressing and underlying-model fields (see `ADR-0011`
rule 2), which are additive and nullable. `request_logs` already gains provider/publisher/region
from `FRD-115`; Foundry populates them.

## 7. API / Interface Contract

No public API change. Internal: `Upstream`, unchanged — asserted as in `FRD-115` §10.

## 8. Security & Privacy

- **Managed identity is preferable to a key** wherever the deployment runs in Azure: no secret to
  store, rotate or leak. Where a key is used it follows `FRD-116`.
- The token source never logs its credential (`FRD-115` FR-8, unchanged).
- FR-4: region enforced at startup, recorded per request. Residency is a per-platform question with
  one mechanism.
- Three vendors means three sets of terms and three data-processing positions. That is a
  procurement matter, not a code one, and is noted here so it is not discovered late.

## 9. Observability

`aira.upstream.provider = "foundry"`, plus deployment and underlying model on the audit row — a
spend anomaly is otherwise untraceable to the deployment that caused it.

## 10. Testing & Acceptance Criteria

- **Unit (dialect, no transport)** — request and response mapping both ways; system message
  handling; streaming chunk assembly; usage including reasoning and cached tokens; every finish
  reason. The dialect's tests must not mention Azure (§5.1); that is the reusability check.
- **Unit (resolver)** — a caller-facing name resolves to the right deployment; **pricing attaches
  to the underlying model, not the deployment name** (written to fail first against a
  name-is-the-model implementation, since that failure would otherwise show up only as slowly
  incomplete spend figures).
- **Unit (reasoning)** — each effort level maps; `limited` with a token count is refused by
  capability rather than approximated; reservation uses the declared per-level estimate.
- **Unit (structured output)** — a `FRD-112` schema becomes a `json_schema` response format; a
  schema not expressible under `strict` follows the model's declaration rather than a silent
  fallback.
- **Unit (token source)** — the shared refresh/single-flight behaviour, exercised through the Entra
  implementation, so the abstraction is proven by a second user rather than asserted.
- **Integration** — against a real Foundry resource where credentials exist; skipped clearly
  otherwise. A three-vendor fallback chain answers in any order.
- **Mutation** — pricing resolves through the underlying model; the region allow-list restricts;
  reasoning tokens are recorded and not folded away.

**Acceptance**
- *Given* a fallback chain `[gemini-…, claude-…, gpt-…]`, *when* the first two are unavailable,
  *then* the third answers and the caller cannot tell which did from the response shape.
- *Given* an Azure deployment renamed on the vendor's side, *when* the catalog entry is updated,
  *then* no use-case configuration changes and pricing is unaffected.

## 10a. What was built (2026-08-06)

`FoundryTransport` (endpoint, credential, api-version) × the **unchanged** OpenAI dialect ×
`AzureRoutes`. The dialect gained nothing, the mappers gained nothing, and the one genuinely
missing piece was the routing axis — which §5.1 predicted and is the reason this FRD was cheap.

**`ADR-0011` was the thing under test, and it holds.** The claim is that transport × dialect ×
model identity is enough structure for a third vendor; a change reaching into the canonical core,
the pipeline or a surface would have falsified it. The diff does not leave `upstreams/`. The
architecture assertion in `test_vertex.py` now also refuses the word "azure" above the platform
packages — and caught the first draft, where `AzureRoutes` had been written into the *dialect's*
package. A dialect that names a platform is one the next platform cannot reuse, so it moved.

One deliberate exemption was added to that assertion and is worth stating: `residency.py` names
every cloud's regions on purpose (`ADR-0012` §6). A list that could not name Azure's would be the
per-cloud list that decision rejected, and a per-cloud audit with it.

Two decisions inside:

- **One adapter per region**, not one adapter carrying a region. Provenance is recorded per model
  (`FRD-115` FR-10), so a deployment fleet spread across two regions must not be flattened into
  whichever was declared first — that would put a residency claim on the audit row the request did
  not satisfy, which is worse than recording none.
- **The credential is fetched, not captured.** `headers()` became async so an Entra token can be
  minted and refreshed through the shared `TokenSource`. Reading it once at construction is the
  version that works for an hour and then fails for the life of the process — a failure that only
  appears in a long-running deployment.

Not verified against a real subscription, and that is stated rather than implied: there is no Azure
to point at here. 18 hermetic tests, mutations **F1**–**F6**. `F1` is the one with money in it —
a deployment name has no price, and unpriced traffic is counted apart rather than as zero, so
getting the attribution wrong would not *fail*, the spend figure would quietly stop being complete.

## 11. Dependencies & Risks

- **`ADR-0011`** (the shape), **`FRD-114`** (addressing, capabilities), **`FRD-115`** (shared
  `TokenSource`, region allow-list).
- **Risk — the OpenAI dialect ends up Azure-shaped**, which would forfeit most of `ADR-0011`'s
  benefit. §5.1's no-transport tests are the tripwire.
- **Risk — API versioning.** Azure pins behaviour to an `api-version`; a newer version can change
  response fields. Pinned in configuration, and the integration test asserts the pinned version is
  the one used.
- **Open — which Foundry surface.** The Azure OpenAI endpoints and the unified model-inference
  endpoint differ in path and in what they support. One authenticated call against the target
  resource settles it, as with `FRD-115` §11 for Vertex, and it should be settled before
  implementation rather than during.
- **Open — residency and vendor terms** for Microsoft-hosted models, in the same way the EU
  requirement was settled for Google.

## 12. Rollout / Demo

A third mock publisher returning OpenAI-shaped bodies, so the dialect and the three-vendor fallback
chain are exercisable hermetically and in demo mode without any cloud account — the same approach
`FRD-119` takes, and what makes the cross-vendor acceptance test runnable in CI.
