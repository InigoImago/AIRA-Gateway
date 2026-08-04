# FRD-304 — Real upstream adapter: Google Gemini

> Phase: 3 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §4, §8 (FR-GW-4); `docs/ROADMAP.md` Phase 3; builds on FRD-100; `ADR-0005`

## 1. Summary
Add a **real** upstream provider that calls the Google Gemini (Generative Language) API, so the
gateway serves genuine model responses instead of the mock. It implements the same `Upstream`
protocol; since our surface is already Gemini-shaped (FRD-100), the mapping is nearly a passthrough
(canonical → Gemini request → real API → Gemini response → canonical). This unblocks pointing tools
(e.g. opencode via its Google provider) at AIRA against a specific use case with real answers.

## 2. Goals & Non-Goals
**Goals**
- `GeminiUpstream` provider: `generateContent`, `streamGenerateContent` (SSE), `embedContent` against
  `https://generativelanguage.googleapis.com/v1beta` using a Google API key.
- Expose configured Gemini model names (e.g. `gemini-2.0-flash`) in the registry alongside the mock.
- Make the `Upstream` protocol **async** (real HTTP); the mock becomes async too.
- Upstream errors surface as a Gemini-shaped **502** (not a bare 500); no key leakage in logs.
- Credentials from **Vault**/env (`google_api_key`); provider registered only when configured.

**Non-Goals**
- Per-use-case model **routing/rerouting** (FRD-301), **fallback** (FRD-302), the pipeline engine
  (FRD-300), Microsoft Foundry adapter, multimodal/tools. Those are separate.

## 3. Functional Requirements
- **FR-1 generate**: map canonical → Gemini request body, `POST /models/{model}:generateContent?key=…`,
  parse `candidates`/`usageMetadata` → canonical.
- **FR-2 stream**: `:streamGenerateContent?alt=sse` → parse `data:` events → canonical chunks.
- **FR-3 embed**: `:embedContent` → values.
- **FR-4 models**: expose the configured model names; the registry resolves them to this provider.
- **FR-5 async protocol**: `Upstream.generate/stream_generate/embed` become async; routes `await` them.
- **FR-6 errors**: non-2xx / transport error → `GeminiHTTPError(502, …, "UNAVAILABLE")`; the API key is
  sent as a query param and **never** logged.
- **FR-7 config**: `google_api_key`, `gemini_models` (csv), `gemini_base_url`; provider built +
  registered only when a key is present.

## 4. Design & Architecture
- `upstreams/gemini.py`: `GeminiUpstream(api_key, models, client)` using an injectable
  `httpx.AsyncClient` (tests pass a `MockTransport`-backed client — fully hermetic).
- `upstreams/gemini_mapping.py`: `canonical_to_gemini_request` + `gemini_response_to_canonical`
  (+ chunk parsing) — pure, unit-tested.
- App registers `[MockProvider(), *gemini]`; `build_gemini_upstream(settings)` returns None when no key.

## 5. Security & Privacy
- API key from Vault/env; sent to Google as `?key=`; excluded from logs/spans. Prompts/responses are
  persisted per FRD-103 (redaction hook applies).

## 6. Testing & Acceptance
- Hermetic tests (httpx `MockTransport`): request mapping (contents/generationConfig), response
  parsing (text/usage/finishReason), streaming SSE parsing, embed, and error → 502. Coverage gate stays.
- **Acceptance**: with `AIRA_GOOGLE_API_KEY` set and the domain allowed, a `generateContent` to a real
  `gemini-*` model returns a real completion; opencode (Google provider, baseURL `…/uc/<uc>/v1beta`,
  AIRA API key) gets real answers attributed to the use case, persisted + traced.

## 7. Dependencies & Risks
- Builds on FRD-100/101/102/103. Needs a Google API key + network egress to
  `generativelanguage.googleapis.com` (allowlist). Risk: Gemini schema drift → keep mapping permissive.
