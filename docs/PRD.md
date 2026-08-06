# AIRA Gateway — Project Requirements Document (PRD)

> **AIRA** = **AI R**est **A**PI Gateway
> Status: **Draft v0.1** · Owner: Vadim Scheibe · Last updated: 2026-08-04

---

## 1. Purpose & Vision

AIRA Gateway is an **enterprise-grade AI gateway** that provides a single, unified,
provider-agnostic REST API in front of multiple upstream LLM platforms. It centralizes
**authentication, authorization, routing, governance, observability, and security** for all
AI traffic in the organization.

The core idea: every AI request in the company flows through AIRA. AIRA knows **who** is asking
(user / project / use case), **what** they are asking, **which model** should serve it (routing,
fallback, cost optimization), **whether it is allowed** (policy, prompt-injection, budget), and
records **everything** (request + response persistence, tracing, audit) so that operations and
IT security have full visibility and control.

### Vision statement
> A governed, observable, and secure single point of entry for all enterprise AI usage — with
> self-service for teams and strong central control for IT security and governance.

### Scope, in one sentence (ADR-0013)
> The gateway's job is to provide **auditable brains** for AI use cases.

Auditable is the operative word and the differentiator: not merely access to models, but access
that is attributed, bounded, priced, and evidenced — who asked, whether it was allowed, which brain
answered and where it ran, what it cost, and what happened.

The sentence is also a boundary. The gateway does **not** think for the use case: no agent
surfaces, no retrieval or vector storage, no conversation state, no tool execution, no workflow
orchestration. The test for any future request is in `ADR-0013` — *does this make model access
better governed and better evidenced, or does it make the gateway think for the use case?*

---

## 2. Goals & Non-Goals

### 2.1 Goals
- **G1 — Unified API surface**: A single provider-agnostic REST interface for all clients. The
  **Gemini-compatible** surface ships **first** (existing projects already run on it); an
  **OpenAI-compatible** surface is added later. Both normalize to one canonical internal schema.
- **G2 — Strong AuthN/AuthZ**: SSO via bearer token (OIDC/Keycloak) *and* self-generated API keys.
- **G3 — Attribution**: Every request is attributed to a user, a project, a use case, or a
  user-within-a-use-case.
- **G4 — Intelligent routing**: Model rerouting (route "easy" requests to cheaper models),
  sequential/parallel model execution, and model fallback.
- **G5 — Full persistence**: Store requests and responses (store as much as possible), with tracing
  and source-IP capture.
- **G6 — Self-service governance**: Use-case owners configure their own pre-dispatch pipeline,
  budgets, and anomaly rules through the management UI.
- **G7 — Security & incident response**: IT Security gets cross-use-case anomaly visibility, can
  mark/block requests, and configure incident response (throttle / alert / block).
- **G8 — Governance oversight**: IT Steuerung (IT Governance) sees all use cases, their processing
  logic, and aggregate statistics.
- **G9 — Enterprise quality**: Full unit-test coverage, strong observability, secrets in Vault,
  event-driven integration via Kafka.
- **G10 — Demo mode**: The whole system can be run and demonstrated without real upstream
  credentials, with automatically seeded test data.

### 2.2 Non-Goals (for now)
- Not building our own LLM inference — AIRA orchestrates upstream providers only.
- Not a general-purpose API management product — scope is AI/LLM traffic.
- No fine-tuning / training pipelines.
- Multi-region active-active HA is out of scope for the initial phases (single-cluster first).

---

## 3. Stakeholders & Roles

Five initial roles (mapped later to Keycloak groups + Django permissions, object-level via
`django-guardian`):

