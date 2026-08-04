# AIRA Gateway — Development Log

A running, dated log of meaningful changes and decisions. Newest entries on top.
Keep entries short; link to ADRs/FRDs/commits for detail.

---

## 2026-08-04 — Decision: API-key issuance belongs in Management (ADR-0006)
- Clarified the control-plane/data-plane split for API keys: **issuance/lifecycle/show-once** →
  **Management** (self-service UI, bound to use case); **validation** → **Gateway** against a local
  **read-model** fed by **Kafka** (`api_key.*` events; never plaintext). Rejected sync-call and
  shared-DB alternatives.
- The Phase-1 gateway-side generation + CLI are a **bootstrap**; issuance moves to Management in
  **Phase 2** (new ROADMAP `FRD-205`). The gateway `api_keys` table becomes the read-model. OIDC
  validation stays in the Gateway. No code change now — documented as `ADR-0006`; updated
  FRD-101/PRD/ROADMAP.

---

## 2026-08-04 — FRD-101 Slice B: OIDC bearer validation — **auth complete**
- **OIDC validation** (`gateway/auth/oidc.py`): `OidcValidator` verifies a Keycloak JWT via the
  issuer's **JWKS** (`PyJWT` + `cryptography`), checking signature, issuer, expiry, and (optional)
  audience; resolves to a `Principal(method="oidc")`. JWKS client is injectable → unit-testable
  without a live Keycloak. `build_oidc_validator` gates on `oidc_enabled`/`oidc_issuer`.
- **Wired** into `resolve_principal`: a non-AIRA `Bearer` token is validated by the OIDC validator
  when configured (`app.state.oidc_validator`); API keys still take the `aira_` path.
- **Keycloak realm**: added `deploy/compose/keycloak/realms/aira-realm.json` (realm `aira`, public
  client `aira-gateway` with direct-access grants, demo user `demo-user`); imported on startup.
- **Gates green**: 123 tests, **100% coverage**; ruff + mypy --strict clean. Hermetic OIDC tests use
  a self-signed RS256 keypair + fake JWKS resolver (valid/expired/wrong-iss/wrong-aud/bad-sig/no-sub).
- **End-to-end verified**: fetched a real access token from Keycloak (password grant) → Gemini route
  returns **200** with the bearer, **401** for a garbage token. Run with
  `AIRA_OIDC_ENABLED=true AIRA_OIDC_ISSUER=http://localhost:8080/realms/aira`.
- **FRD-101 complete** (API key + OIDC). **Next: FRD-102** (attribution: request → user/project/use-case).

---

## 2026-08-04 — FRD-101 Slice A: API-key authentication + gateway DB layer
- **Gateway DB layer** (`gateway/db/`): SQLAlchemy 2.0 async via **psycopg3** (Postgres) /
  **aiosqlite** (tests); `Base`, engine/sessionmaker builders, `create_all` (Alembic deferred to
  FRD-103), and the `api_keys` table. App builds the engine + runs `create_all` in a lifespan.
- **API keys** (`gateway/auth/`): format `aira_<prefix>_<secret>` (hex), only the SHA-256 **hash**
  stored; `ApiKeyService` (create/verify/revoke/ensure_demo_key) with constant-time compare;
  `Principal` (subject + method); credential extraction (`Authorization: Bearer` → `x-goog-api-key`
  → `?key=`); `require_principal` dependency guarding the Gemini routes (Gemini-shaped 401).
- **Toggle & demo**: `auth_required` (default true); demo mode seeds a deterministic demo key.
- **CLI** (`python -m aira_gateway.cli api-key create|revoke`) to mint/revoke keys.
- **Gates green**: 111 tests, **100% coverage**; ruff + mypy --strict clean. Tests hermetic
  (in-memory SQLite; pytest auto-detected).
- **End-to-end verified** against Postgres: CLI minted a real key (persisted in `api_keys`); the
  Gemini route returns **401** without a credential, **200** with the key (header/`?key=`/Bearer),
  **401** for a bad/revoked key.
