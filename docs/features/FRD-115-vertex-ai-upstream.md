# FRD-115 — Vertex AI upstream on a regional endpoint

> Phase: 8 (KIRA parity) · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Origin: `kira_api.md` §5 (GCP endpoints), §10 (service-account config), programme: `ADR-0010`.
> Extends `FRD-304` (upstream adapters). Related: `FRD-116` (where the credential comes from).

## 1. Problem

`FRD-304` gave AIRA a real Google adapter that calls
`generativelanguage.googleapis.com/v1beta` with an API key from an environment variable. The
predecessor calls **Vertex AI** on regional endpoints — `europe-west1-aiplatform.googleapis.com`
and `aiplatform.eu.rep.googleapis.com` — authenticated with a **service account**.

These are not two spellings of the same thing:

- **Where the request is processed.** The Generative Language API is a global endpoint. Vertex AI's
  regional and EU-multi-region endpoints exist because organisations have to be able to say where
  the data went. If that requirement stands behind the predecessor — and a `eu` endpoint in a
  production configuration is fairly strong evidence that it does — then no amount of feature
  parity makes our current adapter a replacement.
- **How it authenticates.** An API key is a bearer secret with no identity, no rotation story worth
  the name, and no IAM. A service account has all three, and is what a corporate GCP project will
  actually grant.

So this is not an optimisation. Under a data-residency requirement it is the difference between
being able to decommission the predecessor and not.

## 2. Goals & Non-Goals

**Goals**
- Dispatch to Vertex AI on a configurable regional endpoint, with the region a property of the
  model rather than of the process.
- Authenticate with a service account: signed JWT exchanged for an access token, cached and
  refreshed, never on the request's critical path when it can be avoided.
- Coexist with the existing adapter, with an explicit and *loud* rule for which one serves a model.
- Everything above the adapter — pipeline, budgets, limits, persistence, reporting — unchanged.
  That is the payoff of the canonical core and this FRD should not need to touch any of it.

**Non-Goals**
- Retiring the Generative Language adapter. It stays: it is what makes a laptop with one API key a
  working development environment.
- Other Vertex surfaces (tuning, batch prediction, Model Garden third-party models).
- **Reading the credential from Vault** — `FRD-116`. This FRD takes it from configuration and is
  written so that swapping the source is a one-line change.

## 3. User Stories
- As **IT Security**, I want model traffic processed in a named region under a service-account
  identity, so that the data-protection assessment can be completed.
- As an **operator**, I want the region to be part of the model's definition, so that adding a
  model in a different region is configuration.

## 4. Functional Requirements

- **FR-1 Vertex dispatch.** `generateContent`, `streamGenerateContent`, `embedContent` and
  `batchEmbedContents` (`FRD-113`) against
  `{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/{model}`,
  with `aiplatform.eu.rep.googleapis.com` for the `eu` multi-region.
- **FR-2 Service-account auth.** RS256-signed JWT assertion exchanged at Google's token endpoint
  for an access token; the token is cached and refreshed **before** expiry, not on failure.
- **FR-3 Region per model.** Configured per model, not globally. The predecessor runs models in two
  regions simultaneously and so will we.
- **FR-4 One adapter per model, decided explicitly.** See §5.3.
- **FR-5 TLS is verified.** Stated as a requirement because the predecessor sets `verify=False`
  (`kira_api.md` §12.5) and this is a place where parity would be a mistake. We do not copy it.
- **FR-6 The credential never leaves the process.** Not logged, not in spans, not in error
  messages, not in `/readyz`. Same rule as the API key in `FRD-304`, restated because a
  service-account key is materially more valuable.
- **FR-7 Token acquisition failure is an upstream failure.** It maps to the existing
  `UpstreamError` handling — a 503-shaped answer with the reason in the log — and must not be
  reported as a client error.

## 5. Design & Architecture

### 5.1 The protocol already fits

`Upstream` is `models() / generate() / stream_generate() / embed()`. The Vertex adapter implements
it exactly as the Gemini one does; the request body is the same Gemini JSON. The differences are
the URL, the `Authorization` header, and the response envelope for streaming.

That is the whole point of `FRD-100`'s canonical core, and it is worth stating plainly as an
acceptance criterion: **if this FRD needs to change anything above `upstreams/`, something was
designed wrong.**

### 5.2 Tokens: refreshed ahead of time, shared, and never a thundering herd

An access token lives about an hour. Fetching it lazily on expiry means one request pays a
round trip, and under load *many* requests discover the expiry simultaneously and all fetch.

