# FRD-115 — Reaching the models: Vertex AI / Model Garden in the EU

> Phase: 8 (KIRA parity) · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Origin: `kira_api.md` §5, §10; **confirmed 2026-08-06: EU residency applies, and access is via
> the Gemini Enterprise platform's Model Garden, serving Gemini *and Anthropic* models.**
> Programme: `ADR-0010`. Extends `FRD-304`. Paired with `FRD-119` (the Anthropic dialect).
> Related: `FRD-116` (where the credential comes from).

## 1. Problem

`FRD-304` gave AIRA a Google adapter that calls `generativelanguage.googleapis.com/v1beta` — a
**global** endpoint — with an API key from an environment variable. Two facts, both now confirmed
rather than inferred, make that insufficient:

- **EU residency applies.** Requests must be processed in the EU. A global endpoint cannot make
  that statement, so our current adapter is not a candidate for production regardless of how
  complete the rest of the parity work becomes.
- **Access is through Model Garden, and it serves two vendors.** Gemini *and* Anthropic models
  through one platform, one project, one credential. That is a procurement and governance win and
  a technical complication: **the two vendors do not share a wire format.**

The second point is the one that matters architecturally. Anthropic models on Vertex are called
through `:rawPredict` / `:streamRawPredict` and speak the **Anthropic Messages API** — different
request body, different streaming events, different usage fields, a *required* `max_tokens`, and a
completely different mechanism for structured output. It is a second dialect, not a second base URL.

This FRD covers **how we reach the platform** — endpoint, region, credential, registry. `FRD-119`
covers **the Anthropic dialect** itself. Splitting them keeps the platform work testable before
either dialect exists, and keeps the dialect work free of authentication concerns.

## 2. Goals & Non-Goals

**Goals**
- Dispatch to Vertex AI on a configurable EU endpoint, with the region a property of the model.
- Authenticate with a **service account**: signed JWT exchanged for an access token, cached,
  refreshed ahead of expiry, single-flighted.
- One access path serving **both** publishers, with the dialect chosen per model.
- Record **provider, publisher and region per request**, so residency is evidenced from data
  rather than asserted from configuration.
- Everything above `upstreams/` unchanged.

**Non-Goals**
- **The Anthropic wire format** — `FRD-119`.
- Retiring the Generative Language adapter. It stays as the laptop path: one API key, no GCP
  project, `make up` works.
- The Gemini Enterprise platform's **agent** surface (data stores, grounding, orchestration). We
  consume *models* through Model Garden; if the agent surface is ever wanted it is a separate
  upstream with a separate decision. See §11.
- Tuning, batch prediction, non-Anthropic third-party Model Garden vendors.
- Reading the credential from Vault — `FRD-116`.

## 3. User Stories
- As **IT Security**, I want every model call processed in the EU under a service-account identity,
  and I want each request's region recorded, so that the data-protection assessment rests on
  evidence.
- As a **use-case administrator**, I want to route to a Gemini or an Anthropic model by name and
  have everything else behave identically.
- As an **operator**, I want one credential and one project for both vendors.

## 4. Functional Requirements

- **FR-1 Vertex dispatch.** `POST {host}/v1/projects/{project}/locations/{location}/publishers/
  {publisher}/models/{model}:{method}`, where `{host}` is `{location}-aiplatform.googleapis.com`
  or `aiplatform.eu.rep.googleapis.com` for the `eu` multi-region.
- **FR-2 Publisher-dependent method and dialect.** `publishers/google` → `:generateContent` /
  `:streamGenerateContent` / `:embedContent`, Gemini body. `publishers/anthropic` → `:rawPredict` /
  `:streamRawPredict`, Anthropic body (`FRD-119`).
- **FR-3 Service-account auth.** RS256-signed JWT assertion exchanged for an access token; cached
  and refreshed **before** expiry, not on failure. One credential for both publishers.
- **FR-4 Region per model**, not per process. Configuration carries region *and* publisher with
  each model name.
- **FR-5 EU-only by configuration, checked at startup.** See §5.5.
- **FR-6 One adapter per model, decided loudly.** A model name offered by two providers refuses to
  start, naming both (§5.4).
- **FR-7 TLS is verified.** Stated because the predecessor sets `verify=False` (`kira_api.md`
  §12.5). We do not copy it.
