# AIRA Gateway — Development Log

A running, dated log of meaningful changes and decisions. Newest entries on top.
Keep entries short; link to ADRs/FRDs/commits for detail.

---

## 2026-08-04 — FRD-306: pipeline rework — LLM routing, explainable filter, dry-run
- Reworked the pipeline after feedback that routing was length-only and the builder was opaque.
- **Routing** is now an **LLM classifier**: it reads system + user text, picks one of the configured
  `categories` (`{name, description, model}`) and routes to that model (`default_model` fallback).
- **Injection filter**: built-in patterns are **shown**; operators add **custom patterns** (invalid
  regex → literal); `use_builtins` toggle; `scope` user | system+user; LLM mode takes model +
  instruction.
- **Transparency**: `engine.dry_run()` + `POST /v1beta/pipeline:dryRun` return a full per-step trace;
  the builder gains a **test panel** with an instant **live preview** (deterministic steps,
  client-side) and a **Dry-run** button (full trace incl. LLM via gateway, `/gw` dev proxy).
- Inspector redesigned with inline help per step + a categories editor.
- **Gates green**: backend 299 tests / 99.8% (pipeline modules ~100%), ruff + mypy --strict clean;
  frontend 23 Vitest tests, Prettier clean, `ng build` OK. `FRD-306` done.

---

## 2026-08-04 — UI usability: tabbed use-case detail
- The use-case detail page was overloaded with stacked lists (members + keys + forms). Split into
  **tabs** (Overview / Members / API keys) so one section shows at a time; add/issue forms moved
  behind **disclosure** toggles; Overview shows quick **stat tiles**. Added `.tabs`/`.tile`/
  `.disclosure` to the design-system. `ng build` + 21 Vitest + Prettier green.

---

## 2026-08-04 — FRD-300/303: pre-dispatch pipeline (filter · routing · fallback) + graph builder
- **Gateway engine** (`aira_gateway/pipeline/`): per-use-case, config-driven pipeline runs before
  dispatch on the canonical request. Steps: `injection_filter` (heuristic **or LLM-backed**, fails
  open; action block|flag), `allow_check` (model allow-list), `model_route` (rule-based incl.
  cost/length rerouting). Dispatch follows a `fallback_models` chain. Default (no config) =
  pass-through, so prior behavior is unchanged. Decisions logged + traced (`aira.pipeline.*`).
- **Distribution**: `aira.pipelines` topic; idempotent consumer → `pipeline_configs` read-model
  (gateway migration 0004). Management `pipelines` app + `GET/PUT /use-cases/{slug}/pipeline`
  (members read, admins edit) publishes `pipeline.upserted` via the outbox.
- **Angular graph builder** (`features/pipelines`): route `use-cases/:slug/pipeline` renders the
  pipeline as a **clickable node graph** (Request → steps → Dispatch → fallback) with a per-step
  inspector; zoneless-safe signal state. Entry from the use-case detail.
- **Gates green**: backend 285 tests / 99.8% (pipeline modules ~100%), ruff + mypy --strict clean;
  frontend 21 Vitest tests, Prettier clean, `ng build` OK. `FRD-300`/`FRD-303` done. **Phase 3 core
  (pipeline) delivered.**

---

## 2026-08-04 — FRD-205: self-service API-key issuance + UI redesign (closes Phase 2)
- **Backend (Management → Gateway)**: Management is now the source of truth for API keys
  (ADR-0006). New `apikeys` app (model + serializers) with nested endpoints on the use-case
  viewset: `POST/GET/DELETE /api/v1/use-cases/{slug}/api-keys[/{prefix}]`. A member issues a key
  **bound to the use case**, plaintext returned **once**, only the hash stored. `api_key.created`/
  `api_key.revoked` flow through the transactional outbox to a new `aira.api-keys` compacted topic.
