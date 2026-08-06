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
  statistics and cost/usage analytics. — **`FRD-601` done (2026-08-06)**: spend and usage
  reporting, scoped so governance sees every use case and everyone else sees their own.
- Global monitoring dashboards.
- Still open here: charts, export, per-request browsing (blocked on `FRD-406` redaction — see
  ADR-0009), and the read-only "processing logic" view of every use case.

FRDs: `FRD-600-governance-views`, `FRD-601-spend-and-usage-reporting`.

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

## Phase 8 — KIRA parity
**Goal:** AIRA carries every capability of the predecessor **KIA-KIRA-API** (`kira_api.md`), so that
it can be decommissioned.

A review on 2026-08-06 found the gap is not where it might be assumed. In breadth AIRA is well
ahead — the predecessor has no use cases, no budgets, no rate limits, no pipeline, no management
UI. In the **core request path** it is behind: the canonical message carries one field, `text`,
so documents and images are rejected at the door, thinking budgets do not exist, and structured
output does not exist. There is also a difference in *where* the model runs — the predecessor uses
Vertex AI on EU-regional endpoints with a service account; we use the global Generative Language
API with an API key.

**Confirmed 2026-08-06**: EU residency applies, and models are reached through the **Gemini
Enterprise platform's Model Garden** — **Gemini *and* Anthropic** under one project and one
credential. Two consequences: `FRD-115` is required rather than optional, and AIRA gains a
**second wire dialect** (`FRD-119`), because Anthropic models on Vertex speak the Anthropic
Messages API — required `max_tokens`, returned thinking blocks, no `responseSchema`. That is the
first real test of the canonical core's claim to be provider-agnostic.

**A third platform is wanted (2026-08-06): Microsoft Foundry** — Azure OpenAI plus Microsoft's own
models. Not urgent, but it is the platform that *decides* the shape of the upstream layer: with two
vendors any difference can be absorbed by a conditional, with three it has to be a structure.
`ADR-0011` records that structure — **transport × dialect × model identity** — and `FRD-120`
specifies Foundry against it. One planning consequence worth noting: the **OpenAI wire format
arrives as an upstream regardless of `FRD-106`**, so the deferred OpenAI *surface* becomes
materially cheaper once the dialect exists.

**Four model families, and documents are the thing that does not generalise.** Gemini, Claude, GPT
and (from Model Garden's self-deploy side) Nemotron. `ADR-0012` unifies them under one namespace and
one capability vocabulary — and settles the one place where unification would do harm: **Gemini and
Claude read PDFs natively, GPT and a NIM-hosted Nemotron cannot.** A fallback chain therefore may
not silently drop an attachment to reach a text-only model; it skips it, and if nothing qualifies the
request fails. Falling back would return a fluent, confident answer about a document the model never
saw, with a 200 — failing is recoverable, being quietly wrong is not. Model Garden's self-deployed
side also makes the transport × dialect grid a real **matrix** (the OpenAI dialect is needed on
Vertex, not only on Foundry) and introduces **cold starts and capacity-shaped 429s**, which the
dispatch timeout and the readiness probe must treat differently from quota.

`ADR-0010` frames the programme and holds the one open decision: whether AIRA also serves the
predecessor's **wire contract** (clients migrate by changing a URL) or whether the clients move to
the Gemini surface. Everything except `FRD-107` is needed either way and can start now.

| FRD | What | Blocking? |
|---|---|---|
| `FRD-110` | Documents and images in a request | the widest gap; everything else sits on it |
| `FRD-111` | Thinking modes and budgets | needs `FRD-114` |
| `FRD-112` | `responseSchema` — forced JSON output | — |
| `FRD-113` | Embedding task types, batches, dimensions | needs `FRD-114` |
| `FRD-114` | Model capability metadata | prerequisite for 111/112/113 |
| `FRD-115` | Vertex AI / Model Garden in the EU | **required — residency confirmed** |
| `FRD-119` | Anthropic models on Vertex: the second dialect | needs `FRD-115` + `FRD-114` |
| `FRD-120` | Microsoft Foundry (Azure OpenAI + Microsoft's own) | **planned, not scheduled** — see `ADR-0011` |
| `FRD-121` | Document conversion for models that cannot read documents | **optional — probably do not build first**, see `ADR-0012` §4 |
| `FRD-116` | Secrets actually read from Vault | policy and implementation have been apart since Phase 0 |
| `FRD-117` | Version info, upstream health, CORS, OpenAPI 3.0, trace header | independent; makes the rest operable |
| `FRD-118` | Several Keycloak backends, groups from UserInfo | **requirement unconfirmed — see its §11** |
| `FRD-602` | CSV export of the usage report | follows `FRD-601` ✓ |
| `FRD-107` | The KIRA wire format itself | **blocked on `ADR-0010`** |

**Out of scope for now:** the OpenAI-compatible surface (`FRD-106`) — deferred by decision on
2026-08-06 so the parity programme is not competing with a second new contract.

---

## Backlog — agreed, not yet scheduled

Work that is decided but deliberately not in the current phase. Ordered as agreed with the
product owner; the reason for the order is recorded so it is not re-litigated later.

| Item | FRD | Why it waits |
|---|---|---|
| ~~Per-caller rate limiting + the budget guard/record race~~ | `FRD-405` | **Done (2026-08-05).** Token buckets and atomic budget reservations over Redis (ADR-0008); the audit write also moved off the request path. |
| **Content redaction** — masking sensitive values *inside* a stored payload | `FRD-406` | **Deferred by decision (2026-08-05), to be done later.** The `Redactor` hook is still a `NoOpRedactor`. Two mitigations already exist: a per-use-case retention period (7 days, FRD-404) and switching payload storage off entirely. Neither masks anything in a payload that *is* kept — redaction remains genuinely open, it is only not urgent. |
| ~~Spend and usage reporting~~ | `FRD-601` | **Done (2026-08-06).** Gateway `GET /v1beta/reporting` + a **Reporting** screen: totals and breakdowns by use case, model and member over a chosen period, scoped by the caller's role (ADR-0009). Per-request *browsing* still waits for `FRD-406`. |
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
