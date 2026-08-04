# ADR-0004 — Local observability backend: Grafana otel-lgtm (supersedes ADR-0002)

- **Status:** Accepted
- **Date:** 2026-08-04
- **Deciders:** Vadim Scheibe
- **Supersedes:** ADR-0002 (SigNoz)

## Context
ADR-0002 chose **SigNoz** as the local OTLP backend. While implementing FRD-001 we found that
**SigNoz has deprecated its Docker Compose manifests** and now installs/runs only through its own
tool ("Foundry"). Embedding SigNoz as a few services in our own Compose stack is no longer a
supported path — we would have to pin a deprecated, unmaintained compose (ClickHouse + collector +
UI, ~5 containers), which is fragile and high-maintenance.

The application-side instrumentation (apps export OTLP to an OpenTelemetry Collector) is unaffected
by this choice — only the backend behind the collector changes.

## Options considered
- **Grafana `otel-lgtm`** — a single container bundling Grafana (UI) + Loki (logs) + Tempo (traces)
  + Prometheus (metrics) + a built-in OTel Collector. Purpose-built by Grafana for local OTLP
  development. One image, OTLP-native. Was already the documented alternative in ADR-0002.
- **SigNoz via pinned legacy compose** — keep SigNoz but on deprecated, unmaintained manifests.
  Heavier (~5 containers) and rots over time.
- **OTel Collector + Jaeger + Prometheus (+ Grafana)** — the modular classic. Very established but
  more containers and wiring to maintain.

## Decision
Use **Grafana `otel-lgtm`** as the local observability backend. Keep a **standalone OpenTelemetry
Collector** as the ingestion point that applications export to (honoring the FRD-001 requirement and
the ADR-0002 indirection principle); the collector forwards OTLP to `otel-lgtm`. Applications only
ever talk to the collector.

```
apps ─OTLP→ otel-collector ─OTLP→ grafana/otel-lgtm ─→ Grafana UI (traces+metrics+logs)
```

## Consequences
- Positive: single-container backend, trivial to run locally; OTLP-native; Grafana is the de-facto
  enterprise standard, so this doubles as a smoother path toward a real LGTM/Grafana Cloud backend
  later. The standalone collector keeps us backend-agnostic (swap the exporter, not app code).
- Negative / trade-offs: `otel-lgtm` is explicitly a **local/dev** tool — not for production (no
  persistence guarantees, single-node). Production observability is revisited in Phase 7. We diverge
  from the earlier SigNoz decision, but the app instrumentation is identical, so the switch is cheap.
- Follow-ups: pin image tags in `deploy/compose`; production backend decided in Phase 7.