- **Gateway**: idempotent consumer upserts/deactivates the `api_keys` read-model; `ApiKey` gains
  `use_case` (migration 0003); a verified api_key `Principal` carries its bound use case, so
  requests need **no `/uc` selector** and a mismatched selector is rejected (403). Shared key
  format/hash extracted to `aira_common.apikeys`. CLI stays as break-glass.
- **Frontend**: use-case detail gains an **API-keys panel** (issue with one-time reveal + copy,
  list masked, revoke) and a members table. Typed `UseCaseService` methods + tests.
- **UI redesign**: global design-system (`styles.scss`) — tokens, cards, buttons, tables, badges,
  callouts; polished app shell (brand header, active-state nav, constrained content). Templates
  restyled with the shared classes.
- **Gates green**: backend 241 tests / 99.95%, ruff + mypy --strict clean; frontend 16 Vitest tests,
  Prettier clean, `ng build` OK.
- **Phase 2 (Management Foundation) is complete.**

---

## 2026-08-04 — Upstream status passthrough (gateway hardening)
- `UpstreamError` now carries the upstream HTTP `status_code` (`None` for transport failures).
- Gemini routes map it: **429 → `429 RESOURCE_EXHAUSTED`**, **503 → `503 UNAVAILABLE`**,
  **504 → `504 DEADLINE_EXCEEDED`**; everything else (upstream 4xx from *our* key/config, upstream
  5xx, transport errors) is masked as a generic **502 UNAVAILABLE** so a broken upstream is never
  mistaken for a client error. Streaming still logs + terminates cleanly (status already sent),
  now including the upstream status.
- Rationale: a client (e.g. opencode) hitting a real Gemini rate-limit should see `429` and back
  off, not a misleading `502`.
- **Gates green**: 225 tests / 99.9% (routes + gemini modules 100%), ruff + mypy --strict clean.

---

## 2026-08-04 — FRD-304: real Google Gemini upstream adapter (Phase 3)
- **Async provider protocol**: `Upstream` (`upstreams/base.py`) is now `async` (`generate`/`embed`
  coroutines, `stream_generate` async-iterator); added `UpstreamError` for upstream failures.
  `MockProvider` updated accordingly.
- **`GeminiUpstream`** (`upstreams/gemini.py`): calls the Generative Language API
  (`generativelanguage.googleapis.com/v1beta`) with an **injectable `httpx.AsyncClient`** so tests
  drive it via `MockTransport` — fully hermetic. API key sent as `?key=` query param, **never
  logged**. Non-2xx / transport errors → `UpstreamError`. `build_gemini_upstream(settings)` returns
  `None` when no key is set; the app registers `[MockProvider(), *gemini]`.
- **Pure mappers** (`upstreams/gemini_mapping.py`): canonical ⇄ Gemini request/response/stream-chunk,
  incl. `systemInstruction`, `generationConfig`, `usageMetadata`, `finishReason` normalisation.
- **Routes**: `generateContent`/`embedContent` return **502 `UNAVAILABLE`** on `UpstreamError`;
  streaming logs the error server-side and terminates the stream cleanly (headers already sent).
- **Config**: `GOOGLE_API_KEY`, `GEMINI_MODELS` (`gemini-2.0-flash,gemini-1.5-flash`),
  `GEMINI_BASE_URL`. `httpx` promoted to a gateway runtime dependency.
- **Gates green**: **222 tests / 99.9%** (new `gemini` modules 100%), ruff + `mypy --strict` clean.
- Enables binding **opencode** (Google provider + custom baseURL) to a use-case with real responses.
- See `docs/features/FRD-304-upstream-adapters.md`.

---

## 2026-08-04 — FRD-203: Angular management shell
- **Auth** (`core/auth`): `angular-oauth2-oidc` code-flow+PKCE against the `aira` realm; `AuthService`
  facade; functional `authInterceptor` (bearer on `/api` calls) + `authGuard` (redirect to login);
  `provideAppInitializer` runs OIDC discovery on startup.
- **API** (`core/api`): typed `MeService` + `UseCaseService` (list/get/create/update/remove +
  members) with models.
