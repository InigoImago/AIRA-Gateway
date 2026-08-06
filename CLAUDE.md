# AIRA Gateway — Project Guidance (CLAUDE.md)

Guidance for anyone (human or AI) working on **AIRA Gateway — AI REST API**.
Read this first, then `docs/PRD.md` and `docs/ROADMAP.md`.

> Note: the sandbox/environment guidance lives in the parent `../CLAUDE.md`. **This** file is about
> the AIRA project itself: what we're building, how we build it, and the conventions to follow.

---

## 1. What we are building
An enterprise-grade AI gateway with two self-developed components + open-source infrastructure:
- **Gateway API** (data plane) — FastAPI.
- **Management & Monitoring** (control plane) — Angular SPA + Django REST Framework.
- Infra: PostgreSQL, Keycloak (SSO), Apache Kafka (event bus), HashiCorp Vault (secrets),
  OpenTelemetry Collector → SigNoz (observability). The two components communicate over **Kafka**.

Full detail: `docs/PRD.md`. Delivery is phased: `docs/ROADMAP.md`.

## 2. Locked-in decisions (see `docs/adr/` for rationale)
- **Language**: all docs, code, and identifiers in **English**.
- **Management UI**: **Angular** (TypeScript SPA) + **Django REST Framework** backend.
  Django keeps ORM/migrations/`django-guardian` object-level RBAC/admin; Angular is the frontend.
- **Gateway**: **FastAPI** (Python **3.14**).
- **Toolchain** (see `ADR-0003`): **Python 3.14 + uv**, **Node 26** (Angular). Pin versions in
  `pyproject.toml`/`.python-version` and `package.json` `engines`/`.nvmrc`.
- **AuthN**: Keycloak OIDC (bearer) **and** self-generated API keys (hashed at rest).
- **Roles (initial)**: Global Administrator, IT Security, IT Steuerung (Governance),
  Use Case Administrator, Use Case User. Least-privilege, object-scoped.
- **Secrets**: only in **HashiCorp Vault** — never commit secrets.
- **Observability**: OTLP → OpenTelemetry Collector → **Grafana `otel-lgtm`** locally (ADR-0004,
  supersedes the earlier SigNoz choice in ADR-0002).
- **Deployment**: **Docker Compose** locally now; Kubernetes/Helm later.
- **Demo mode**: mock upstream + one-command **automated seeding** must always work.

## 3. Engineering conventions
- **Test-first / high coverage**: near-100% unit-test coverage is a hard goal; **CI enforces the
  gates** (`.github/workflows/ci.yml`) — Python via `pytest --cov-fail-under`, Angular via
  `coverageThresholds` in `angular.json`. `make ci` runs exactly what CI checks, locally.
  Every feature ships with tests. No feature is "done" without tests. Frontend tests assert the
  **rendered DOM and real interactions**, not just component methods.
- **A green test proves nothing on its own.** It proves the code and the test agree, which they
  inevitably do when both were written from the same mental model — and line coverage cannot see
  a *missing requirement*: on 2026-08-05 a review found seven real defects behind a green suite at
  99% coverage. So: **prove a test can fail.** Break the property, watch it go red, restore.
  `make mutants` (`tools/mutation_check.py`) does this for **88 properties** across auth, budgets,
  pipeline, retention, the management control plane and the gateway's counters; when
  you fix a bug, add the mutation that reintroduces it. Two traps that cost real defects here:
  a stand-in that is more permissive than the thing it replaces (reuse the real method where you
  can), and a test whose setup never reaches the path it is named after — SQLite enforces no
  column lengths, and `TestClient` buffers a whole streamed body before you can hang up.
- **Three test layers**, each for what the layer below cannot see:
  `unit` (hermetic, `make test`) → `tests/integration/` (live stack, `make test-integration`)
  → `e2e/` (real browser, `make test-e2e`). Anything needing a user token belongs in `e2e/`: the
  dev realm has the password grant disabled, so a token only comes from the real code flow.
