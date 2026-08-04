# ADR-0005 — Gemini-compatible API surface first (OpenAI later)

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Vadim Scheibe

## Context
AIRA exposes a unified, provider-agnostic API. The PRD originally implied an OpenAI-compatible
surface as the primary one. However, the immediate consumers are **existing projects that already
run against the Google Gemini API** (Generative Language API). To be a drop-in for them, AIRA should
speak the **Gemini wire format first**. OpenAI compatibility remains desired but can follow.

## Options considered
- **OpenAI-compatible first** — largest ecosystem, but none of the current projects use it; would
  force clients to change now.
- **Gemini-compatible first** — matches the existing clients exactly (drop-in), delivers value
  immediately; OpenAI added later on the same canonical core.
- **Both at once** — more surface to build/test up front; slows the first useful release.

## Decision
Ship the **Gemini-compatible** surface first, mirroring the Google Generative Language API v1beta:

- `POST /v1beta/models/{model}:generateContent`
- `POST /v1beta/models/{model}:streamGenerateContent` (streaming)
- `POST /v1beta/models/{model}:embedContent`
- `GET  /v1beta/models` and `GET /v1beta/models/{model}`

Auth accepts our credentials the Gemini way (`x-goog-api-key` header or `?key=` query param for
AIRA API keys) **and** `Authorization: Bearer` (OIDC) — details in FRD-101.

Internally, every surface maps to **one canonical request/response schema** consumed by upstream
adapters. The **OpenAI-compatible** surface (`/v1/chat/completions`, `/v1/embeddings`, `/v1/models`)
is added later as **FRD-106** on top of the same canonical core.

## Consequences
- Positive: immediate drop-in for the existing Gemini-based projects; no client changes; a clean
  canonical layer keeps OpenAI (and other dialects) cheap to add later.
- Negative / trade-offs: we implement the Gemini schema (contents/parts/generationConfig/candidates/
  usageMetadata) now; the canonical layer adds one mapping indirection. OpenAI users wait for FRD-106.
- Follow-ups: `FRD-100` specifies the Gemini surface + canonical schema; `FRD-106` adds OpenAI.
  No new runtime dependency is required to implement the server side (FastAPI + Pydantic); the
  Google GenAI SDK is only relevant later for the real upstream adapter (Phase 3).
