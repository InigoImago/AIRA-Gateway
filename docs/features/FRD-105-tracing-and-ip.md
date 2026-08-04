# FRD-105 — Tracing enrichment: attribution & source IP on spans

> Phase: 1 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §5 (FR-GW-8/9), §10; `docs/ROADMAP.md` Phase 1; builds on FRD-001/102/103

## 1. Summary
Enrich the request span (from FRD-001 auto-instrumentation) with AIRA-specific attributes —
subject, use case, auth method, model, operation, status, source IP, token counts — so traces in
Grafana can be filtered by **who / which use case / which model**. Complements the persisted
`request_logs` (FRD-103) with the same dimensions on the live trace.

## 2. Goals & Non-Goals
**Goals**
- A `set_span_attributes(**attrs)` helper (skips `None`) on the current span; no-op when OTel is off.
- Set `aira.subject`, `aira.use_case`, `aira.auth_method` at attribution time.
- Set `aira.model`, `aira.operation`, `aira.status`, `aira.source_ip`, `aira.total_tokens` at record time.

**Non-Goals**
- Custom pipeline-step child spans (Phase 3 when the pipeline engine exists). Metrics dashboards.

## 3. Functional Requirements
- **FR-1 Helper**: `aira_common.observability.set_span_attributes(**attrs)` sets non-None attributes
  on `trace.get_current_span()`.
- **FR-2 Attribution attrs**: `require_attribution` sets subject/use_case/auth_method.
- **FR-3 Request attrs**: `record_request` sets model/operation/status/source_ip/total_tokens.
- **FR-4 Safety**: no secrets on spans (only subject id, use case, ip).

## 4. Testing & Acceptance
- `set_span_attributes` sets provided attributes and skips `None` (verified with an in-memory span
  exporter). Route calls exercise the helper (no-op span when OTel disabled).
- Coverage gate stays green; end-to-end: attributes visible on the gateway span in Grafana/collector.

## 5. Dependencies & Risks
- Builds on FRD-001/102/103. Low risk (attribute-only). Keep attribute keys namespaced under `aira.`.