| Role | Description | Key capabilities |
|------|-------------|------------------|
| **Global Administrator** | Full system owner | Manage everything: users, use cases, global config, providers, system settings |
| **IT Security** | Security oversight (restricted view) | Cross-use-case anomaly visibility, mark/block requests, define incident response. **Cannot** see all business content by default — scoped visibility |
| **IT Steuerung (IT Governance)** | Governance & oversight | Read-only overview of all use cases, their descriptions & processing logic, aggregate statistics |
| **Use Case Administrator** | Owns a specific use case | Configure pre-dispatch pipeline, model routing/fallback, budgets, anomaly rules for *their* use case; manage members |
| **Use Case User** | Member of a use case | Consume the gateway within the use cases they are assigned to |

> **Least-privilege principle**: roles are additive and object-scoped. A Use Case User only sees
> their assigned use case(s); IT Security sees security-relevant metadata across use cases but not
> necessarily full business payloads (configurable redaction).

---

## 4. System Overview

AIRA consists of **two self-developed components** plus **open-source infrastructure**.

```
                         ┌──────────────────────────────────────────────┐
   Clients (SDKs,        │                 AIRA GATEWAY                   │
   apps, agents) ──────▶ │  Component 1: Gateway API (FastAPI)           │
   Gemini-compat         │   • Unified API  • AuthN/Z  • Attribution     │
   (OpenAI later)        │   • Routing/Fallback  • Pipeline execution    │
                         │   • Request/Response persistence  • Tracing   │
                         └───────┬───────────────────────┬──────────────┘
                                 │                        │
                    ┌────────────▼─────────┐   ┌──────────▼───────────┐
                    │  Upstream: Gemini     │   │ Upstream: Microsoft  │
                    │  Enterprise Agent     │   │ Foundry (OpenAI +    │
                    │  (Gemini + Anthropic) │   │ Microsoft models)    │
                    └───────────────────────┘   └──────────────────────┘

        Kafka (event bus)  ◀───────────────────────────────▶
                                 │
                         ┌───────▼───────────────────────────────────────┐
                         │  Component 2: Management & Monitoring          │
                         │  Angular SPA  +  Django REST Framework         │
                         │   • Use-case self-service  • Pipeline builder  │
                         │   • Budgets  • Anomaly rules                   │
                         │   • IT Security console  • IT Governance views │
                         └───────────────────────────────────────────────┘

   Shared infrastructure: PostgreSQL · Keycloak (SSO) · HashiCorp Vault (secrets) · Kafka
```

### 4.1 Component 1 — Gateway API (FastAPI)
The data-plane. High-throughput, stateless-where-possible service that:
- Exposes a **unified, Gemini-compatible** REST API first (OpenAI-compatible surface added later).
- Authenticates via **bearer token (OIDC)** or **API key**.
- **Classifies & attributes** each request to user / project / use case.
- Applies the **use-case pre-dispatch pipeline** (injection filter, allow checks, routing).
- **Routes** to the appropriate model/upstream, with rerouting (cost-based) and **fallback**.
- **Persists** the full request and response.
- Emits **tracing** spans and captures **source IP**.
- Publishes events to **Kafka** (usage, anomalies, audit).

### 4.2 Component 2 — Management & Monitoring (Angular + DRF)
The control-plane. Human-facing UI + config API that:
- Provides **use-case self-service** (create use case, assign members).
- Lets Use Case Admins build the **pre-dispatch pipeline** (sequential/parallel steps, fallback).
- Manages **budgets** per use case / per member.
- Lets users define **anomaly rules** (rate, size, data-leak detection).
- Gives **IT Security** a cross-use-case console (anomalies, mark/block, incident response).
- Gives **IT Steuerung** oversight dashboards and aggregate statistics.
- Owns configuration; pushes it to the Gateway via **Kafka** (and/or a config read-model).

### 4.3 Inter-component communication
The two components **communicate over Kafka**. Broadly:
- Management → Gateway: configuration changes (pipelines, budgets, anomaly rules, block lists) as
  versioned events → Gateway maintains a local read-model/cache.
- Gateway → Management: usage events, request/response metadata, anomaly signals, audit events.

Postgres is the system of record; Kafka carries the event stream. (Exact topic design and the
config-distribution mechanism are defined in the architecture doc / relevant FRDs.)

---

## 5. Functional Requirements

