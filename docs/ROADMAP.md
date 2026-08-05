# AIRA Gateway — Delivery Roadmap (Phases)

> Status: **Draft v0.1** · Last updated: 2026-08-04
> Companion to `docs/PRD.md`. Each phase lists its goals, deliverables, and the FRDs it contains.

Guiding principles:
- **Vertical slices**: each phase ends with something runnable and demoable via `docker compose up`.
- **Test-first**: every phase carries its own unit/integration tests; coverage gates from Phase 0.
- **Demo mode always works**: the mock upstream + seed data are maintained across all phases.
- **Kafka contract early**: the event schema is established before both components depend on it.

---

## Phase 0 — Foundation & Infrastructure
**Goal:** A reproducible local platform and engineering baseline everything else builds on.

Deliverables:
- Monorepo layout (`gateway/`, `management/backend/`, `management/frontend/`, `deploy/`, `docs/`).
- Docker Compose stack: PostgreSQL, Keycloak, Kafka (+ schema registry), Vault (dev mode).
- **Local observability backend**: OpenTelemetry Collector + **Grafana otel-lgtm** (traces +
  metrics + logs), both components wired to export via OTLP from day one (ADR-0004).
- CI pipeline with lint + type-check + tests + **coverage gate**.
- Shared conventions: logging, config, error handling, OpenTelemetry bootstrap.
- **Seed framework**: one command to load test data (extended each phase).

FRDs: `FRD-000-foundation-infra`, `FRD-001-observability-baseline`, `FRD-002-seed-and-demo-mode`.

---

## Phase 1 — Gateway MVP (data plane)
**Goal:** A working unified API that authenticates, attributes, dispatches to a mock upstream, and
persists everything.

Deliverables:
- **Gemini-compatible** endpoints (`/v1beta/models/{model}:generateContent`, `:streamGenerateContent`,
  `:embedContent`, `GET /v1beta/models`) → canonical internal schema. **This ships first** (existing
  projects run on Gemini); the **OpenAI-compatible** surface is added later as `FRD-106` (ADR-0005).
- **API-key auth** (issue/hash/verify/revoke) + **OIDC bearer** validation against Keycloak.
- Request attribution to user / project / use case.
- **Mock upstream** provider; request/response **persistence**; source-IP capture; tracing spans.
- Kafka usage/audit event emission (schema v1).

FRDs: `FRD-100-gemini-api`, `FRD-101-auth-apikey-oidc`, `FRD-102-attribution`,
`FRD-103-request-response-persistence`, `FRD-104-mock-upstream`, `FRD-105-tracing-and-ip`.
Later: `FRD-106-openai-api` (OpenAI-compatible surface on the same canonical schema).

---

## Phase 2 — Management Foundation (control plane)
**Goal:** The management app exists with SSO, RBAC, and use-case CRUD; Angular shell in place.

Deliverables:
- Django + DRF backend; Keycloak SSO (OIDC); the 5 roles as groups + object-level perms (guardian).
- **Use-case self-service**: create/edit use cases, assign members, membership → access.
- Angular SPA shell: login (OIDC), role-aware navigation, use-case list/detail.
- Config event schema (Management → Gateway) v1 over Kafka; Gateway read-model skeleton.

FRDs: `FRD-200-mgmt-backend-foundation`, `FRD-201-keycloak-rbac`, `FRD-202-usecase-crud`,
`FRD-203-angular-shell`, `FRD-204-config-distribution-kafka`,
`FRD-205-api-key-issuance` (self-service API-key issuance in Management + `api_key.*` Kafka events →
Gateway read-model; the Gateway keeps validation only — see ADR-0006).

---

## Phase 3 — Routing, Pipeline & Fallback
**Goal:** Use-case-driven pre-dispatch pipeline with sequential/parallel execution and fallback,
configured in the UI and enforced by the gateway.

Deliverables:
- Pipeline engine in the gateway: ordered steps, **sequential/parallel** branches, injection-filter
  and allow-check step types, **model routing** + cost-based **rerouting**, **fallback** chains.
- **Pipeline builder** UI (Angular) — ordered/parallel steps + fallback editing.
- First **real upstream adapters** (Gemini Enterprise, Microsoft Foundry) behind the mock-compatible
  interface.

FRDs: `FRD-300-pipeline-engine`, `FRD-301-routing-and-rerouting`, `FRD-302-model-fallback`,
`FRD-303-pipeline-builder-ui`, `FRD-304-upstream-adapters`.