- **Typed code**: Python type hints (mypy), TypeScript strict mode.
- **A page is a parent plus panels.** `use-case-detail` grew to 1238 lines and six concerns
  before it was split: the parent loads and owns the tab bar (whose counts must exist before any
  tab is opened, which is why loading stays there), and each panel is a child owning its form
  state and mutations. A new tab is a new child, never another block in the parent. Outcomes go
  through the page's single `PageFeedback` — one banner per page, not one per panel.
  In a child, an `input()` is a **signal**: `{{ slug }}` renders the function, `{{ slug() }}`
  renders the value, and only a browser will show you the difference.
- **Angular is zoneless**: all mutable component state must be a `signal`. A plain property
  changed from code schedules no re-render, so `[(ngModel)]` is written as
  `[ngModel]="x()" (ngModelChange)="x.set($event)"`. See FRD-203 §4.
- **No silent failures in the UI**: every load and mutation reports its outcome through
  `core/api/error-message.ts`, which surfaces the backend's error envelope.
- **Lint/format**: Python (ruff + black), Angular (eslint + prettier). CI blocks on violations.
- **API contracts**: OpenAPI for HTTP; explicit, versioned schemas for Kafka events.
- **Config over code**: behavior driven by use-case configuration, not hard-coded branches.
- **Async on the hot path**: persistence and event emission must not block the gateway request path.
- **Security by default**: validate input, scope every query by role/object, redact where required.

## 4. Documentation discipline (IMPORTANT — keep this current)
Always keep documentation in sync with what is actually built. On any meaningful change:
1. **ADRs** — record every significant/architectural decision as an ADR in `docs/adr/`
   (copy `docs/adr/ADR-TEMPLATE.md`, increment the number, link it from `docs/adr/README.md`).
2. **FRDs** — before building a feature, write/refresh its FRD in `docs/features/`
   (from `docs/features/FRD-TEMPLATE.md`). Update it if the implementation deviates.
3. **DEVLOG** — append a dated entry to `docs/DEVLOG.md` summarizing what changed and why.
4. **PRD/ROADMAP** — update these when scope, phases, or requirements shift.
5. Keep this `CLAUDE.md` updated as conventions evolve.

Rule of thumb: if a future contributor would be surprised or have to reverse-engineer a decision,
write it down (ADR or DEVLOG). Prefer small, frequent updates over big retroactive ones.

## 5. Repository layout (target)
```
AIRA/
├── CLAUDE.md                  # this file
├── README.md
├── docs/
│   ├── PRD.md                 # requirements
│   ├── ROADMAP.md             # phases
│   ├── DEVLOG.md              # running change log
│   ├── adr/                   # architecture decision records
│   └── features/              # FRDs (one per feature)
├── gateway/                   # FastAPI data plane
├── management/
│   ├── backend/               # Django + DRF control plane
│   └── frontend/              # Angular SPA
└── deploy/                    # docker-compose, later helm/k8s
```
(Directories are created as phases begin; not all exist yet.)