### 5.1 Gateway API (Component 1)

- **FR-GW-1 Unified interface**: Provide a **Gemini-compatible** surface **first** (Google
  Generative Language API v1beta: `POST /v1beta/models/{model}:generateContent`,
  `:streamGenerateContent`, `:embedContent`, `GET /v1beta/models`). An **OpenAI-compatible** surface
  (`/v1/chat/completions`, `/v1/embeddings`, `/v1/models`) is added later (FRD-106). Both normalize
  to one internal canonical schema, then translate to the target upstream's dialect. See ADR-0005.
- **FR-GW-2 Authentication**: Accept (a) OIDC bearer tokens validated against Keycloak, and
  (b) self-generated API keys (hashed at rest, prefix-identifiable, revocable, scoped). Per ADR-0006,
  API keys are **issued in Management** (self-service, show-once) and distributed to the Gateway via
  Kafka into a local read-model; the **Gateway only validates**. OIDC validation lives in the Gateway.
- **FR-GW-3 Attribution**: Resolve each request to `user`, `project`, `use_case`, and
  `user∈use_case`. Attribution derives from the credential + request context.
- **FR-GW-4 Routing & rerouting**: Route requests to a model/upstream based on use-case config;
  support **cost-based rerouting** (classify request difficulty → cheaper model when possible).
- **FR-GW-5 Sequential/parallel execution**: Execute pipeline steps and/or multiple models
  sequentially or in parallel per use-case configuration.
- **FR-GW-6 Fallback**: On upstream error / timeout / policy failure, fall back to a configured
  alternative model/upstream.
- **FR-GW-7 Persistence**: Persist request and response payloads and metadata ("store as much as
  possible"), with configurable retention and redaction hooks.
- **FR-GW-8 Tracing**: Distributed tracing (OpenTelemetry) across gateway → pipeline → upstream.
- **FR-GW-9 Source IP capture**: Record originating IP (respecting proxy headers / trusted hops).
- **FR-GW-10 Pipeline enforcement**: Execute the use-case's pre-dispatch pipeline (injection
  filter, allow/deny checks, routing) before dispatch; honor throttle/alert/block decisions.
- **FR-GW-11 Budget enforcement**: Reject/limit requests that exceed use-case or per-member budgets.
- **FR-GW-12 Event emission**: Publish usage/audit/anomaly events to Kafka.
- **FR-GW-13 Demo/mock upstream**: A built-in mock provider so the gateway works end-to-end without
  real credentials.

### 5.2 Management & Monitoring (Component 2)

- **FR-MG-1 Use-case self-service**: Create/edit use cases (name, description, processing logic,
  members). Assigned members automatically gain access to that use case.
- **FR-MG-2 Membership & roles**: Assign users to use cases and to roles; object-scoped access.
- **FR-MG-3 Pipeline builder**: Visually configure the pre-dispatch pipeline — ordered steps,
  **sequential or parallel** branches, step types (e.g. LLM prompt-injection filter, allow-check,
  routing), and **model fallback** chains.
- **FR-MG-4 Model routing config**: Configure routing rules and cost-based rerouting per use case.
- **FR-MG-5 Budget control**: Set budgets per use case and per member; view consumption.
- **FR-MG-6 Anomaly self-service**: Define anomaly rules, e.g. too many requests per time window,
  oversized requests, requests that leak internal information.
- **FR-MG-7 IT Security console**: Cross-use-case anomaly overview; **mark** or **auto-block**
  requests; define **incident response** (throttle / alert / block) per rule.
- **FR-MG-8 IT Steuerung views**: Read-only overview of all use cases (descriptions + processing
  logic) and aggregate statistics.
- **FR-MG-9 Observability dashboards**: Logging, tracing, monitoring surfaced per use case and
  globally.
- **FR-MG-10 Audit trail**: All configuration and security actions are audited.
- **FR-MG-11 Demo mode & seeding**: One command seeds realistic demo data (users, use cases,
  pipelines, sample traffic) to showcase every feature.

