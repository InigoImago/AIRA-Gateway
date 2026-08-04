# ADR-0002 — Local observability backend: OpenTelemetry Collector + SigNoz

- **Status:** Superseded by [ADR-0004](ADR-0004-observability-grafana-otel-lgtm.md)
- **Date:** 2026-08-04
- **Deciders:** Vadim Scheibe

> **Superseded (2026-08-04):** SigNoz deprecated its Docker Compose manifests in favor of its
> "Foundry" installer, so it can no longer be embedded cleanly in our Compose stack. We switched to
> Grafana `otel-lgtm` (the documented alternative below). See ADR-0004.

## Context
Both components will emit OpenTelemetry traces, metrics, and logs. We need a local, open-source
backend running on developer hardware (Docker Compose) to receive and visualize this telemetry from
Phase 0 onward.

## Options considered
- **OTel Collector → SigNoz** — single self-hosted, OTLP-native system unifying traces + metrics +
  logs with dashboards and alerting. One system to run and learn.
- **Grafana LGTM stack** (Grafana + Loki + Tempo + Mimir/Prometheus + OTel Collector) — very much
  the industry standard and highly modular, but more moving parts to operate locally.
- **Jaeger + Prometheus + Grafana assembled manually** — flexible but the most assembly/maintenance.

## Decision
Use an **OpenTelemetry Collector** as the ingestion point (apps export OTLP to it) forwarding to a
local **SigNoz** backend. Applications never talk to the backend directly — only to the Collector —
so the backend can be swapped without touching app code.

## Consequences
- Positive: one unified local system for traces/metrics/logs; OTLP-native; low setup friction;
  Collector indirection keeps us backend-agnostic.
- Negative / trade-offs: SigNoz is less ubiquitous in enterprises than Grafana; if the org later
  standardizes on Grafana, we switch the Collector's exporter to the **LGTM** stack (documented
  alternative) — app instrumentation is unaffected.
- Follow-ups: define Collector pipelines, resource attributes, and sampling in
  `FRD-001-observability-baseline`.