- **Shell**: header + **role-aware navigation** (Security/Governance/Administration shown by role
  from `/api/v1/me`), logout.
- **Screens** (lazy-loaded): use-case **list** (+ create form) and **detail** (edit context, member
  add/remove) wired to FRD-202 endpoints.
- **Dev proxy** (`proxy.conf.json`): `/api` → management `:8002`; `make run-frontend` uses it.
- **Gates green**: `ng build` OK (lazy chunks), **13 Vitest tests** pass (interceptor/guard/service/
  list/shell, browserless), Prettier clean. Python side unchanged (202 tests / 100%).
- **Next: FRD-205** (self-service API-key issuance) closes Phase 2.

---

## 2026-08-04 — FRD-204: config distribution over Kafka (Management → Gateway read-model)
- **Transactional outbox** (management `outbox` app): use-case/membership change events are written to
  an `OutboxEvent` row **inside the same transaction** as the change (mutations wrapped in
  `transaction.atomic`; subscriber wired via `events.subscribe` in app-ready). A `relay` command
  publishes pending rows to Kafka and marks them — at-least-once (crash-safe; consumer idempotent).
- **Shared Kafka** (`aira_common.kafka`): `Producer` protocol + `InMemoryProducer` (tests) +
  `AiokafkaProducer` (real; `# pragma: no cover` I/O); topics `aira.usecases`/`aira.memberships`;
  W3C trace context on headers.
- **Gateway consumer**: `apply_event` (idempotent upsert/delete) into read-model tables `use_cases`
  + `use_case_members` (Alembic 0002); `worker` (aiokafka) + `decode_event_type`. `make kafka-topics`
  creates compacted topics; `make relay` / `make consume`.
- **Gates green**: 202 tests, **100% coverage** (pure logic; Kafka I/O pragma-excluded, integration-
  tested); ruff + mypy --strict clean (aiokafka untyped import ignored).
- **End-to-end verified**: created `kafka-uc` in management → outbox rows → `relay` published to Kafka
  → gateway consumer applied → read-model shows `use_cases: kafka-uc` and `use_case_members:
  kafka-uc/demo-user/admin`. Failed publish (missing topic) left rows pending (nothing lost).
- **Next: FRD-205** (self-service API-key issuance, distributed via this backbone) or **FRD-203** (UI).

---

## 2026-08-04 — FRD-202: use-case CRUD + membership
- **`usecases` app**: `UseCase` (slug/name/description/processing_notes) + `UseCaseMembership`
  (unique per user). CRUD at `/api/v1/use-cases/` (DRF ModelViewSet); slug validated to the gateway
  selector charset (`[a-z0-9-]`).
- **RBAC applied** (FRD-201): list is scoped (governance sees all, others see permitted); create needs
  the use-case-admin/global-admin role and makes the creator the use-case admin; edit/delete needs
  `change_usecase` (or global-admin); membership needs `manage_members` (or global-admin). Adding a
  member grants **`django-guardian`** object perms (view; +change/manage for admins).
- **Membership actions**: `POST/GET /use-cases/{slug}/members/`, `DELETE …/members/{username}`.
- **Change hook** (`events.emit`): in-process subscribers on usecase/membership changes — the Kafka
  publisher subscribes here in FRD-204. Migrations excluded from coverage.
- **Gates green**: 190 tests, **100% coverage**; ruff + mypy --strict clean (DRF generics typed).
- **End-to-end verified**: as global-admin `demo-user` created `live-uc`, listed it, added a member,
  and an invalid slug → 400.
- **Next: FRD-203** (Angular shell) or **FRD-204** (Kafka distribution).

---

## 2026-08-04 — FRD-201: RBAC (roles + object-level use-case perms)
- **`aira_management.rbac`**: `sync_user_roles` maps a token's realm roles onto Django groups (the
  five AIRA roles) on every auth — Keycloak is the source of truth. DRF permission classes
  (`IsGlobalAdmin`, `IsITSecurity`, `IsITSteuerung`, `IsUseCaseAdmin`, `IsUseCaseUser`; global-admin
  implies all). `scope_queryset` narrows lists: governance roles (global-admin, it-steuerung) see all;
  others are limited to their **`django-guardian`** object-level permissions.
