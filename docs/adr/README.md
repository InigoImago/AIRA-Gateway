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
| 0002 | [Local observability backend: OpenTelemetry Collector + SigNoz](ADR-0002-observability-signoz.md) | Accepted | 2026-08-04 |
| 0003 | [Toolchain & runtime versions (Python 3.14 + uv, Node 26)](ADR-0003-toolchain-versions.md) | Accepted | 2026-08-04 |