## 6. Current status
**Phase 0 (Foundation & Infra) complete.** Local Compose stack (postgres, keycloak, kafka,
schema-registry, vault) runs via `make up`. uv workspace with three Python-side packages
(`aira_common`, `aira_gateway`, `aira_management` = Django+DRF) plus an Angular 22 frontend shell —
all with `/healthz`+`/readyz`, 100% Python coverage, and green ruff/mypy/prettier gates.
Observability (`FRD-001`): OTel Collector + Grafana `otel-lgtm` (traces verified in Tempo). Seed &
demo (`FRD-002`): extensible seed framework + `seed_demo` command creating the 5 roles/users
(verified end-to-end against Postgres); deterministic mock upstream for demo mode.
**Phase 0 complete. Phase 1 in progress:** `FRD-100` done — Gemini-compatible API
(`/v1beta/models/{model}:generateContent|:streamGenerateContent|:embedContent`, list/get models)
on a provider-agnostic canonical core, served by the mock provider (verified end-to-end).
API direction: **Gemini first, OpenAI later** (ADR-0005). `FRD-101` **complete** — auth on the Gemini
routes via **API keys** (`aira_<prefix>_<secret>`, hashed; `x-goog-api-key`/`?key=`/Bearer) **and**
**OIDC bearer** (Keycloak JWKS; realm `aira` under `deploy/compose/keycloak/realms/`). Gateway has a
SQLAlchemy-async DB layer; CLI mints keys; `auth_required`/`oidc_enabled` toggles. `FRD-102` done —
**use-case attribution**: selector via `/uc/<use-case>` path or `X-AIRA-Use-Case` header (header
wins), OIDC membership authorized from Keycloak **groups** (`/use-cases/<slug>` → `Principal.use_cases`;
403 for non-members), `require_use_case` toggle. `FRD-103` done — **persistence**: `request_logs`
table stores every dispatched request/response with attribution, source IP (XFF), tokens, latency,
and trace_id; redaction hook + `store_payloads` toggle; **Alembic** migrations (`make migrate-gateway`,
dev/tests still `create_all`). `FRD-104` done — **streaming fidelity**: SSE (`?alt=sse`) for the
google-genai SDK + JSON-array default; mock honours `maxOutputTokens` (→`MAX_TOKENS`). `FRD-105` done —
**tracing enrichment**: `aira.*` span attributes (subject/use_case/model/…) filterable in Grafana/Tempo.
**Phase 1 (Gateway MVP) is complete.** **Phase 2 in progress:** `FRD-200` done — management **DRF API**
+ **OIDC bearer auth** (Keycloak JWT via shared `aira_common.oidc.JwtVerifier`; auto-provisions users)
+ `GET /api/v1/me` + consistent error envelope. `FRD-201` done — **RBAC**: `sync_user_roles` (token
realm roles → Django groups, Keycloak is source of truth), DRF role permission classes,
`scope_queryset` (governance sees all, else `django-guardian` object-level). `FRD-202` done —
**use-case CRUD + membership** at `/api/v1/use-cases/` (scoped by RBAC; membership grants guardian
object perms; change hook `events.emit`). `FRD-204` done — **Kafka config distribution**: management
**transactional outbox** + `relay` → Kafka (compacted topics) → gateway **idempotent consumer** into
read-model tables (`use_cases`/`use_case_members`). `aira_common.kafka` (aiokafka); `make
kafka-topics/relay/consume`. Verified end-to-end. `FRD-203` done — **Angular shell**: OIDC login
(angular-oauth2-oidc), bearer interceptor + auth guard, role-aware nav from `/api/v1/me`, use-case
list/detail screens (dev proxy `/api`→:8002). `FRD-205` done — **self-service API-key issuance**
(ADR-0006): Management issues keys **bound to a use case** (plaintext shown once, hash stored),
distributes `api_key.*` over Kafka (`aira.api-keys`) → gateway `api_keys` read-model; a verified
api_key `Principal` carries its use case (no `/uc` selector needed; mismatched selector → 403).
Shared key format in `aira_common.apikeys`; Angular use-case detail gets an API-keys panel; UI
restyled with a global design-system. **Phase 2 (Management Foundation) is complete.**
**Phase 3 in progress:** `FRD-304` done — **real Google Gemini upstream adapter** (async `Upstream`
protocol + `UpstreamError`; injectable `httpx.AsyncClient`, hermetic `MockTransport` tests; key never
logged; registered only when `AIRA_GOOGLE_API_KEY` is set). Gateway also **passes upstream status
codes through** (429→RESOURCE_EXHAUSTED, 503→UNAVAILABLE, 504→DEADLINE_EXCEEDED; else 502).
`FRD-300`/`FRD-303` done — **pre-dispatch pipeline** (`aira_gateway/pipeline/`): per-use-case,
config-driven steps run before dispatch — `injection_filter` (heuristic w/ visible built-in +
custom patterns, or LLM-backed; scope user|system+user; block|flag), `allow_check` (model
allow-list), `model_route` (**LLM classifier** reads system+user, picks a configured category →
model; `FRD-306`) — then a `fallback_models` dispatch chain. Default = pass-through. Config authored
in Management (`GET/PUT /use-cases/{slug}/pipeline`) → `aira.pipelines` Kafka → gateway
`pipeline_configs` read-model (migration 0004); Angular **clickable graph builder** at
`use-cases/:slug/pipeline` with **inline help + a test panel** (client-side live preview + real
**dry-run** via `POST /v1beta/pipeline:dryRun`, `/gw` proxy). **Phase 3 core (pipeline) delivered.**
Backlog: `FRD-307` (Global-Admin-approved model catalog + builder pickers, documented), drag-drop/
parallel branches, authenticated dry-run, `FRD-106` (OpenAI surface).
**Phase 4 (Budgets & Quotas) in progress:** `FRD-400` done — Management `budgets` app + `GET/POST/
DELETE /use-cases/{slug}/budgets` (scope use_case|member, period day|month, token/request limits) →
`aira.budgets` Kafka → gateway `budgets` read-model (migration 0005). `FRD-401` done — gateway
`BudgetService`: pre-dispatch `guard` rejects over-budget requests with **429 RESOURCE_EXHAUSTED**,
post-dispatch `record` increments the `budget_usage` counters (keyed by scope+period, resets at
day/month boundaries; migration 0006; `enforce_budgets` toggle). `FRD-402` done — **Budgets tab** in
the use-case detail: set use-case/member limits + see consumption bars; usage from gateway
`GET /v1beta/usage/{use_case}` (`/gw` proxy). **Phase 4 (Budgets & Quotas) complete.**
**Security hardening pass (`ADR-0007`, no new features)** — closed authorization gaps (dry-run +
usage endpoints now authenticated; API-key issuance requires **membership**, not just visibility;
Django users bound to the Keycloak `sub` via `OidcIdentity`), safe defaults (management refuses to
boot outside `local` with dev `SECRET_KEY`/`DEBUG`/`ALLOWED_HOSTS=*`; security headers; `X-Forwarded-For`
only via `AIRA_TRUST_FORWARDED_FOR`; `?key=` redacted from spans; revocation terminal in the read-model;
`seed_demo` local/demo only), and input bounds (body ceiling `AIRA_MAX_REQUEST_BYTES`; slug-validated
use-case selector; pipeline-config bounds + **nested-quantifier regexes rejected** — ReDoS). Frontend:
`requireHttps: 'remoteOnly'`, CSP, encoded URL segments, bearer scoped to `/api` + `/gw`. Keycloak dev
realm: pinned redirect URIs/web origins, password grant off. **The SPA's dry-run/consumption views now
need `AIRA_OIDC_ENABLED` on the gateway** (see `.env.example`); both degrade gracefully without it.
**Management UI pass** (2026-08-05, no new screens): fixed two zoneless re-render bugs (forms kept
submitted text; scope-dependent fields never appeared) by moving all form state to signals; every
load/mutation now surfaces the backend error envelope (`core/api/error-message.ts`) instead of
failing silently; loading vs. empty states; confirmations on destructive actions; inline validation;
width-overflow fixes throughout (scrollable tables/nav/tabs, `min-width:0`, long-identifier
breaking, capped sticky inspector, wide builder ≥1200px); accessibility (tablist semantics, labels,
accessible names, focus rings); deep-linkable tabs. Frontend coverage **53.8% → 92.3%** statements
(30 → **134** tests) with a gate in `angular.json`.
**Cost-based budgeting (`FRD-403`, 2026-08-05)**: budgets can cap **spend**, not just tokens — a
token differs in price by >10x between models and output is billed several times higher than
input, so a token cap was never a cost control. Management gains a **model catalog with prices**
(global-admin only, the price half of `FRD-307`) distributed over `aira.models`; the gateway
prices each request from the prompt/completion split, records `request_logs.cost_nanos`, and
enforces `limit_cost` with 429. **Money is integer nano-units, never a float** (`aira_common.money`;
amounts cross APIs as decimal strings), and **unpriced traffic is counted apart, never as zero**.
New SPA screen **Models & prices**.