- **Next: FRD-101 Slice B** — OIDC bearer validation (Keycloak JWKS) + realm import, plugged into
  the same `resolve_principal`.

---

## 2026-08-04 — FRD-100: Gemini-compatible unified API (Phase 1 begins)
- **Decision**: ship the **Gemini** wire format first (existing projects run on it); OpenAI later →
  `ADR-0005`. Updated PRD/ROADMAP/README; added detailed `FRD-100`.
- **Canonical core** (`gateway/core/canonical.py`): provider-agnostic request/response/usage/chunk —
  the single schema every surface and upstream agrees on (so OpenAI/FRD-106 is just another mapper).
- **Upstream abstraction** (`upstreams/base.py`): `Upstream` protocol + `ProviderRegistry`; the
  deterministic `MockProvider` (evolved from FRD-002) is the only provider in Phase 1.
- **Gemini surface** (`api/gemini/`): Pydantic wire schemas, Gemini⇄canonical mappers, and routes —
  `POST /v1beta/models/{model}:generateContent | :streamGenerateContent | :embedContent`,
  `GET /v1beta/models`, `GET /v1beta/models/{model}`. Gemini-shaped error envelope (400/404/500).
- **Gates green**: 88 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified** via curl: list models, `:generateContent` (correct candidates + usage),
  NDJSON `:streamGenerateContent`, and unknown-model → 404.
- **Next in Phase 1**: FRD-101 (auth: API key + OIDC), then attribution/persistence/tracing.

---

## 2026-08-04 — FRD-002: seed & demo mode — **Phase 0 fully complete**
- **Seed framework** (Django, `aira_management.apps.seed`): an extensible registry — each phase
  registers idempotent `SeedContribution`s (run in `(order, name)`); a `seed_demo` management command
  runs them, supports `--fresh` (reset) and refuses production without `--force`.
- **Phase 0 contribution** `roles_and_users`: creates the five roles as Django `Group`s and one
  deterministic demo user each (admin/itsec/itgov/ucadmin/ucuser), idempotently. Roles centralized in
  `aira_management.roles.Role` (reused by Phase 2 RBAC).
- **Mock upstream** (gateway `upstreams/mock.py`): deterministic offline completions/embeddings for
  demo mode (basic; full fidelity in FRD-104).
- **Hermetic tests**: `settings.py` uses in-memory SQLite under pytest (`"pytest" in sys.modules` —
  ordering-robust, replaced a fragile conftest env hack), so the suite needs no Postgres.
- `make seed` / `make seed-reset` wired (migrate + seed_demo).
- **Gates green**: 68 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified**: `make seed` against live Postgres created 5 groups + 5 users mapped to
  roles; re-run created nothing (idempotent); confirmed in the `aira_mgmt` DB.
- **Phase 0 (Foundation & Infra) is complete** (all of FRD-000/001/002). **Next: Phase 1 — Gateway MVP.**

---

## 2026-08-04 — FRD-001: observability baseline (backend switched to Grafana otel-lgtm)
- **Decision change**: SigNoz deprecated its Docker Compose manifests (Foundry-only), so it can't be
  embedded cleanly. Switched the local OTLP backend to **Grafana `otel-lgtm`** → `ADR-0004`
  (supersedes `ADR-0002`). Updated PRD/ROADMAP/CLAUDE.md/FRD-001.
- **Compose**: added `otel-collector` (contrib 0.157) + `otel-lgtm` (0.30) under an `observability`
  profile; collector config forwards OTLP → otel-lgtm (`otlp_grpc`). `make up` now includes
  observability by default; `make up-core` for a lean start.
- **Instrumentation**: new `aira_common.observability` (tracer/meter/logger providers, OTLP/HTTP
  export, gated by `otel_enabled`); structlog `add_trace_context` processor (trace/span ids in
  logs); Kafka header inject/extract helpers for cross-component context. Gateway auto-instruments
  FastAPI, management auto-instruments Django when enabled.