- **Wiring**: `django-guardian` added (INSTALLED_APPS + object-perm backend; `ANONYMOUS_USER_NAME=None`).
  The auth class calls `sync_user_roles` after provisioning.
- **Gates green**: 174 tests, **100% coverage**; ruff + mypy --strict clean (guardian import ignored).
- **End-to-end verified**: assigned realm role `global-admin` to `demo-user` in Keycloak → token
  carries it → `/api/v1/me` shows `roles:[global-admin]` and the Django group membership is synced
  (`demo-user | global-admin` in `aira_mgmt`).
- **Next: FRD-202** (use-case CRUD + membership, using these RBAC mechanics).

---

## 2026-08-04 — Phase 2 begins · FRD-200: management DRF API + OIDC
- **Shared OIDC** (`aira_common.oidc.JwtVerifier` + `build_jwks_client`): extracted JWT/JWKS
  verification so the gateway **and** management use one implementation. Gateway `OidcValidator`
  refactored to wrap it (behaviour unchanged, tests green).
- **Management DRF foundation**: `api` app with `KeycloakJWTAuthentication` (verifies the bearer JWT,
  auto-provisions a Django user from claims, attaches claims as `request.auth`), a consistent DRF
  **error envelope** (`{"error":{code,message,details}}`), and `GET /api/v1/me` (subject, username,
  email, realm roles, use-case groups). `IsAuthenticated` default; 401 via `authenticate_header`.
- **Gates green**: 167 tests, **100% coverage**; ruff + mypy --strict clean. Hermetic tests use a
  self-signed RS256 + fake JWKS (no Keycloak needed).
- **End-to-end verified**: management backend with `AIRA_OIDC_ISSUER=…/realms/aira` — no token → 401;
  a real Keycloak `demo-user` token → 200 `me` with username/email/groups; user auto-provisioned.
- **Next: FRD-201** (RBAC: realm roles → Django groups + `django-guardian` object-level use-case perms).

---

## 2026-08-04 — Quality: error-safety + test-tier separation (Jenkins-ready)
- **Confirmed the pytest suite is hermetic**: 154→156 tests pass with the **entire Compose stack
  stopped** (in-memory SQLite, fake JWKS, mock provider). The earlier curl checks were *manual*, not
  part of the suite.
- **Two test tiers** for CI: unit tests run by default; stack-dependent tests are marked
  `@pytest.mark.integration` and **excluded** (`-m 'not integration'`). Added `make test-integration`
  and an example integration test; documented in **`docs/TESTING.md`** with a Jenkins pipeline sketch
  (unit stage needs no Docker; integration stage brings the stack up).
- **Error-safety**: added a global exception handler — any unhandled error now returns a
  **Gemini-shaped 500 (`INTERNAL`)** on `/v1beta` (AIRA envelope elsewhere), logs full context
  server-side (path, method, error type/msg, subject, use_case, trace_id), and **does not leak**
  internal details to the client. Tested with a throwing provider.
- **Reviewed**: expected errors already carry contextual messages (model-not-found, missing-method,
  not-a-member-of-use-case, field-located validation errors, unauthenticated). Noted follow-up: OIDC
  fails closed (401) even when Keycloak/JWKS is unreachable — safe, but can't cleanly distinguish
  "provider down" (503) from "bad token" via PyJWT alone.
- **Gates green**: 156 tests, **100% coverage**; ruff + mypy --strict clean.

---

## 2026-08-04 — FRD-104 + FRD-105 — **Phase 1 (Gateway MVP) complete**
- **FRD-104 (mock fidelity + streaming)**: `:streamGenerateContent?alt=sse` now returns
  `text/event-stream` (`data: {json}\n\n`, the google-genai SDK path); the default returns a streamed
  **JSON array** (Gemini REST form). Mock honours `generationConfig.maxOutputTokens` → truncates and
  reports `finishReason=MAX_TOKENS`.
