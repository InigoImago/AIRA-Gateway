# FRD-104 — Mock upstream fidelity (streaming SSE, generationConfig)

> Phase: 1 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §5 (FR-GW-13); `docs/ROADMAP.md` Phase 1; builds on FRD-100; `ADR-0005`

## 1. Summary
Raise the deterministic mock and the Gemini streaming surface to real-client fidelity so the
official `google-genai` SDK works as a drop-in. Adds **SSE streaming** (`?alt=sse`), the correct
default **streamed JSON array** form, and mock honouring of `generationConfig` (max output tokens →
`MAX_TOKENS` finish reason).

## 2. Goals & Non-Goals
**Goals**
- `:streamGenerateContent?alt=sse` returns `text/event-stream` with `data: {json}\n\n` events (SDK path).
- Default `:streamGenerateContent` returns a streamed **JSON array** `[{chunk}, …]` (Gemini's REST form).
- Mock honours `generationConfig.maxOutputTokens`: truncates the completion and reports
  `finishReason=MAX_TOKENS` (else `STOP`).

**Non-Goals**
- Multimodal parts, tools/function-calling, `thinkingConfig`, `responseSchema` (later; accepted-but-
  ignored). Real latency simulation (optional, deferred). Real upstreams (Phase 3).

## 3. Functional Requirements
- **FR-1 SSE**: when `alt=sse`, stream `data: <GenerateContentResponse JSON>\n\n` events,
  `Content-Type: text/event-stream`.
- **FR-2 JSON array**: otherwise stream `[` + comma-separated `GenerateContentResponse` objects + `]`,
  `Content-Type: application/json`.
- **FR-3 maxOutputTokens**: the mock truncates the completion to N tokens and sets
  `finish_reason=max_tokens` (mapped to Gemini `MAX_TOKENS`); otherwise `stop`/`STOP`.
- **FR-4 Persistence unchanged**: streaming still records one `request_logs` row after completion.

## 4. Design
- `routes._stream_response(request, provider, canonical, body, sse)` branches on `sse` for the two
  framings; both accumulate text/usage and call `record_request` at the end.
- `MockProvider.generate` applies `max_output_tokens` truncation; `stream_generate` propagates the
  resulting `finish_reason`.

## 5. Testing & Acceptance
- SSE response has `text/event-stream` and parseable `data:` events ending with `finishReason=STOP`.
- Default streaming body is a parseable JSON array of chunks.
- `maxOutputTokens=N` yields ≤N completion tokens and `finishReason=MAX_TOKENS`.
- Coverage gate stays green.

## 6. Dependencies & Risks
- Builds on FRD-100/103. Risk: SSE framing mismatch with the SDK → follow `data: … \n\n` exactly.
