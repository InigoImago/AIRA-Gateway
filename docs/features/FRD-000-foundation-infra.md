# FRD-000 — Foundation & Infrastructure

> Phase: 0 · Status: **Done (Phase 0)** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §4, §6, §8, §13; `docs/ROADMAP.md` Phase 0

## 1. Summary
Establish the reproducible local platform and engineering baseline that every later feature builds
on: the monorepo layout, a Docker Compose stack for all infrastructure (PostgreSQL, Keycloak, Kafka
+ schema registry, Vault), shared application conventions (config, logging, error handling), and a
CI pipeline that enforces linting, type-checking, tests, and a coverage gate. This FRD does **not**
deliver business features — it delivers the ground everything stands on, runnable via
`docker compose up`.

## 2. Goals & Non-Goals
**Goals**
- One-command local bring-up of all infrastructure dependencies via Docker Compose.
- Agreed monorepo structure and per-service scaffolding (empty but runnable).
- Shared conventions: 12-factor config, structured logging, error handling, health checks.
- CI with lint + type-check + test + **coverage gate** wired from the first commit.
- Vault (dev mode) as the single source of secrets; no secrets in the repo.

**Non-Goals**
- Business endpoints, auth logic, routing, UI screens (later FRDs).
- Observability internals — covered by `FRD-001`.
- Seed data & demo mode — covered by `FRD-002`.
- Kubernetes/Helm — Phase 7.

## 3. User Stories
- As a **developer**, I want `docker compose up` to start Postgres/Keycloak/Kafka/Vault so I can run
  the system locally without manual setup.
- As a **developer**, I want CI to block merges that fail lint/type/test/coverage so quality stays high.
- As a **maintainer**, I want a clear repo layout so each component has an obvious home.

## 4. Functional Requirements
- **FR-1 Monorepo layout** (created, each buildable in isolation):
  ```
  AIRA/
  ├── gateway/                # FastAPI service (skeleton: app factory, /healthz, config, tests)
  ├── management/
  │   ├── backend/            # Django + DRF project (skeleton: settings, /healthz, tests)
  │   └── frontend/           # Angular workspace (skeleton: shell app, build, unit test)
  ├── deploy/
  │   ├── compose/            # docker-compose.yml + service configs + .env.example
  │   └── README.md
  ├── libs/                   # shared Python (config, logging, kafka, otel bootstrap)
  └── docs/
  ```
- **FR-2 Compose stack** in `deploy/compose/docker-compose.yml` with services:
  - `postgres` (separate logical DBs/schemas for gateway vs management),
  - `keycloak` (dev, with a pre-provisioned realm import placeholder for later FRDs),
  - `kafka` + `schema-registry` (single-broker dev; KRaft mode preferred, no ZooKeeper),
  - `vault` (dev mode, root token via env for local only),
  - named volumes for data; a shared Docker network; healthchecks on every service.
- **FR-3 Service skeletons** (must start, expose `/healthz`, and have ≥1 passing test each):
  - Gateway: FastAPI app factory, settings via env, `/healthz` + `/readyz`, `pyproject.toml`.
  - Management backend: Django project + DRF installed, `/healthz`, `pytest`+`pytest-django` set up.
  - Frontend: Angular workspace, a placeholder shell component, `ng build` + one unit test green.
- **FR-4 Shared libs** (`libs/`): typed config loader (env + Vault), structured JSON logger,
  standard error/response envelope, and a Kafka producer/consumer wrapper (thin; topics defined later).
- **FR-5 Secrets via Vault**: a documented convention + helper for reading secrets from Vault; local
  bootstrap seeds required dev secrets into Vault on startup. `.env.example` documents non-secret config.
- **FR-6 CI pipeline**: on push/PR — install, **lint** (ruff/black, eslint/prettier), **type-check**
  (mypy, tsc), **test** (pytest, ng test headless), **coverage gate** (fail under threshold).
- **FR-7 Makefile / task runner**: `make up`, `make down`, `make test`, `make lint`, `make fmt`,
  `make seed` (stub → implemented in FRD-002) for a consistent developer UX.

## 5. Design & Architecture
- **Compose-first**: all infra in one network; apps read connection info from env/Vault.
- **KRaft Kafka** to avoid ZooKeeper; single broker for local. Schema Registry for event contracts.
- **Config precedence**: explicit env > Vault-provided secrets > safe defaults. Fail fast if a
  required secret is missing.
- **App factories** so tests can build isolated app instances; no import-time side effects.
- **Health/readiness**: `/healthz` (liveness) and `/readyz` (checks DB/Kafka reachability) on backends.

## 6. Data Model
- No business entities yet. Provision empty databases/schemas: `aira_gateway`, `aira_mgmt`.
- Django migrations framework initialized (no domain models yet).

## 7. API / Interface Contract
- `GET /healthz` → `200 {"status":"ok"}` (gateway + management).
- `GET /readyz` → `200`/`503` with per-dependency status.
- No other endpoints in this FRD.

## 8. Security & Privacy
- No secret values committed; `.gitignore` blocks `.env`, keys, Vault data.
- Vault dev-mode root token is local-only and documented as **not for production**.
- Keycloak/Postgres/Kafka default dev credentials are injected from Vault/env, not hard-coded in code.

## 9. Observability
- Only the **hooks** here: apps initialize the OTel SDK (no-op/console exporter acceptable until
  `FRD-001` wires the Collector). Structured logging active from day one. Health endpoints scrapeable.

## 10. Testing & Acceptance Criteria
- **Tests**: each skeleton service ships ≥1 unit test; shared libs are unit-tested; coverage gate
  active (threshold set high, e.g. ≥90% initially, ratcheting toward ~100%).
- **Acceptance**:
  - **Given** a clean checkout, **when** I run `make up`, **then** Postgres, Keycloak, Kafka,
    Schema Registry, and Vault all reach healthy state.
  - **Given** the stack is up, **when** I curl `/healthz` on gateway and management, **then** both
    return `200 ok`; `/readyz` reports dependencies reachable.
  - **When** I run `make test`, **then** all suites pass and coverage meets the gate.
  - **When** I run `make lint`, **then** it passes with zero violations.

## 11. Dependencies & Risks
- Enables all later FRDs. No upstream dependencies.
- Risk: Kafka KRaft/Schema Registry local flakiness → pin versions, add healthchecks/retries.
- Risk: Vault dev-mode ergonomics → provide a clear bootstrap script and docs.

## 12. Rollout / Demo
- Demo: `make up && make test` — a green stack and green tests are the deliverable.
- Seed data added in `FRD-002` (the `make seed` target is a stub here).
