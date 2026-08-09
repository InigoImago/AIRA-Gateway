# Architecture Decision Records (ADRs)

This directory records significant technical/architectural decisions for AIRA Gateway.
Each ADR is immutable once **Accepted** — to change a decision, add a new ADR that supersedes it.

## Convention
- Copy `ADR-TEMPLATE.md`, name it `ADR-NNNN-short-slug.md` (next free number).
- Status: `Proposed` → `Accepted` / `Rejected` / `Superseded by ADR-XXXX`.
- Link every new ADR in the index below.

## Index
| # | Title | Status | Date |
|--:|-------|--------|------|
| 0001 | [Management UI: Angular + Django REST Framework](ADR-0001-management-ui-angular-drf.md) | Accepted | 2026-08-04 |
| 0002 | [Local observability backend: OpenTelemetry Collector + SigNoz](ADR-0002-observability-signoz.md) | Superseded by 0004 | 2026-08-04 |
| 0003 | [Toolchain & runtime versions (Python 3.14 + uv, Node 26)](ADR-0003-toolchain-versions.md) | Accepted | 2026-08-04 |
| 0004 | [Local observability backend: Grafana otel-lgtm](ADR-0004-observability-grafana-otel-lgtm.md) | Accepted | 2026-08-04 |
| 0005 | [Gemini-compatible API surface first (OpenAI later)](ADR-0005-gemini-compatible-api-first.md) | Accepted | 2026-08-04 |
| 0006 | [API key lifecycle: issued by Management, validated by Gateway (Kafka)](ADR-0006-api-key-lifecycle-split.md) | Accepted | 2026-08-04 |
| 0007 | [Security hardening baseline (authorization boundaries, safe defaults, input bounds)](ADR-0007-security-hardening-baseline.md) | Accepted | 2026-08-04 |
| 0008 | [Redis as the shared counter store for rate limits and budget reservations](ADR-0008-redis-shared-counters.md) | Accepted | 2026-08-05 |
| 0009 | [The gateway learns realm roles, from one shared definition](ADR-0009-gateway-knows-roles.md) | Accepted | 2026-08-06 |
| 0010 | [KIRA parity: bring the contract along, or move the clients?](ADR-0010-kira-parity-and-the-api-contract.md) | Accepted (Option C) | 2026-08-06 |
| 0011 | [Upstreams: platform, dialect, and what a model name means](ADR-0011-upstreams-platform-dialect-identity.md) | Accepted | 2026-08-06 |
| 0012 | [One catalog over many platforms, and what "supports documents" then means](ADR-0012-one-catalog-many-platforms.md) | Accepted | 2026-08-06 |
| 0013 | [The gateway provides auditable model access, not agents](ADR-0013-auditable-model-access-not-agents.md) | Accepted | 2026-08-06 |
| 0014 | [Detection is asynchronous; enforcement is not](ADR-0014-detection-is-asynchronous-enforcement-is-not.md) | Accepted | 2026-08-07 |
| 0015 | [A convenience default is a production default](ADR-0015-a-convenience-default-is-a-production-default.md) | Accepted | 2026-08-08 |
| 0016 | [Stored prompts are readable, and every read is recorded](ADR-0016-content-is-readable-and-every-read-is-recorded.md) | Accepted | 2026-08-09 |