---

## 6. Non-Functional Requirements

- **NFR-1 Test coverage**: The whole system is covered by unit tests; target **near-100% coverage**
  with enforced thresholds in CI. Integration and contract tests for cross-component boundaries.
- **NFR-2 Security**: Secrets only in **HashiCorp Vault**; no secrets in code/env files committed.
  Encryption in transit; API keys hashed at rest; least-privilege RBAC; input validation.
- **NFR-3 Observability**: Structured logging, **OpenTelemetry** tracing, Prometheus-style metrics;
  correlation IDs across components.
- **NFR-4 Scalability**: Gateway horizontally scalable and stateless where possible; Kafka for
  decoupling; Postgres as system of record.
- **NFR-5 Performance**: Gateway adds low overhead to the request path; persistence and event
  emission are async / non-blocking on the hot path.
- **NFR-6 Reliability**: Graceful degradation; fallback on upstream failure; at-least-once event
  delivery with idempotent consumers.
- **NFR-7 Auditability & compliance**: Immutable audit log; data retention & redaction controls;
  role-scoped data access for compliance.
- **NFR-8 Maintainability**: Clear module boundaries, typed code (Python type hints, TypeScript),
  linting/formatting, documented APIs (OpenAPI).
- **NFR-9 Portability**: Runs locally via Docker Compose; designed to migrate to Kubernetes/Helm.
- **NFR-10 Configurability**: Behavior driven by use-case config, not code changes.

---

## 7. Data Model (high-level)

Core entities (details in FRDs):
- **User**, **Project**, **UseCase**, **UseCaseMembership** (user × use case × role)
- **ApiKey** (hashed, scoped, revocable)
- **Provider** / **UpstreamModel** (Gemini Enterprise, MS Foundry, mock)
- **RoutingRule**, **PipelineDefinition**, **PipelineStep**, **FallbackChain**
- **Budget** (per use case / per member) + **UsageRecord**
- **RequestLog** / **ResponseLog** (payloads + metadata + source IP + trace ID)
- **AnomalyRule**, **AnomalyEvent**, **IncidentResponsePolicy**
- **AuditEvent**

---

## 8. Integrations

| System | Purpose | Notes |
|--------|---------|-------|
| **Keycloak** | SSO / OIDC | Bearer-token auth for Gateway; SSO for management UI; role source |
| **PostgreSQL** | System of record | Config + logs + usage; separate schemas/DBs per component |
| **Kafka** | Event bus | Config distribution + usage/audit/anomaly events between components |
| **HashiCorp Vault** | Secrets | Upstream credentials, signing keys, DB creds |
| **Gemini Enterprise Agent Platform** | Upstream | Gemini + Anthropic models |
| **Microsoft Foundry** | Upstream | OpenAI + Microsoft models |
| **Mock provider** | Demo | Built-in, no external calls |
| **OpenTelemetry Collector** | Telemetry ingestion | OTLP receiver; both components export traces/metrics/logs here |
| **Grafana otel-lgtm** | Observability backend | Local single-container Grafana + Loki + Tempo + Prometheus; OTLP-native (see ADR-0004; supersedes SigNoz) |

---

## 9. Security & Compliance

- OIDC + API-key authentication; API keys hashed (e.g. Argon2/SHA-256 with prefix lookup).
- RBAC with object-level scoping; IT Security has security-metadata visibility with configurable
  payload redaction.
- All secrets in Vault; short-lived credentials where possible.
- Prompt-injection filtering and data-leak detection as pipeline steps.
- Full audit trail; immutable, queryable by IT Security / Governance.
- Configurable data retention & redaction for stored requests/responses (PII handling).

---

## 10. Observability & Operations

- **Telemetry pipeline**: both components export via **OTLP** to an **OpenTelemetry Collector**,
  which forwards to a local **Grafana `otel-lgtm`** backend (traces + metrics + logs unified). This
  is the local observability target on developer hardware from Phase 0 (see ADR-0004).