- **FR-8 The credential never leaves the process.** Not logged, not in spans, not in error
  messages, not in `/readyz`. Restated from `FRD-304` because a service-account private key is
  materially more valuable than an API key.
- **FR-9 Token-acquisition failure is an upstream failure**, mapping to `UpstreamError` and a
  503-shaped answer — never a client error.
- **FR-10 Provider, publisher and region on every audit row and span.** FR-5 is a configuration
  claim; this is what makes it auditable.

## 5. Design & Architecture

### 5.1 One transport, two dialects

```
VertexTransport          # URL building, auth, retries, error mapping — publisher-agnostic
├── VertexGeminiAdapter    # Gemini bodies (reuses FRD-304's mappers unchanged)
└── VertexAnthropicAdapter # Anthropic bodies (FRD-119)
```

Both adapters implement the existing `Upstream` protocol, so nothing above `upstreams/` learns that
a second vendor exists. The transport owns everything that is about *Google the platform*
(endpoint, OAuth, quota errors); each adapter owns everything that is about *the vendor's API*.

Getting that seam right is the whole design. Put authentication in the adapters and it is written
twice; put body mapping in the transport and adding a third vendor rewrites it.

### 5.2 Tokens: ahead of time, shared, single-flight

An access token lives about an hour. Fetching lazily on expiry makes one request pay a round trip
and, under load, makes *many* requests discover the expiry at once and all fetch.

One token holder per project (not per adapter — the credential is shared), refreshing at ~80% of
lifetime, with concurrent refreshes collapsed into one in-flight attempt. A failed refresh keeps
serving the still-valid token and retries with backoff; only an expired token with a failing
refresh produces FR-9. The clock is injectable, because "refreshes before expiry" is otherwise a
property testable only by waiting an hour.

### 5.3 Model naming has an `@` in it

Anthropic models on Vertex carry a version suffix — `claude-sonnet-4-5@20250929`. That string
travels through our model catalog, pipeline configuration, URL paths, Kafka keys and reporting
group-by. `@` is not a problem for any of them, but it is exactly the kind of character that turns
out to be a problem in one place — a validation regex written for `[a-z0-9-]`, or an unencoded URL
segment.

So: the allowed model-name character set is defined once, `@` is in it, and the URL segment is
encoded on the way out. Asserted by a test using a real Anthropic model name, not a placeholder.

### 5.4 Two adapters, one model name — refuse to start

`ProviderRegistry` maps model name → provider by iterating and assigning, so **the last provider
registered silently wins**. Harmless with one adapter. With three — Generative Language, Vertex
Gemini, Vertex Anthropic — it becomes a silent decision about which region and which credential
handled a request, and the wrong answer is invisible in every log and every report.

Registration therefore **refuses a duplicate model name at startup and names both providers**.
Failing to boot is the correct response to an ambiguous routing table; a running gateway that
sometimes leaves the EU is not.

### 5.5 "EU-only" has to be checked, not intended

FR-5 is the requirement the residency assessment actually rests on, and configuration alone will
not hold it: someone adds a model in `us-central1` because that is where a preview model launched,
and nothing objects.

So the gateway takes an **allowed-region list** (default: the EU regions and `eu`), and a model
configured outside it **refuses to start**, naming the model and the region. Combined with FR-10's
per-request recording, the claim becomes: configuration cannot express a non-EU region, and every
request carries evidence of where it went.

An organisation that deliberately wants a non-EU region changes one setting and thereby makes an
explicit decision — which is the point.

### 5.6 Configuration

```
AIRA_VERTEX_PROJECT=my-project
AIRA_VERTEX_CREDENTIALS=<service-account JSON>     # FRD-116 replaces this source
AIRA_VERTEX_ALLOWED_REGIONS=eu,europe-west1,europe-west4
AIRA_VERTEX_MODELS=eu/google/gemini-2.5-pro,eu/anthropic/claude-sonnet-4-5@20250929
```

`region/publisher/model` per entry — the three things FR-2 and FR-4 need. Registered only when
configured, so an unconfigured deployment behaves exactly as today.

## 6. Data Model

`request_logs` gains `provider`, `publisher` and `region` (nullable; one migration). `FRD-601`'s
reporting can then break down by them — which is what turns FR-10 from a log line into an
answerable question.

## 7. API / Interface Contract

No public API change. Internal: `Upstream`, unchanged — and that is an acceptance criterion, not an
observation (§10).

## 8. Security & Privacy