- **FRD-105 (tracing enrichment)**: `aira_common.set_span_attributes(mapping)` sets non-None
  attributes on the current span. `require_attribution` tags `aira.subject/use_case/auth_method`;
  `record_request` tags `aira.model/operation/status/source_ip/total_tokens`.
- **Gates green**: 154 tests, **100% coverage**; ruff + mypy --strict clean.
- **End-to-end verified**: SSE (`text/event-stream`) + JSON-array streaming + `maxOutputTokens`→
  `MAX_TOKENS`; a trace is **searchable in Tempo by `aira.use_case=demo-uc`** (filter traces by use
  case in Grafana).
- **Phase 1 complete**: FRD-100 (Gemini API) · 101 (auth) · 102 (attribution) · 103 (persistence) ·
  104 (mock/streaming) · 105 (tracing). Every request is authenticated → attributed to a use case →
  authorized → dispatched → persisted → traced. **Next: Phase 2 (Management foundation).**

---

## 2026-08-04 — FRD-103: request/response persistence + Alembic
- **`request_logs`** table + `RequestLogService`: persist each dispatched request/response with
  attribution (subject, auth_method, use_case), source IP, model, operation, token usage, status,
  latency, and **trace_id** (correlates to Grafana). Wired into generate/embed/stream routes via
  `record_request`.
- **Source IP** from first `X-Forwarded-For` hop else socket peer. **Redaction hook**
  (`Redactor`/`NoOpRedactor`) + `store_payloads` toggle (metadata-only when off).
- **Alembic** introduced for the gateway DB (`migrations/`, async env, `0001_initial` = api_keys +
  request_logs); `make migrate-gateway`. Dev/tests keep `create_all` (SQLite/bootstrap).
- **Gates green**: 149 tests, **100% coverage**; ruff + mypy --strict clean. Route persistence tested
  via httpx ASGITransport (hermetic SQLite).
- **End-to-end verified**: alembic migrated Postgres; a `:generateContent` call wrote a `request_logs`
  row with subject=demo, use_case=demo-uc, source_ip=203.0.113.7 (XFF), tokens 3/6/9, trace_id set,
  payloads stored.
- **Next: FRD-104** (mock upstream full fidelity) / **FRD-105** (tracing spans + IP on the span).

---

## 2026-08-04 — FRD-102: attribution & use-case selection (OIDC)
- **Problem addressed**: an OIDC token authenticates the *identity*, not *which use case* — a user
  can be in several. Solution: explicit per-request use-case **selector** + membership authorization
  from Keycloak **groups** (no Management DB/Kafka needed yet).
- **Selector**: `/uc/<use-case>/v1beta/...` path (via `UseCasePathMiddleware`) **or**
  `X-AIRA-Use-Case` header; **header overrides path** (per user's choice).
- **Membership**: `Principal.use_cases` derived from token groups under `/use-cases/<slug>`;
  `require_attribution` dependency authorizes `use_case ∈ use_cases` for OIDC (403 otherwise),
  attaches `Attribution(subject, method, use_case)` to `request.state`. `require_use_case` toggle
  (400 when missing). API-key/demo attributed without the group check (binding comes in FRD-205).
- **Keycloak realm**: added `/use-cases/{demo-uc,other-uc}` groups + a group-membership protocol
  mapper (`groups` claim); demo-user ∈ `/use-cases/demo-uc`.
- **Gates green**: 138 tests, **100% coverage**; ruff (+ FastAPI `Depends` bugbear config) + mypy
  --strict clean.
- **End-to-end verified**: real Keycloak token carries `groups`; `/uc/demo-uc` → 200, `/uc/other-uc`
  → 403 PERMISSION_DENIED, header overrides path → 200, no use case → 200.
- **Next: FRD-103** (persist request/response + attribution), then FRD-104/105.

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