---

## Phase 4 — Governance: Budgets & Quotas
**Goal:** Cost/usage control per use case and per member, enforced and visible.

Deliverables:
- **Budget** definitions (per use case / per member), usage accounting, enforcement in the gateway.
- UI for setting budgets and viewing consumption; alerts on threshold breach.

FRDs: `FRD-400-budget-model`, `FRD-401-budget-enforcement`, `FRD-402-budget-ui`.

---

## Phase 5 — Anomaly Detection & IT Security
**Goal:** Self-service anomaly rules + IT Security console with incident response.

Deliverables:
- **Anomaly rules** self-service: rate-based, size-based, data-leak detection.
- Detection pipeline (gateway signals → Kafka → evaluation) and **AnomalyEvent** store.
- **IT Security console**: cross-use-case anomaly overview, **mark/block** requests, scoped
  visibility (payload redaction), dedicated IT Security role.
- **Incident response** engine: configurable **throttle / alert / block** per rule.

FRDs: `FRD-500-anomaly-rules`, `FRD-501-anomaly-detection-engine`, `FRD-502-it-security-console`,
`FRD-503-incident-response`.

---

## Phase 6 — IT Steuerung (Governance) & Analytics
**Goal:** Oversight and aggregate reporting for governance.

Deliverables:
- **IT Steuerung views**: all use cases with descriptions + processing logic (read-only), aggregate
  statistics and cost/usage analytics.
- Global monitoring dashboards.

FRDs: `FRD-600-governance-views`, `FRD-601-analytics-dashboards`.

---

## Phase 7 — Hardening & Production Readiness
**Goal:** Enterprise polish and a path off the developer machine.

Deliverables:
- Security hardening & pen-test fixes; retention/redaction policies finalized.
- Performance/load testing; HA considerations.
- **Kubernetes/Helm** charts; secrets via Vault in-cluster; production observability stack.
- Coverage/quality audit against the near-100% target.

FRDs: `FRD-700-hardening`, `FRD-701-k8s-helm`, `FRD-702-perf-and-ha`.

---

## Backlog — agreed, not yet scheduled

Work that is decided but deliberately not in the current phase. Ordered as agreed with the
product owner; the reason for the order is recorded so it is not re-litigated later.

| Item | FRD | Why it waits |
|---|---|---|
| ~~Per-caller rate limiting + the budget guard/record race~~ | `FRD-405` | **Done (2026-08-05).** Token buckets and atomic budget reservations over Redis (ADR-0008); the audit write also moved off the request path. |
| **Content redaction** — masking sensitive values *inside* a stored payload | `FRD-406` | **Deferred by decision (2026-08-05), to be done later.** The `Redactor` hook is still a `NoOpRedactor`. Two mitigations already exist: a per-use-case retention period (7 days, FRD-404) and switching payload storage off entirely. Neither masks anything in a payload that *is* kept — redaction remains genuinely open, it is only not urgent. |
| Request-log and spend reporting UI | `FRD-601` | Data is recorded (incl. per-request cost); nothing displays it beyond the budget bars. |
| Budget threshold alerting | `FRD-402` follow-up | Today a breach is a 429 and nothing else — nobody is told before the wall is hit. |
| Membership reconciliation (Keycloak groups ↔ Management) | — | The two sources can drift; nothing detects it. |
| Pagination | — | No list endpoint or screen paginates. |
| ~~Read-model tombstones~~ | — | **Done (2026-08-05).** Deleting a use case left its keys authenticating and its budgets, limits and pipeline in force; the tombstone now cascades and migration 0011 cleared what earlier deletions had left. |

---

## Phase → FRD index (summary)

| Phase | Theme | FRDs |
|------:|-------|------|
| 0 | Foundation & Infra | 000, 001, 002 |
| 1 | Gateway MVP | 100–105 |
| 2 | Management Foundation | 200–205 |
| 3 | Routing/Pipeline/Fallback | 300–304 |
| 4 | Budgets & Quotas | 400–404 |
| 5 | Anomaly & IT Security | 500–503 |
| 6 | Governance & Analytics | 600–601 |
| 7 | Hardening & Prod | 700–702 |

> Phases are sequential in dependency but the roadmap allows overlap where safe (e.g. Angular shell
> work in Phase 2 can begin while Phase 1 stabilizes).
