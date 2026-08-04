# FRD-001 — Observability Baseline

> Phase: 0 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §6 (NFR-3), §10; `docs/ROADMAP.md` Phase 0; `ADR-0004` (supersedes ADR-0002)

## 1. Summary
Wire end-to-end observability from day one: both components emit **traces, metrics, and logs** via
**OTLP** to a standalone **OpenTelemetry Collector**, which forwards to a local **Grafana
`otel-lgtm`** backend (Grafana + Loki + Tempo + Prometheus in one container) running in Docker
Compose. Applications talk only to the Collector, keeping the backend swappable. This establishes
correlation IDs / trace context propagation across the two components and Kafka, so every later
feature is observable by default.

## 2. Goals & Non-Goals
**Goals**
- Run OTel Collector + Grafana `otel-lgtm` locally via Compose.
- Instrument gateway (FastAPI) and management (Django) to export OTLP traces/metrics/logs.
- Propagate trace context across HTTP and Kafka (W3C `traceparent`).
- Standard resource attributes (service.name, version, environment) and a baseline sampling policy.
- A "hello telemetry" trace/metric/log visible in Grafana proves the pipeline.

**Non-Goals**
- Business-specific spans/metrics (added by each feature FRD).
- Alerting rules for anomalies (Phase 5) — only the plumbing here.
- Production-grade observability backend (`otel-lgtm` is dev-only) — revisited in Phase 7.

## 3. User Stories
- As a **developer**, I want to see traces/metrics/logs of local requests in Grafana so I can debug
  and validate behavior.
- As an **operator (future)**, I want consistent resource attributes and trace propagation so I can
  follow a request across components.

## 4. Functional Requirements
- **FR-1 Compose services**: add `otel-collector` and `otel-lgtm` to the Compose stack (under an
  `observability` profile), on the shared network, with the Collector's OTLP ports published to the
  host and Grafana's UI on port 3000.
- **FR-2 Collector config**: OTLP gRPC/HTTP receivers; batch + memory-limiter + resource processors;
  OTLP exporter to `otel-lgtm` (plus a debug exporter). Config in
  `deploy/compose/otel/collector-config.yaml`.
- **FR-3 App instrumentation**:
  - Gateway (FastAPI): OTel SDK + FastAPI/httpx/logging instrumentation; OTLP exporter → Collector.
  - Management (Django): OTel SDK + Django/logging instrumentation; OTLP exporter → Collector.
  - Endpoint configurable via settings (`otel_endpoint`), pointing to the Collector.
- **FR-4 Resource attributes**: `service.name` (`aira-gateway`, `aira-management`),
  `service.version`, `deployment.environment` (`local`).
- **FR-5 Context propagation**: W3C Trace Context over HTTP; inject/extract `traceparent` on **Kafka**
  message headers (producer injects, consumer extracts) so async flows stay linked.
- **FR-6 Correlation in logs**: structured logs carry `trace_id`/`span_id`; logs shipped via OTLP.
- **FR-7 Sampling**: parent-based, ratio sampler; ratio configurable (default 1.0 locally).
- **FR-8 Metrics baseline**: request latency/count/error metrics auto-exported for both services.
- **FR-9 Toggle**: observability is enabled via `otel_enabled`; when off (or endpoint empty) the SDK
  setup is a no-op so tests and low-resource machines are unaffected.

## 5. Design & Architecture
```
 aira-gateway ─┐                          ┌── traces (Tempo)
 aira-mgmt    ─┤ OTLP → otel-collector →  ┤── metrics (Prometheus)  → Grafana UI (:3000)
 (Kafka hdrs) ─┘   (receivers/processors) └── logs (Loki)
                                          (grafana/otel-lgtm)
```
- **Backend-agnostic**: only the Collector's exporter targets `otel-lgtm`; swapping to a full
  LGTM/Grafana Cloud or another OTLP backend is a Collector-config change (per ADR-0004), no app
  changes.
- **Shared bootstrap in `libs/`**: `aira_common.observability` configures tracer/meter/logger
  providers, the OTLP exporters, resource attributes, propagators, and a structlog processor that
  injects trace context into logs. Both Python services call it at startup.
- **Kafka propagation helpers** in `aira_common` inject/extract W3C context to/from message headers.

## 6. Data Model
- None (telemetry is stored inside `otel-lgtm`'s bundled stores, out of scope for our schema).

## 7. API / Interface Contract
- No public API. Internal contract: OTLP to `otel-collector:4317/4318`; Kafka message headers include
  `traceparent` (and `tracestate` when present).

## 8. Security & Privacy
- Local only; Collector/Grafana not exposed publicly. Be mindful not to put sensitive payloads in
  span attributes/logs — establish a redaction guideline now (enforced with data handling later).

## 9. Observability
- This FRD *is* the observability baseline. Deliverable proof: a sample request produces a trace
  spanning the handler and a log line — visible and correlated in Grafana; service metrics appear;
  a Kafka-propagated trace links producer→consumer.

## 10. Testing & Acceptance Criteria
- **Tests**: unit tests for the shared `observability` bootstrap (resource attrs, endpoint wiring,
  no-op when disabled, structlog trace-context processor) using in-memory span exporters; a test
  asserting Kafka header inject/extract round-trips trace context. Coverage gate stays green.
- **Acceptance**:
  - **Given** the stack is up, **when** I hit a gateway/management endpoint, **then** a corresponding
    trace appears in Grafana (Tempo) with the correct `service.name`.
  - **When** a message is produced and consumed over Kafka, **then** the consumer span shares the
    producer's trace id.
  - **When** I view logs, **then** they include `trace_id`/`span_id` correlation.

## 11. Dependencies & Risks
- Depends on `FRD-000` (Compose stack, shared libs).
- Risk: `otel-lgtm` resource footprint locally → keep it under the `observability` Compose profile so
  it is opt-in; the `otel_enabled` toggle lets apps run without it.
- Risk: instrumentation version drift → pin OTel package versions.

## 12. Rollout / Demo
- Demo: `make up`, generate a request, open Grafana (`http://localhost:3000`), show the correlated
  trace + metrics + logs.
- Seed traffic from `FRD-002` will make dashboards populated out of the box in demo mode.