- **The service-account key is the most valuable secret in the deployment.** FR-8 governs it;
  `FRD-116` is where it should live. Until then it is an environment variable, and that is a
  documented gap rather than a design.
- FR-7: TLS verification on.
- FR-5 + FR-10: residency enforced at startup and evidenced per request.
- Access tokens live in memory only.
- Both publishers under one credential means one IAM grant to review — an advantage, provided the
  grant is scoped to the models actually used rather than to the project.

## 9. Observability

- `aira.upstream.provider`, `aira.upstream.publisher`, `aira.upstream.region` on spans and audit
  rows.
- Token refresh logged with outcome and token age at refresh. A credential that quietly stopped
  refreshing is otherwise discovered by an outage.
- Quota/429 responses distinguished from other upstream failures — per-model, per-region Vertex
  quota is a different operational problem from a model being down, and `FRD-117`'s readiness
  probe should not confuse them.

## 10. Testing & Acceptance Criteria

- **Unit (hermetic, `MockTransport`)** — URLs built correctly for a regional host, the `eu`
  multi-region, and both publishers with their respective methods; the bearer header set; upstream
  status codes passed through as they already are.
- **Unit (token holder)** — refreshes before expiry against an injected clock; concurrent callers
  produce **one** fetch; a failed refresh with a valid token keeps serving; an expired token with a
  failing refresh raises `UpstreamError`, not a client error. The single-flight property written to
  fail first against a naive implementation.
- **Unit (registry)** — a duplicate model name across providers **refuses to start**, naming both.
- **Unit (residency)** — a model configured in a disallowed region **refuses to start**, naming
  both. Written to fail first against a warning-only implementation.
- **Unit (naming)** — a real Anthropic model name with `@` survives validation, URL construction,
  Kafka key round-trip and reporting group-by.
- **Unit (secrets)** — the credential appears in no log record, span attribute or error message.
  Asserted by capturing all three during a failing call.
- **Architecture assertion** — the diff for this FRD touches nothing outside `upstreams/`, the
  settings, the migration and the tests. If it does, `FRD-100`'s canonical core is less
  provider-agnostic than claimed and that is worth finding out here rather than at the third vendor.
- **Integration** — against the real project where credentials exist; skipped with a clear reason
  otherwise. Must assert the recorded region and publisher.
- **Mutation** — TLS verification actually on; the duplicate-name check actually refuses; the
  region allow-list actually refuses; the refresh threshold actually below expiry.

**Acceptance**
- *Given* a Vertex-configured gateway, *when* a request is dispatched to a Gemini and to an
  Anthropic model, *then* both are processed in the configured EU region, both audit rows record
  provider, publisher and region, and the credential appears nowhere in logs, spans or responses.
- *Given* a model configured in `us-central1`, *when* the gateway starts, *then* it refuses and
  names the model and the region.

## 11. Dependencies & Risks

- **`FRD-119`** for the Anthropic dialect; **`FRD-116`** for the credential source; **`FRD-114`**
  so a model's publisher and capabilities are declared rather than inferred.
- **Open — which surface of "Gemini Enterprise" do we call?** Two readings, and they lead to
  different adapters:
  1. **Model Garden raw model access** (assumed here): the platform is the procurement and
     governance vehicle; we call the Vertex publisher endpoints directly. Everything above is
     written for this.
  2. **The agent platform's own API**: assistants, data stores, grounded answers. That is not a
     model API and would be a different upstream with different semantics — grounding citations,
     server-side conversation state — none of which the canonical core models today.

  The phrasing "to access the Gemini and Anthropic models" reads as (1), which is why this FRD
  assumes it. **Confirm before implementation** — one authenticated `curl` against the project's
  `publishers/anthropic` endpoint settles it in a minute, and getting it wrong is a rewrite rather
  than a correction.
- **Risk — clock skew** breaks JWT assertions and presents as an unexplained 401. Small leeway
  plus an error message that names skew as a likely cause.
- **Risk — Model Garden requires per-model enablement** in the project. A model that is configured
  but not enabled fails at first call, not at startup. `FRD-117`'s readiness probe should surface
  it; the error message must distinguish "not enabled" from "not found".

## 12. Rollout / Demo

Demo mode unaffected: the mock stays the default and Vertex registers only when configured.
`deploy/compose/README.md` gains the configuration, the region allow-list, and a note that a
service-account key in an environment variable is interim until `FRD-116`.