**Verified against the live stack (2026-08-05)**: new `e2e/` (Playwright, 22 tests, real browser)
and `tests/integration/` (12 tests, live stack). The run found three defects the hermetic suites
could not: the hardened realm's client description exceeded Keycloak's `varchar(255)` and broke
its boot; the dev realm had none of the five AIRA roles, so the documented demo acceptance could
not pass; and the pipeline builder discarded edits made before its config had loaded. All fixed.
Note: Keycloak imports a realm only if it does not exist — recreate it after editing
(`deploy/compose/README.md`).
**CI (2026-08-05)**: `.github/workflows/ci.yml` — three jobs (Python lint/types/tests, frontend
format/build/tests, and the full containerised stack with integration + Playwright e2e). The
workflow is a thin wrapper around `make` targets so CI and a local run cannot drift; `make ci`
reproduces the hermetic half. Node is now pinned per ADR-0003 (`.nvmrc` + `engines`).
**Storage & retention (`FRD-404`, 2026-08-05)**: payload storage is switchable **per use case**
(`store_payloads`, default on; `AIRA_STORE_PAYLOADS` is a kill switch above it) and stored
prompts/responses expire per use case, **default 7 days**, enforced by `python -m aira_gateway.retention` (hourly container; **schedule it or nothing
is deleted**). Payload retention and record retention are separate clocks — whole-row deletion is
off by default so the cost reporting (FRD-403) keeps its horizon.
**Rate limiting & atomic budgets (`FRD-405`, `ADR-0008`, 2026-08-05)**: **Redis** joins the stack
as the shared counter store, because two things were being decided on stale state. Per-use-case /
per-member **rate limits** (token bucket, refill-test-take in one Lua script) now hold across
gateway instances — per-process counters would let N replicas behind a load balancer allow N × the
limit; over the limit is a **429 with `Retry-After`**. Budget `guard` **reserves** before dispatch
and `settle`/`release` reconcile afterwards, closing the race in which N concurrent requests all
passed a limit with room for one (proved by a test pair: 20/20 pass on the old path, 1/20 on the
new). Postgres stays authoritative and seeds the counter on a miss. Degradation is decided:
without Redis, limits fall back to a **per-instance** bucket (not fail-open — that is the worst
moment to stop bounding a caller) and budgets to the old **Postgres** path (enforcing but racy);
`/readyz` reports `degraded: true` and still returns 200. The **request-log write moved off the
hot path** (bounded queue, drained on shutdown, inline when full — never dropped), finally
honouring §3's "async on the hot path". New SPA tab **Rate limits**.
**Review pass on FRD-405 (2026-08-05)** — a structured audit (four parallel reviews, every
finding re-verified by hand) found seven defects in the same day's work and fixed each with a test
that was first shown to fail: a refused member drained the whole use case's bucket (the token
decision is now **all-or-nothing across scopes**); reservations leaked on every failure that was
not an `UpstreamError`, on a failed stream, and on a client disconnect (`BudgetService.hold` makes
release-unless-settled structural); **`embedContent` bypassed both controls** (all verbs now pass
one shared pre-dispatch gate); two Redis edge cases (a half-made reservation is handed back, and
counters expire in **five minutes** and rebuild from Postgres so drift cannot outlive the period);
and the audit writer dropped rows submitted during shutdown. `DEPLOYMENT.md` had also kept "No
rate limiting" as a known gap and omitted `aira.rate-limits` from its topic list — a **silent**
failure, since a missing topic produces no error anywhere.
**Spend and usage reporting (`FRD-601`, 2026-08-06)**: the request log has been collected since
Phase 1 and priced since `FRD-403`, and nothing read it. The gateway now serves
`GET /v1beta/reporting?from=&to=` — totals plus breakdowns by use case, model and member — and the
SPA gains a **Reporting** screen. The visibility rule sits at the edge in one function: governance
sees every use case, anyone else sees the ones their token puts them in, and a caller with neither
gets an **empty report rather than a refusal** (`None` = everything and `()` = nothing are distinct
values on purpose). Latency is an **average and a maximum**, and is called that — `percentile_cont`
is Postgres-only and the hermetic tests run on SQLite. Unpriced traffic stays counted apart, and
the screen says the spend is a lower bound whenever there is any. Per-request *browsing* waits for
`FRD-406` (ADR-0009).
**KIRA parity programme (`ADR-0010`, 2026-08-06)** — AIRA is the successor to **KIA-KIRA-API**
(`kira_api.md`) and must carry all of its functionality. Reviewed against the code: in breadth we
are well ahead (the predecessor has no use cases, budgets, limits, pipeline or UI); in the **core
request path** we are behind. `CanonicalMessage` carries one field, `text: str`, and the Gemini
`Part` requires `text`, so a request with `inlineData` is **rejected with a 400** — documents and
images, thinking budgets and structured output do not exist here at all. Also: **Vault is in the stack but no code reads from it** despite §2's policy.
**Confirmed 2026-08-06 — EU residency applies, and models are reached through the Gemini Enterprise
platform's Model Garden: Gemini *and* Anthropic, one project, one credential.** So `FRD-115`
(Vertex, EU-regional, service-account) is required, not optional — and AIRA gains a **second wire
dialect**: Anthropic on Vertex uses `:rawPredict` with the Anthropic Messages API — **`max_tokens`
required**, **thinking blocks returned** (must be dropped, never persisted), **no `responseSchema`**
(structured output is a forced tool call), **no embeddings**. That is `FRD-119`, and it is the first
real test of whether the canonical core is provider-agnostic or merely Gemini-shaped. **A third platform is wanted: Microsoft Foundry** (Azure OpenAI + Microsoft's own models) —
planned, not scheduled, and it is what *decides* the upstream shape: two vendors can be reconciled
with a conditional, three need a structure. **`ADR-0011`** records it — **transport × dialect ×
model identity**: a transport owns reaching the cloud (endpoint, credential, region), a dialect owns
the API shape, and **the caller's model name is never the platform's addressing** (an Azure
*deployment* is not a model, and pricing must attach to the underlying model or spend figures go
quietly incomplete). Credential acquisition is **one shared `TokenSource`** with three
implementations, not three refresh races. Capability flags say **whether, never how** — three
vendors now do structured output by three unrelated mechanisms. Note: the **OpenAI dialect arrives
as an upstream regardless of `FRD-106`**, which makes that deferred surface much cheaper later.
**Four model families under one gateway (`ADR-0012`)**: Gemini, Claude, GPT, and Nemotron from
Model Garden's **self-deploy** side. One namespace, one capability vocabulary — flags say *whether*,
never *how*; **undeclared means unsupported**. Governing principle: **hide the plumbing, declare the
semantics** — a difference that changes the *answer* is never hidden. The case that matters:
**Gemini and Claude read PDFs natively, GPT and a NIM-hosted Nemotron cannot**, so a fallback chain
**skips** an incapable candidate rather than dropping the attachment, and fails if none qualifies —
a stripped-attachment fallback returns a confident answer about a document the model never saw, with
a 200. Optional, opt-in, never-silent conversion is `FRD-121` (recommendation: do not build first).
Self-deployed models also mean **cold starts of minutes and capacity-shaped 429s**, so `hosting` is a
declared property that the dispatch timeout, the retry decision and the readiness probe read — and
the probe must **not** wake a scaled-to-zero endpoint. Sixteen documents: `ADR-0010`–`ADR-0012` +
`FRD-107`, `FRD-110`–`FRD-121`, `FRD-602` (ROADMAP Phase 8). **One decision open** (`ADR-0010`): does AIRA serve the predecessor's wire contract, so
clients migrate by changing a URL, or do the clients move to the Gemini surface? Recommendation:
compatibility surface **with a sunset date and its usage in reporting** — everything except
`FRD-107` is contract-independent and can start now. Three deviations from the predecessor are
deliberate and written down: TLS verification stays on, CORS is an allow-list not `*`, and
`GET /models` requires auth. The OpenAI surface (`FRD-106`) is deferred so parity is not competing
with a second new contract.
Next candidates: **`FRD-114`** (model metadata — now also carries publisher + default output cap,
prerequisite for 110–113 and 119), **`FRD-110`** (documents/images — the widest gap),
**`FRD-115`/`FRD-119`** (Vertex EU + the Anthropic dialect — required), **`FRD-116`** (Vault),
**content redaction** (`FRD-406`, the `Redactor` hook is still a no-op —
deliberately deferred, see the ROADMAP backlog), budget
threshold alerting, Phase 5 (anomaly/IT-Security), `FRD-307` (model catalog), `FRD-106` (OpenAI
surface). See `docs/DEVLOG.md`.

## 7. Working agreement
- Confirm scope via PRD/FRD before large changes; work phase by phase.
- Read relevant files before editing; follow existing patterns.
- Run tests after changes; never weaken the coverage gate to make tests pass.
- Ask when requirements are ambiguous rather than guessing.