So: a single token holder per adapter, refreshing at ~80% of lifetime, with concurrent refreshes
collapsed into one in-flight attempt (`asyncio` single-flight). A failed refresh keeps serving the
still-valid token and retries with backoff; only an expired token with a failing refresh produces
FR-7's error. The clock is injectable, because "refreshes before expiry" is otherwise a property
that can only be tested by waiting an hour.

### 5.3 Two adapters, one model name — decide loudly

`ProviderRegistry` maps model name → provider by iterating providers and assigning, so **the last
provider registered silently wins**. With one adapter that was harmless. With two adapters that
both offer `gemini-2.0-flash`, it becomes a silent decision about which region and which
credential handled a request — and the wrong answer is invisible in every log and every report.

Two changes, both small:

- Registration **refuses a duplicate model name** at startup and names the two providers. Failing
  to boot is the correct response to an ambiguous routing table.
- Configuration decides which adapter serves which models, explicitly — Vertex is configured with
  its own model list per region, and a name in both lists is the error above.

The audit row and the span already record the model; they gain the **provider and region**, so
that "where was this processed" is answerable from data rather than from configuration
archaeology. Under a residency requirement this is not a nicety.

### 5.4 Configuration

```
AIRA_VERTEX_PROJECT=my-project
AIRA_VERTEX_CREDENTIALS=<service-account JSON>   # FRD-116 replaces this source
AIRA_VERTEX_MODELS=eu:gemini-2.5-pro,europe-west1:gemini-2.0-flash
```

Registered only when configured, exactly as `FRD-304`'s adapter is — so an unconfigured deployment
behaves as it does today.

## 6. Data Model

`request_logs` gains `provider` and `region` (nullable; migration). Reporting (`FRD-601`) can then
break down by them, which is what makes FR-3 auditable rather than merely configured.

## 7. API / Interface Contract

No public API change. Internal: the `Upstream` protocol, unchanged.

## 8. Security & Privacy

- **The service-account key is the most valuable secret in the deployment.** FR-6 governs it, and
  `FRD-116` is where it should ultimately live. Until then it is an environment variable and that
  fact is a known, documented gap — not a design.
- FR-5: TLS verification on.
- The token holder keeps the access token in memory only, never persisted or logged.
- Region recorded per request (§5.3) so residency is evidenced, not asserted.

## 9. Observability

- `aira.upstream.provider`, `aira.upstream.region` on the span and the audit row.
- A metric or log line on token refresh — success, failure, and the age at refresh. A credential
  that quietly stopped refreshing is otherwise found by an outage.
- `/readyz` reports adapter reachability per `FRD-117`, without exposing the credential.

## 10. Testing & Acceptance Criteria

- **Unit (hermetic, `MockTransport`)** — the URL is built correctly for a regional and for the `eu`
  endpoint; the bearer header is set; request and response mapping match `FRD-304`'s; upstream
  status codes pass through as they already do.
- **Unit (token holder)** — refreshes before expiry against an injected clock; concurrent callers
  produce **one** fetch; a failed refresh with a valid token keeps serving; an expired token with a
  failing refresh raises `UpstreamError` and not a client error. The single-flight property is
  written to fail first against a naive implementation.
- **Unit (registry)** — a duplicate model name across providers **refuses to start**, naming both.
- **Unit (secrets)** — the credential appears in no log record, span attribute or error message
  produced by the adapter. Asserted by capturing all three during a failing call.
- **Integration** — against a real project, if credentials are available in the environment;
  skipped with a clear reason otherwise. It must assert the recorded region.
- **Mutation** — TLS verification is actually on; the duplicate-name check actually refuses; the
  refresh threshold is actually below expiry.

**Acceptance**
- *Given* a Vertex-configured gateway, *when* a request is dispatched, *then* it is processed at
  the configured region, the audit row records provider and region, and the credential appears
  nowhere in logs, spans or the response.
- *Given* the same model name configured on two adapters, *when* the gateway starts, *then* it
  refuses to start and names both.

## 11. Dependencies & Risks

- **`FRD-116`** for the credential source; this FRD ships before it and says so.
- **Risk — clock skew** breaks JWT assertions. Mitigated by a small leeway and by an error message
  that names skew as a likely cause, because otherwise it presents as an unexplained 401.
- **Open — does the residency requirement actually apply to AIRA?** The predecessor's configuration
  implies it. If confirmed, this FRD is not optional and moves ahead of most of the programme; if
  not, it is a straightforward improvement. **This is the single most schedule-relevant open
  question in the programme.**

## 12. Rollout / Demo

Demo mode is unaffected — the mock stays the default and Vertex registers only when configured.
`deploy/compose/README.md` gains the configuration and a note that a service-account key in an
environment variable is an interim arrangement until `FRD-116`.
