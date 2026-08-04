# FRD-103 — Request/response persistence

> Phase: 1 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §5 (FR-GW-7), §9; `docs/ROADMAP.md` Phase 1; builds on FRD-100/101/102

## 1. Summary
Persist every dispatched request and its response, together with **attribution** (subject, auth
method, use case), **source IP**, model, token usage, status, latency, and **trace id**, into the
gateway's Postgres DB. Payloads pass through a **redaction hook** before storage. This realizes the
"store as much as possible" goal and is the basis for later analytics, budgets, and anomaly
detection. This FRD also introduces **Alembic** migrations for the gateway schema (previously
`create_all`).

## 2. Goals & Non-Goals
**Goals**
- A `request_logs` table capturing metadata + (optionally redacted) request/response payloads.
- Record on every successful dispatch (generateContent / streamGenerateContent / embedContent).
- Capture **source IP** (respecting `X-Forwarded-For`), **trace id**, latency, token usage.
- A pluggable **`Redactor`** hook (default no-op) and a `store_payloads` toggle.
- **Alembic** migrations for the gateway DB (`api_keys` + `request_logs`).

**Non-Goals**
- Persisting auth/validation *rejections* (4xx before dispatch) — audit of those flows via Kafka
  events later (FR-GW-12). Guaranteed/async delivery via Kafka is also later; Phase 1 writes inline.
- Retention/TTL jobs, PII classification rules (the hook is here; real rules later).

## 3. Functional Requirements
- **FR-1 Model**: `request_logs(id, created_at, subject, auth_method, use_case, source_ip, api,
  operation, model, status, prompt_tokens, completion_tokens, total_tokens, latency_ms, trace_id,
  request_payload, response_payload)`.
- **FR-2 Record on dispatch**: after a successful generate/stream/embed, write one row with the
  attribution from `request.state`, timing, usage, and payloads.
- **FR-3 Source IP**: first `X-Forwarded-For` hop if present, else the socket peer.
- **FR-4 Redaction hook**: `Redactor.redact(payload)` applied to request+response payloads before
  storage; default `NoOpRedactor`. `store_payloads=false` stores metadata only (payloads null).
- **FR-5 Trace correlation**: store the current `trace_id` (from the OTel span, FRD-001).
- **FR-6 Streaming**: record after the stream completes, with the assembled text + final usage.
- **FR-7 Migrations**: Alembic manages the gateway schema; `make migrate-gateway` applies it. Dev/
  tests still use `create_all` (in-memory SQLite / bootstrap).

## 4. Design & Architecture
```
route → provider dispatch → build response
      → record_request(request, operation, model, status, usage, latency, payloads)
          reads request.state.attribution, applies Redactor, writes RequestLog (async session)
```
- `persistence/` package: `service.RequestLogService`, `redaction.{Redactor,NoOpRedactor}`,
  `recorder.record_request` + `client_ip`. The routes call `record_request` after producing output.
- Inline await for reliability in Phase 1 (the gateway already depends on its Postgres for the
  API-key read-model); async/Kafka-buffered delivery is a later optimization.

## 5. Data Model
See FR-1. `request_payload`/`response_payload` are JSON columns (JSONB on Postgres).

## 6. Security & Privacy
- Redaction hook + `store_payloads` toggle allow suppressing sensitive content. Only subject id and
  use case go to logs/spans; never secrets. Retention controls come later.

## 7. Observability
- Each record carries `trace_id` linking it to the Grafana trace (FRD-001). Optional metric: stored
  request counter by operation/use case.

## 8. Testing & Acceptance Criteria
- **Tests** (hermetic, SQLite): service writes all fields; usage-None (embed) handled; `client_ip`
  precedence (XFF vs peer); redaction applied / `store_payloads=false` nulls payloads; route
  integration (generate/embed/stream) creates a correctly-attributed row. Coverage gate stays green.
- **Acceptance**:
  - **Given** an authenticated `:generateContent` call, **then** a `request_logs` row exists with the
    caller's subject/use_case, the model, token usage, latency, trace id, and payloads.
  - **When** `store_payloads=false`, **then** payload columns are null but metadata is present.
  - **When** streaming, **then** a row is written after the stream with the assembled text.

## 9. Dependencies & Risks
- Builds on FRD-100/101/102. New deps: Alembic. Risk: inline write adds latency → acceptable in
  Phase 1; revisit with Kafka buffering. Risk: create_all vs Alembic drift → Alembic is the prod
  source of truth; create_all is dev/test only, run on a fresh DB.