- **Gates green**: 55 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified**: ran the gateway with `AIRA_OTEL_ENABLED=true`; spans for `/healthz` +
  `/readyz` (service.name=aira-gateway, http.route, status) flowed apps → collector → otel-lgtm and
  are **queryable in Tempo**; no export errors. Grafana UI at `http://localhost:3000`.
- **Next:** `FRD-002` (seed & demo mode), then Phase 1 (Gateway MVP).

---

## 2026-08-04 — Phase 0 / Slice 3b: Angular frontend shell — **Phase 0 complete**
- Scaffolded **`management/frontend`** with **Angular 22** (latest; note: Node is 26, Angular is 22).
  Uses the new `@angular/build:unit-test` builder → **Vitest + jsdom** (no browser needed — CI-friendly).
- Replaced the default welcome page with a minimal **AIRA shell** (title/subtitle header, nav
  placeholder, `router-outlet`); updated specs (3 tests) and page `<title>`.
- Wired frontend into `make`: `test`/`test-frontend`, `lint`/`lint-frontend` (Prettier + build),
  `fmt`, `run-frontend`, and `sync` (npm install). `make test` now runs Python + frontend together.
- **Gates green**: `ng build` OK (~216 kB), 3 frontend tests pass, Prettier clean; Python side still
  41 tests / 100% coverage / ruff + mypy clean. `node_modules`/`dist` git-ignored.
- **Phase 0 (Foundation & Infra) is complete**: full local stack (`make up`) + gateway, management
  backend, and frontend skeletons, all tested and observ-ready hooks in place.
- **Next:** Phase 1 — Gateway MVP (`FRD-100` unified API, `FRD-101` auth, `FRD-102` attribution,
  `FRD-103` persistence, `FRD-104` mock upstream, `FRD-105` tracing/IP). Also still pending from
  Phase 0 plan: OTel Collector + SigNoz wiring (`FRD-001`) and seed/demo (`FRD-002`).

---

## 2026-08-04 — Phase 0 / Slice 3a: management backend (Django + DRF)
- Added **`management/backend`** as a third uv workspace member: **Django 6.0 + DRF 3.17 +
  psycopg 3.3** on Python 3.14 (src layout, package `aira_management`).
- Structure: `config` (settings driven by a typed `ManagementSettings`, `runtime.get_settings()`,
  urls/asgi/wsgi), `apps/health` (`/healthz` + `/readyz` mirroring the gateway contract, reusing
  `aira_common`), `manage.py`.
- **Type-checking**: wired **django-stubs** mypy plugin; refactored the dynamic `settings.AIRA`
  access to a typed `get_settings()` accessor so `mypy --strict` stays clean.
- **Quality gates green**: 41 tests total, **100% coverage** across gateway+libs+backend;
  `ruff`, `ruff format`, and `mypy --strict` (25 files) all pass. `make run-backend` added.
- **Smoke test**: `manage.py check` clean; runserver `/readyz` returns `ready` against the live
  Compose stack (postgres+kafka reachable, HTTP 200).
- **Next:** Slice 3b (Angular frontend shell) to close Phase 0.

---

## 2026-08-04 — Phase 0 / Slice 2: gateway skeleton + shared libs
- **uv workspace** at repo root (`pyproject.toml`) with members `gateway` + `libs`; shared tooling
  config (ruff, mypy strict, pytest, coverage gate `--cov-fail-under=90`). Python 3.14 venv via uv.
- **`aira-common`** shared lib: `config` (pydantic-settings base), `logging` (structlog JSON),
  `errors` (AiraError + ErrorResponse envelope), `events` (EventPublisher protocol +
  InMemoryEventPublisher; real Kafka transport deferred to Phase 1), `health` (async TCP checks).
- **`aira-gateway`** skeleton (FastAPI): app factory (`create_app`), `GatewaySettings`,
  `/healthz` + `/readyz` (probes Postgres + Kafka), AiraError exception handler, `main:app` entry.
- **Quality gates green**: 32 tests, **100% coverage**; `ruff check`, `ruff format --check`, and
  `mypy --strict` all pass. Wired `make sync/test/lint/fmt/run-gateway`.