- **Logging**: structured JSON logs with correlation/trace IDs (also shipped via OTLP).
- **Tracing**: OpenTelemetry spans across gateway, pipeline steps, upstreams; source IP recorded.
- **Monitoring**: metrics (latency, throughput, error rate, cost, budget consumption, anomalies).
- **Dashboards**: per-use-case and global (Grafana); security-specific views for IT Security.
- **Alerting**: anomaly-driven alerts feeding incident response.
- **Vendor portability**: apps emit vendor-neutral **OTLP** and only talk to the Collector, so the
  backend is swappable without app changes — a production move to **Dynatrace, Grafana Cloud,
  Datadog, Honeycomb, New Relic, Elastic**, etc. is a Collector-exporter change (and can fan out to
  several backends at once). See ADR-0004.

---

## 11. Demo Mode

- Built-in **mock upstream** provider (deterministic, no external credentials).
- **Automated seeding** command loads: demo users mapped to all roles, projects, use cases with
  pipelines/budgets/anomaly rules, and sample request/response traffic + anomalies — so every
  feature is demonstrable immediately after `docker compose up`.

---

## 12. Testing Strategy

- **Unit tests** for all business logic (Gateway + DRF backend + Angular), enforced coverage
  thresholds in CI.
- **Contract tests** for the Kafka event schemas and the unified API.
- **Integration tests** with ephemeral Postgres/Kafka/Keycloak/Vault (Compose/testcontainers).
- **E2E smoke tests** in demo mode.

---

## 13. Technology Stack

| Layer | Choice |
|-------|--------|
| Gateway API | **Python 3.12+, FastAPI**, Pydantic, httpx, OpenTelemetry |
| Management backend | **Django + Django REST Framework**, django-guardian, mozilla-django-oidc |
| Management frontend | **Angular (TypeScript)**, Angular CDK, `angular-oauth2-oidc`, a flow/graph lib for the pipeline builder |
| Data | **PostgreSQL** |
| Eventing | **Apache Kafka** |
| SSO | **Keycloak** |
| Secrets | **HashiCorp Vault** |
| Observability | **OpenTelemetry Collector** → **Grafana otel-lgtm** (local; ADR-0004) |
| Packaging/Runtime | **Docker Compose** (local) → **Kubernetes/Helm** (future) |
| Testing | pytest, coverage, Jasmine/Karma or Jest (Angular) |
| Language | **English** for docs, code, and identifiers |

---

## 14. Assumptions, Constraints & Risks

**Assumptions**
- Upstream platforms (Gemini Enterprise, MS Foundry) expose usable APIs; exact contracts confirmed
  per integration FRD.
- Initial deployment is single-node Docker Compose on the developer machine.

**Constraints**
- All docs and code in English; secrets only in Vault; near-100% unit-test coverage required.
- Two-component split communicating via Kafka.

**Risks**
- R1: Upstream API differences complicate the unified schema → mitigate with adapter layer + mock.
- R2: "Store as much as possible" raises data-privacy/retention concerns → redaction & retention
  controls from the start.
- R3: Kafka-based config distribution adds eventual-consistency complexity → versioned config +
  read-model with fallbacks.
- R4: Near-100% coverage target adds cost → bake testing into every FRD/phase.

---

## 15. Glossary

- **Use Case**: A bounded AI application context with its own members, pipeline, budgets, rules.
- **Pre-dispatch pipeline**: Ordered (sequential/parallel) steps applied before a request reaches
  an upstream model (e.g. injection filter, allow-check, routing).
- **Rerouting**: Cost-based redirection of "easy" requests to cheaper models.
- **Fallback**: Alternative model/upstream used when the primary fails.
- **Incident response**: Configured reaction to an anomaly — throttle, alert, or block.
- **IT Steuerung**: IT Governance oversight role.

---

## 16. Related Documents
- `docs/ROADMAP.md` — phased delivery plan.
- `docs/features/` — individual Feature Requirement Documents (FRDs).
