# FRD-100 — Gemini-compatible Unified API

> Phase: 1 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §5 (FR-GW-1), `docs/ROADMAP.md` Phase 1, `ADR-0005`

## 1. Summary
Expose a **Gemini-compatible** REST surface mirroring Google's Generative Language API (v1beta), so
projects already built against Gemini work against AIRA as a drop-in. Incoming requests are mapped
to a **canonical internal schema**, dispatched to an upstream provider (the deterministic mock in
Phase 1), and the canonical response is mapped back to the Gemini wire format. This canonical core is
what lets an OpenAI-compatible surface (FRD-106) be added later without touching upstreams.

## 2. Goals & Non-Goals
**Goals**
- Implement the core Gemini endpoints (generateContent, streamGenerateContent, embedContent,
  list/get models) with Gemini-shaped request/response bodies.
- Define a provider-agnostic **canonical schema** and the Gemini⇄canonical mappers.
- Wire the endpoints to the mock upstream so the surface works end-to-end offline.
- Gemini-style error bodies and status codes.

**Non-Goals**
- **Auth** (FRD-101), **attribution** (FRD-102), **persistence** (FRD-103), **full mock fidelity**
  (FRD-104), **tracing/IP capture** (FRD-105) — layered on later. FRD-100 endpoints are open in demo.
- Real Gemini/Foundry upstreams (Phase 3). OpenAI surface (FRD-106).
- Tools/function-calling, multimodal parts beyond text (later; schema is designed to extend).

## 3. User Stories
- As a **developer with a Gemini project**, I want to point my client's base URL at AIRA and have
  `:generateContent` work unchanged.
- As a **maintainer**, I want one canonical schema so adding OpenAI later is a new mapper, not a new
  pipeline.

## 4. Functional Requirements
- **FR-1 generateContent**: `POST /v1beta/models/{model}:generateContent` accepts a Gemini
  `GenerateContentRequest` (`contents[]` of `{role, parts[]}`, optional `systemInstruction`,
  `generationConfig`) and returns a `GenerateContentResponse` (`candidates[]` with
  `content`, `finishReason`, `index`; `usageMetadata`; `modelVersion`).
- **FR-2 streamGenerateContent**: `POST /v1beta/models/{model}:streamGenerateContent` streams
  partial `GenerateContentResponse` chunks (SSE / chunked JSON), terminating cleanly.
- **FR-3 embedContent**: `POST /v1beta/models/{model}:embedContent` returns `{embedding: {values[]}}`.
- **FR-4 list/get models**: `GET /v1beta/models` and `GET /v1beta/models/{model}` return Gemini
  `Model` resources (name, version, supported methods) for the models AIRA exposes (mock in Phase 1).
- **FR-5 Canonical schema**: an internal `CanonicalRequest`/`CanonicalResponse` (messages with role +
  text, generation params, usage) that Gemini bodies map to/from; upstream adapters speak canonical.
- **FR-6 Model routing (minimal)**: resolve `{model}` to an upstream; unknown model → Gemini-style
  404. (Rich routing/rerouting is Phase 3.)
- **FR-7 Errors**: Gemini-shaped error envelope `{"error": {"code", "message", "status"}}` with
  appropriate HTTP status (400 invalid arg, 404 model not found, 500 internal).

## 5. Design & Architecture
```
Gemini request ──▶ gemini schema (pydantic) ──▶ map ──▶ CanonicalRequest ──▶ upstream (mock)
Gemini response ◀── gemini schema ◀── map ◀── CanonicalResponse ◀────────────┘
```
- **Modules** (gateway):
  - `api/gemini/schemas.py` — Pydantic models for the Gemini wire format.
  - `api/gemini/routes.py` — the `:generateContent` / `:embedContent` / models routes.
  - `api/gemini/mapping.py` — Gemini ⇄ canonical mappers.
  - `core/canonical.py` — canonical request/response/usage models.
  - `upstreams/` — providers implement a small `Upstream` protocol (`complete`, `embed`); the mock
    from FRD-002 is adapted to it.
- **Routing note**: FastAPI path `{model}:generateContent` — the `:method` suffix is parsed from the
  path segment (custom route handling), matching Gemini's colon-verb convention.
- **Streaming**: FastAPI `StreamingResponse`; the mock yields a few deterministic chunks.

## 6. Data Model
- No DB persistence yet (FRD-103). In-memory model registry describing the mock model(s).

## 7. API / Interface Contract (essentials)
Request (generateContent):
```json
{ "contents": [{"role": "user", "parts": [{"text": "Hi"}]}],
  "generationConfig": {"temperature": 0.2, "maxOutputTokens": 256} }
```
Response:
```json
{ "candidates": [{"content": {"role": "model", "parts": [{"text": "..."}]},
                  "finishReason": "STOP", "index": 0}],
  "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 7, "totalTokenCount": 8},
  "modelVersion": "mock-1" }
```

## 8. Security & Privacy
- FRD-100 ships **open in demo mode**; real auth/authorization arrives in FRD-101 (accepting
  `x-goog-api-key`, `?key=`, or `Authorization: Bearer`). Input is validated by the Pydantic schema.

## 9. Observability
- Reuse the FRD-001 baseline: FastAPI auto-instrumentation already traces these routes; add a span
  attribute for the resolved model and a request/response token count metric (basic).

## 10. Testing & Acceptance Criteria
- **Tests**: schema validation (valid/invalid bodies); Gemini⇄canonical mapping round-trips;
  route tests for generateContent (200 + shape), embedContent, list/get models, unknown-model 404,
  invalid-body 400; a streaming test collecting chunks. Keep coverage gate green.
- **Acceptance**:
  - **Given** the gateway is running, **when** a client POSTs a valid Gemini `generateContent` body
    to `/v1beta/models/mock-1:generateContent`, **then** it gets a well-formed `GenerateContentResponse`
    with a deterministic candidate and `usageMetadata`.
  - **When** `{model}` is unknown, **then** a Gemini-shaped 404 is returned.
  - **When** the body is malformed, **then** a Gemini-shaped 400 is returned.
  - **When** calling `:streamGenerateContent`, **then** multiple chunks stream and terminate.

## 11. Dependencies & Risks
- Depends on FRD-000/002 (gateway skeleton, mock upstream). Feeds FRD-101/102/103/105 (which wrap
  auth/attribution/persistence/tracing around these routes) and FRD-106 (OpenAI on the same core).
- Risk: exact Gemini schema nuances (field names, enums) → follow the public v1beta shapes; keep
  models permissive (ignore unknown fields) to avoid breaking real clients.
- Risk: colon-verb routing in FastAPI → handle via explicit path handling; covered by tests.

## 12. Rollout / Demo
- Demo: `make up && make run-gateway`, then `curl` a Gemini `generateContent` request to the mock
  model and show the Gemini-shaped response; a trace appears in Grafana.
