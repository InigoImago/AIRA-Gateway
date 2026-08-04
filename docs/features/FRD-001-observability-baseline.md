# FRD-001 — Observability Baseline

> Phase: 0 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §6 (NFR-3), §10; `docs/ROADMAP.md` Phase 0; `ADR-0002`

## 1. Summary
Wire end-to-end observability from day one: both components emit **traces, metrics, and logs** via
**OTLP** to an **OpenTelemetry Collector**, which forwards to a local **SigNoz** backend running in
Docker Compose. Applications talk only to the Collector, keeping the backend swappable. This
establishes correlation IDs / trace context propagation across the (eventually) two components and
Kafka, so every later feature is observable by default.

## 2. Goals & Non-Goals
**Goals**
- Run OTel Collector + SigNoz locally via Compose.
- Instrument gateway (FastAPI) and management (Django) to export OTLP traces/metrics/logs.
- Propagate trace context across HTTP and Kafka (W3C `traceparent`).
- Standard resource attributes (service.name, version, environment) and a baseline sampling policy.
- A "hello telemetry" trace/metric/log visible in SigNoz proves the pipeline.

**Non-Goals**
- Business-specific spans/metrics (added by each feature FRD).
- Alerting rules for anomalies (Phase 5) — only the plumbing here.
- Production-grade retention/scaling of SigNoz (Phase 7).

## 3. User Stories
- As a **developer**, I want to see traces/metrics/logs of local requests in SigNoz so I can debug
  and validate behavior.
- As an **operator (future)**, I want consistent resource attributes and trace propagation so I can
  follow a request across components.

## 4. Functional Requirements
- **FR-1 Compose services**: add `otel-collector` and `signoz` (its required sub-services) to the
  Compose stack, on the shared network, with healthchecks.
- **FR-2 Collector config**: OTLP gRPC/HTTP receivers; batch + memory-limiter processors; resource
  processor; exporter to SigNoz. Config lives in `deploy/compose/otel/collector-config.yaml`.
- **FR-3 App instrumentation**:
  - Gateway (FastAPI): OTel SDK + auto-instrumentation (ASGI, httpx, logging); OTLP exporter → Collector.
  - Management (Django): OTel SDK + Django/DB/logging instrumentation; OTLP exporter → Collector.
  - Endpoint configurable via `OTEL_EXPORTER_OTLP_ENDPOINT` (points to the Collector).
- **FR-4 Resource attributes**: `service.name` (`aira-gateway`, `aira-management`),
  `service.version`, `deployment.environment` (`local`), instance id.
- **FR-5 Context propagation**: W3C Trace Context over HTTP; inject/extract `traceparent` on **Kafka**
  message headers (producer injects, consumer extracts) so async flows stay linked.
- **FR-6 Correlation in logs**: structured logs carry `trace_id`/`span_id`; logs shipped via OTLP.
- **FR-7 Sampling**: parent-based, ratio sampler; ratio configurable (default 1.0 locally).
- **FR-8 Metrics baseline**: request latency/count/error metrics auto-exported for both services.

## 5. Design & Architecture
```
 aira-gateway ─┐                         ┌── traces
 aira-mgmt    ─┤ OTLP → OTel Collector →  ┤── metrics  → SigNoz UI
 (Kafka hdrs) ─┘   (receivers/processors) └── logs
```
- **Backend-agnostic**: only the Collector's exporter knows about SigNoz; swapping to Grafana LGTM
  is a Collector-config change (per ADR-0002), no app changes.
- **Bootstrap in `libs/`**: a shared `otel` bootstrap module used by both Python services (single
  place to configure exporter, resource, propagators). Extends the FRD-000 no-op bootstrap.

## 6. Data Model
- None (telemetry is stored inside SigNoz's own datastore, out of scope for our schema).

## 7. API / Interface Contract
- No public API. Internal contract: OTLP to `otel-collector:4317/4318`; Kafka message headers include
  `traceparent` (and `tracestate` when present).

## 8. Security & Privacy
- Local only; Collector not exposed publicly. Be mindful not to put sensitive payloads in span
  attributes/logs — establish a redaction guideline now (enforced with data handling in later FRDs).

## 9. Observability
- This FRD *is* the observability baseline. Deliverable proof: a sample request produces a trace
  spanning the FastAPI handler, an outgoing httpx call, and a log line — all visible and correlated
  in SigNoz; service metrics appear; a Kafka-propagated trace links producer→consumer.

## 10. Testing & Acceptance Criteria
- **Tests**: unit tests for the shared `otel` bootstrap (correct resource attrs, exporter endpoint
  wiring, propagator setup) using in-memory span exporters; a test asserting Kafka header
  inject/extract round-trips trace context. Keep coverage gate green.
- **Acceptance**:
  - **Given** the stack is up, **when** I hit a gateway health/demo endpoint, **then** a
    corresponding trace appears in SigNoz with correct `service.name`.
  - **When** a message is produced and consumed over Kafka, **then** the consumer span shares the
    producer's trace id.
  - **When** I view logs in SigNoz, **then** they include `trace_id`/`span_id` correlation.

## 11. Dependencies & Risks
- Depends on `FRD-000` (Compose stack, shared libs, OTel no-op bootstrap).
- Risk: SigNoz resource footprint locally → document minimal config; allow disabling via env for
  low-resource machines (fallback to console exporter).
- Risk: instrumentation version drift → pin OTel package versions.

## 12. Rollout / Demo
- Demo: `make up`, generate a request, open SigNoz, show the correlated trace + metrics + logs.
- Seed traffic from `FRD-002` will make dashboards populated out of the box in demo mode.