- Note: on Python 3.14, ruff formats multi-type excepts with PEP 758 syntax
  (`except TimeoutError, OSError:` — no parentheses); valid and intended.
- **Smoke test**: ran the gateway against the live Compose stack — `/readyz` returns `ready`
  with postgres+kafka reachable (HTTP 200).
- **Next:** Slice 3 (management backend skeleton: Django + DRF) + Angular workspace shell.

---

## 2026-08-04 — Phase 0 / Slice 1: infra stack + toolchain
- **Toolchain** (ADR-0003): confirmed Python 3.14.4 + uv 0.9.26 present. Installed **Node 26.6.0**
  via nvm; worked around `NPM_CONFIG_PREFIX` (unset in persistent env) and symlinked node/npm/npx
  into `~/.local/bin` (first on PATH); installed system lib `libatomic1` (Node 26 dependency).
- **Monorepo skeleton**: `gateway/`, `management/backend/`, `management/frontend/`, `libs/`,
  `deploy/compose/` created.
- **Docker Compose infra** (`deploy/compose/`): postgres 17, keycloak 26.1, kafka 3.9 (KRaft),
  schema-registry 7.8, vault 1.18 — with healthchecks, `.env.example`, postgres init script
  (creates `aira_gateway`/`aira_mgmt`/`keycloak` DBs), and a root `Makefile`
  (`up/down/destroy/ps/logs` + stub `test/lint/fmt/seed`).
- **Brought up & verified healthy**: postgres (DBs created), kafka (fixed a KRaft
  `advertised.listeners 0.0.0.0` error → use `://:PORT` + `localhost` quorum), schema-registry
  (API responds), vault (unsealed).
- **Keycloak**: initially blocked (quay.io 403); resolved after the host allowed quay.io. Image
  pulled, service healthy, OIDC discovery reachable at `/realms/master/.well-known/openid-configuration`.
- **Slice 1 complete**: all five infra services (postgres, keycloak, kafka, schema-registry, vault)
  up and healthy via `make up`.
- **Next:** Slice 2 (gateway skeleton + shared `libs/`).

---

## 2026-08-04 — Git init + Phase 0 FRDs
- Initialized the Git repository (branch `main`) and added a `.gitignore` (Python, Node/Angular,
  secrets/`.env`, Docker data volumes).
- Wrote the three **Phase 0 FRDs**:
  - `FRD-000-foundation-infra` — monorepo layout, Docker Compose stack (Postgres, Keycloak, Kafka
    +schema-registry, Vault), service skeletons, shared `libs/`, CI + coverage gate, Make targets.
  - `FRD-001-observability-baseline` — OTLP → OTel Collector → SigNoz, app instrumentation, trace
    context propagation over HTTP + Kafka, correlated logs/metrics.
  - `FRD-002-seed-and-demo-mode` — `DEMO_MODE`, mock upstream (basic), idempotent extensible
    seed framework covering all five roles, deterministic data.
- **Next:** implement Phase 0, starting with `FRD-000` (Compose stack + skeletons + CI).

---

## 2026-08-04 — Project kickoff & planning foundation
- Established project vision and scope; created **`docs/PRD.md`** (Project Requirements Document v0.1).
- Created **`docs/ROADMAP.md`** — phased delivery plan (Phase 0–7).
- Added **`docs/features/FRD-TEMPLATE.md`** and **`README.md`**.
- Locked key decisions:
  - Management UI = **Angular + Django REST Framework** → `ADR-0001`.
  - Local observability = **OTel Collector + SigNoz** (alt: Grafana LGTM) → `ADR-0002`.
  - Docs & code in **English**; **Docker Compose** locally; **automated seeding** + demo mode required.
- Created **`CLAUDE.md`** (project guidance) and set up **`docs/adr/`** (ADR process + first two ADRs).
- **Next:** write Phase 0 FRDs (`FRD-000` foundation, `FRD-001` observability, `FRD-002` seed/demo),
  then begin implementation of Phase 0 (Foundation & Infra).
