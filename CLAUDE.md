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
  `make mutants` (`tools/mutation_check.py`) does this for **346 properties** across auth, budgets,
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
- **A surface parses; the layer decides.** Both halves of the request path now have one owner —
  `prepare_for_dispatch` before dispatch (`FRD-126`) and `accounting` after it (`FRD-128`). The
  second was found by asking whether every path had been tested with a dropped connection: four of
  six lost the audit row when a caller went away mid-answer. A request that reached an upstream is
  recorded however it ended, including `499`/`client_gone`. `api/serving.py` shared the *steps* of the pre-dispatch
  path with both API surfaces and not their *order* — and every guarantee that layer makes is a
  guarantee about the order (rate limit before the pipeline, declaration and thinking after
  routing, reservation last). Both surfaces wrote the same six calls by hand until `FRD-126`;
  the third would have written them again. `prepare_for_dispatch` owns the sequence, a surface owns
  parsing and its error envelope, and `test_surface_layering.py` fails on a surface that calls a
  step directly. A layering rule only a reviewer enforces is one the next surface breaks.
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

### Reader-facing documentation (added 2026-08-07)
The ADRs and FRDs record *why*; six documents record *what*, and the `README.md` is a hub linking
them: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (C4 in Mermaid),
[`docs/REQUEST-LIFECYCLE.md`](docs/REQUEST-LIFECYCLE.md) (one request, every control, in order),
[`docs/SETUP.md`](docs/SETUP.md) (demo · standalone · dev · integrated),
[`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) (every `AIRA_*` variable, defaults dumped from the
settings classes rather than remembered), [`docs/INTEGRATIONS.md`](docs/INTEGRATIONS.md) (what each
connected system must provide) and [`docs/GAP-ANALYSIS.md`](docs/GAP-ANALYSIS.md) (requirements
against reality). Licence: **Apache 2.0** (`LICENSE`, `NOTICE`). When a feature changes what a
reader would do or expect, the relevant one of those six changes with it — a link checker and the
settings dump make the mechanical half cheap.

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
parallel branches, authenticated dry-run. (`FRD-106`, an OpenAI-compatible **surface**, was
**withdrawn on 2026-08-07** — it was a thought experiment about generalisation, and the OpenAI
*dialect* as an **upstream** is unaffected.)
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
the probe must **not** wake a scaled-to-zero endpoint. **Scope, settled (`ADR-0013`, 2026-08-06): direct model access — the gateway provides *auditable
brains* for AI use cases.** Not agents: no platform agent surfaces, no retrieval or vector storage,
no conversation state, no tool execution, no workflow orchestration. The test for any future
request: *does this make model access better governed and better evidenced, or does it make the
gateway think for the use case?* A review against that word found four audit gaps — most
importantly that a **refused request leaves no row at all** (rate-limited, over budget, unknown
model, invalid: the log records what was *served*, not what was *asked*), plus served-vs-requested
model invisible after fallback, pipeline decisions only on a **sampled** span, and degradation
global rather than per-request. **`FRD-122` closed all five on 2026-08-06** (migration `0012`, mutations `T1`–`T8`): refusals are
recorded at the route's **exception boundary** — one site, because a fact repeated at every
`return` is a fact eventually forgotten at one of them; `requested_model` sits beside `model`
(which keeps its meaning, so existing reports and indexes still hold); pipeline decisions are
persisted through an **allow-list** so a future step cannot start storing the classifier's
reasoning by default; the API-key **prefix** identifies the calling system; degradation is frozen
onto the row; and reporting counts refusals by outcome. Two findings while building it: a full
writer queue was turning a correct **429 into a 500** (guarded on the refusal path only — on the
success path a failed write means a served request went unrecorded, and failing loudly is right),
and a request routed elsewhere then refused was naming the model the caller typed rather than the
one attempted. Nineteen documents: `ADR-0010`–`ADR-0013` + `FRD-107`, `FRD-110`–`FRD-122`, `FRD-504`,
`FRD-602` (ROADMAP Phase 8 / 5).
**`FRD-114` done (2026-08-06)** — the model catalog is now a **runtime authority**: one shared
vocabulary (`aira_common.models`), Management validates a declaration *where it is written* (a
thinking maximum at or above the output cap describes a model that could never answer), the event
carries everything (the gateway never asks Management on the request path), and `ModelCatalog`
turns it into decisions. **Undeclared means the baseline and nothing more** — absence of
information is not permission, the same rule as "unpriced is not free". Enforced today: output cap,
per-model default cap, `generate`/`embed`, and a deprecation `Warning` header (deprecation **warns,
revocation blocks** — conflating them removes the ability to announce a retirement). `model_prices`
was renamed to `model_catalog`, and the rename exposed a real hazard: an old container's
`create_all` **resurrected the dropped table** and then failed every event against it —
`create_all` alongside Alembic means a partially-deployed stack can undo a migration.
**`FRD-115`+`FRD-119` done (2026-08-06)** — **Vertex EU with Gemini and Anthropic.** One
`VertexTransport` (URL, OAuth, region, errors) under two dialects; the shared `TokenSource`
(`aira_common.tokens`) refreshes ahead of expiry, single-flights, and serves through a failed
refresh — written once because getting that race right per platform means getting it wrong on the
second. **Residency is enforced, not intended**: a model outside the allowed regions **refuses to
start**, and provider/publisher/region are on every audit row (migration `0014`) so an EU claim is
evidence rather than configuration. An **ambiguous routing table refuses to boot** — with three
adapters, last-registration-wins becomes a silent choice of region and credential. Anthropic
specifics: `max_tokens` always sent, **thinking blocks dropped**, cache tokens counted as input,
streamed usage **accumulated** across `message_start` and `message_delta`, no embedding.
**The architecture assertion is a test**: `test_no_code_above_the_adapters_knows_the_vendor` parses
every module outside `upstreams/vertex/` and fails if a vendor appears in code. It passes.
**The dispatch chain now takes conditions (2026-08-06, `ADR-0012` §3 implemented).** A candidate
that fails one is **skipped with its reason kept**; an exhausted chain raises `NoCapableModel` →
**400 FAILED_PRECONDITION**, not the 502 it used to be — "every candidate was excluded" is fixable
by an operator, an outage is not. **Residency is the first condition**, media types (`FRD-110`) and
the schema capability (`FRD-112`) are the next two, and they share the mechanism rather than each
inventing one. Two present-day defects fell out: a model no provider served was a **silent
`continue`** (a typo in a fallback chain was invisible), and the candidates a chain passed over now
reach the audit row. Residency is **one policy for every cloud** (`aira_gateway.residency`, `AIRA_ALLOWED_REGIONS`):
Google's `europe-west1` and Azure's `westeurope` in one list, defaulting to the EU regions of every
supported cloud — a per-cloud setting would mean a per-cloud audit. It is still **deployment-wide**;
per-use-case regions are a governance extension, not a bug fix, and are not built.
**`FRD-110` done (2026-08-06) — documents and images.** `CanonicalMessage` carries **ordered
parts**; `text=` still constructs and `.text` still reads, so the whole existing suite passed
unmodified — but `.text` is now **lossy**, which is the pipeline's stated blind spot (a prompt
injection inside a PDF is invisible to the injection filter). **The rule that matters: a model that
cannot read the attachment is refused by name, never sent the prompt without it** — a dropped
attachment produces no error, it produces a fluent wrong answer with a 200 and the caller blames
the model. Checked **after routing at every hop**, so a fallback skips an incapable candidate and
an exhausted chain fails. Attachment bytes are stripped before redaction and unconditionally
(stripping is not redaction); the reservation counts attachment tokens (a document is input no
character count predicts); the mock *sees* attachments or the feature would only ever be exercised
against a cloud nobody has in CI.
**The integration layer found a defect the hermetic one structurally cannot see**: a client
dropping a real socket **cancels** the response task, so a bare `await` in the streaming `finally`
lost the settle and the audit row — in-process `aclose()` raises `GeneratorExit` and works fine,
which is why the hermetic test passed. Now `asyncio.shield`ed, and deliberately given **no**
mutation entry, because no hermetic test can tell the two apart and a harness that claims otherwise
is worse than none.
**`FRD-107` Stage A done (2026-08-06) — the KIRA surface.** `/kira/api/external` with the
predecessor's shapes, error envelope and codes, integer model ids, attribution (one membership, or
an `X-AIRA-Use-Case` header, else **403 naming the candidates** — never an unattributed bucket), and
`Deprecation`/`Sunset` on every response. **It carries documents**, because `FRD-110` landed first.
`thinking` and `responseSchema` are **refused by name**, and so is a model whose catalog declares a
non-`disabled` default thinking mode — the predecessor applies that default, so serving it with no
thinking would answer differently for a reason nobody could see. `/embed` likewise refuses a list
and a `task_type` rather than approximating.
**The controls are now extracted, not copied**: `api/serving.py` holds the pre-dispatch gate,
pipeline, dispatch chain and audit writer, and both surfaces use it — a second copy would be the
`:embedContent` failure with a whole API to hide in. `test_a_kira_request_is_audited_exactly_like_
a_gemini_one` compares the audit rows the two produce, which is the only way to be sure no step was
skipped rather than merely present.
**`FRD-111`/`112`/`113` + `FRD-107` Stage B done (2026-08-06).** Thinking, structured output and
embedding options — and the KIRA surface serves them, since building a capability and continuing to
refuse it at the compatibility layer helps nobody. The wire format did not change.
Three rules came out of it that generalise:
- **`None` and "off" are different answers.** A model whose catalog declares a default thinking
  mode applies it; a request asking for `disabled` must say so *explicitly* or the default wins.
- **One flag, three mechanisms** (`ADR-0011` rule 3). `structured_output` says whether a model can
  return a document; Gemini uses a schema parameter, Anthropic a forced tool call, Azure a third.
  The catalog never learns how. The schema is parsed (so an unknown field is an error **naming the
  field**) and then **forwarded, never executed** — re-validating would run caller-supplied regexes
  over provider output on the hot path.
- **A batch weighs what it is.** `FRD-405`'s bucket now takes a `cost`: a batch of n takes n, or
  the limit is intact on paper and gone in practice. Same for the budget's request count.
Capability checks for thinking and schemas run **per hop** of the dispatch chain, for the same
reason attachments do — a chain that quietly answers with less than was asked for returns a 200.
**A real model in the stack (`FRD-123`, 2026-08-06)** — Ollama behind a `verify` Compose profile,
**built as the OpenAI dialect** rather than against its native API, because `ADR-0011` says that
dialect arrives regardless (Azure needs it) — so `FRD-120` shrinks to a transport. The mock agrees
with us by construction; a model that never agreed to anything is the only way to know the
accounting is right rather than merely self-consistent. Two traps the dialect hides: **usage
arrives in a chunk with an empty `choices` array**, and the vendor reports **no** stream usage
unless `stream_options.include_usage` is sent — and a stream reporting none is *released*, so
forgetting it makes every streamed request silently free. A `limited` thinking budget has no
faithful mapping (`reasoning_effort` is a level, not a budget) and is **refused, not rounded**.
The architecture assertion caught the new dialect importing from the Anthropic one; the right fix
was that `to_json_schema` was never vendor-specific and now lives in `core/schema.py`.
**Still open, deliberately**: this sandbox denies `registry.ollama.ai`, so no model has been pulled
— the adapter is hermetically tested (38 tests), the five integration tests skip with a reason, and
the seed declares **neither** thinking nor structured output for local models, because absence of
information is not permission. Local prices are **invented and say so in their display name**.
**Verified against a running model (2026-08-06).** Ollama is attached as **named servers**
(`AIRA_OPENAI_SERVERS`, `name=url|models|embeddings|region`) — a self-hosted fleet is several
machines and each is audited under its own name. Live suites now cover the governed path (14
cases), the real model (8), and fallback/limits/retention/KIRA (11). What they found:
**a model name may contain a colon** (`qwen3:0.6b` split at the first one produced "Model 'qwen3'
not found"); **a comment claimed a rule the system did not have** (a local region *is* checked, so
a server now declares none unless the operator names one, and naming one is a startup check);
and **the budget counter was racy in two ways**, one of them silent — read-then-write lost
increments under load, and the system of record drifted below the truth in the direction that
spends money. Closed with an upsert; both tests shown to fail against the old code first (`B8`).
**Two open questions are now measured, not assumed**: thinking is billed **inside**
`completion_tokens` (`FRD-111` FR-6 — pricing needs no special case), and the reasoning comes back
in **its own field**, which is a third shape of the "never return thoughts" obligation and the
easiest to miss. Local prices are invented and say so in their display name.
**174 edge cases against the running API (2026-08-06)** — every case asserts **never a 500**, an
actionable status, and a message that *names* the problem. Four defects, all live-only: a custom
validator's error carried an unserialisable `ValueError` into the KIRA `details` (500 for a
malformed body); that surface had **no branch for a shared control's refusal**, so every one became
a 500; `maxOutputTokens: -1` was accepted and silently truncated the answer; and a request with no
content was served and billed, though `FRD-113` FR-7 had refused the same thing for embeddings all
along. Routing errors now use each surface's own envelope. One mutation (`X3`) was **removed rather
than kept**: the fix is doubly enforced, so no single-line edit reproduces it — a property guarded
twice cannot be a mutation, and that is not a reason to weaken the guard.
**`FRD-120` done (2026-08-06) — Microsoft Foundry, and the test `ADR-0011` was really making.**
`FoundryTransport` × the **unchanged** OpenAI dialect × `AzureRoutes`; the one missing piece was
the routing axis. **The diff does not leave `upstreams/`**, which is the claim — and the
architecture assertion caught the first draft, where `AzureRoutes` sat in the *dialect's* package
(a dialect that names a platform is one the next platform cannot reuse). It now refuses "azure"
above the platform packages, exempting `residency.py`, which names every cloud's regions on
purpose. The addressing is the part with money in it: an Azure **deployment** name has no price, so
attributing a response to it would not fail — the spend figure would quietly stop being complete.
One adapter **per region** (provenance is per model), and `headers()` is async so an Entra token is
minted rather than captured once. No Azure subscription here, so it is hermetic only, and that is
stated rather than implied.
**`FRD-116` done (2026-08-06) — Vault is finally read.** §2 has required it since Phase 0 while
every credential was an environment variable. `aira_common.secrets` does AppRole + KV-v2; a
pydantic `VaultSource` ranks it above the environment for both planes — **a settings source, not an
injection into `os.environ`**, because values placed there are readable from `/proc`, inherited by
every subprocess, and dumped by any library that panics. **Fail closed**: a configured Vault that
cannot be reached stops the process, since falling back turns a broken secret store into a service
that starts, looks healthy and runs on a stale value. "Vault is down" and "nobody wrote that key"
are different exceptions. Rotation is a restart, recorded as a decision. Verified against the
stack's Vault with a **real AppRole** the suite creates, scopes to its own path and removes.
**Two test lessons worth carrying:** `caplog` cannot see structlog, so "no secret reaches a log"
was green against a loader that printed everything — use `structlog.testing.capture_logs`; and an
assertion matching a string that appears in *both* the right and the wrong message proves nothing.
**`FRD-117` done except FR-7 (2026-08-06) — diagnostics.** The rule: **a health check must not be
able to take down a healthy service.** Reachability is probed in the background and `/readyz`
*reads* the verdict; probing inline makes readiness as slow as the slowest upstream. **The first
draft would have proved nothing** — it probed `provider.models()`, which is local configuration
evaluated once at registry build, so every verdict would have been a confident green describing
nothing. Adapters now implement `ping()` (a GET of a listing, never a generation, which would wake
a scaled-to-zero model); one without it reports `probed: false`. **Stale counts as degraded**, and
**unreachable is degraded not down** — verified by stopping the model container: 200 `ready` with
`degraded: true`, cleared on recovery. `x-trace-id` is pure ASGI mounted outermost (BaseHTTPMiddleware
loses the span context, and the failing responses are the ones worth correlating). CORS refuses
`*`+credentials at startup. FR-7 (a second OpenAPI 3.0 doc) is **not built** and said so.
**`FRD-602` done (2026-08-06) — the usage export.** CSV is a **renderer on the existing reporting
endpoint**, chosen by `Accept`, never its own endpoint: `visible_scope` is one function and a second
entry point is a second chance to forget it — which is how an export comes to return more than the
screen, as a *file* that gets forwarded and cannot be recalled. Asserted on the file's bytes. BOM,
CRLF, RFC 4180, quoted keys (a use case named `vertrieb, süd` would otherwise shift every figure one
column left), the unpriced caveat as a trailing row. The SPA downloads via a blob, because a plain
link carries no bearer token and a 401 reads as a broken export.
**A lesson this project has now learned twice**: `aira_common.secrets` imported `httpx` without
declaring it, and the management image died on `ModuleNotFoundError` — the same failure the `pyjwt`
comment beside it already documents. **A shared library's dependencies cannot be validated by any
environment that also installs its consumers**, and this repo's dev env, test runner and coverage
gate are all such an environment. `libs/tests/test_declared_dependencies.py` now parses every module
and checks; shown to fail with the declaration removed.
**`FRD-124` done (2026-08-06) — nothing a request asks for is silently dropped.** Twelve fields a
legitimate Google client can send were posted at the running gateway; **eleven came back 200 and
did nothing**: `stopSequences` (unbounded output), `seed` (a different answer every call — the exact
failure a seed exists to rule out), `tools` (prose where a function call was expected),
`safetySettings` (a governance control applied nowhere), `candidateCount: 3` (one answer, which
reads as the model having one thing to say). `ADR-0012`'s rule — **a chain must not degrade a
request silently** — had only ever been pointed at the *model*; a field the *surface* drops is the
same defect one step earlier. Three answers now: portable → carried; out of scope by `ADR-0013` →
**refused by name with the reason**; the dialect has no word for it → **the candidate is skipped**
(`SamplingExpressible`, the fifth requirement to share the `ADR-0012` §3 mechanism and the first
that is a property of the **dialect**, since no catalog entry can say whether `top_k` exists — no
dialect has all six controls). Refusal not best effort: `seed` on a Claude candidate answers
perfectly and simply is not reproducible, and **nothing in the response differs from a correct
one**. This **reverses `FRD-100` FR-7 on evidence** — Google's own API rejects unknown fields, so
leniency was never the compatible choice; strictness stays **one-directional**, responses keep
ignoring extras or every upstream release becomes an outage.
The defect that started it: `disabled` thinking mapped to an **absent** `reasoning_effort`, with a
comment claiming absence means off. A real reasoning model sent no such field **thinks anyway** and
spent the whole 600-token allowance on it — empty answer, 200, reasoning stripped before the caller
sees it. A green unit test asserted the wrong thing, because code and test came from the same idea.
**Off has to be said out loud.**
Two test lessons, the second one repeated: unit tests that exercise a requirement *directly* leave
the route's wiring undefended (a mutation proved it) — **the same shape as the export's scope test
the same day**, both invisible to coverage; and integration tests here assert **behaviour, not wire
bodies** (a seed makes three requests return one answer), because inspecting a dict is exactly how
the thinking defect survived a suite that appeared to test it.
**The same live round found the refusal that ran before the boundary (`FRD-122` §12).** `FRD-122`
closed "the log records what was served, not what was asked" at the route's **exception boundary**
— one site, deliberately. The **body-size ceiling is pure ASGI and answers before any route**, so a
20 MB body was refused 413 and left **no trace at all**; found by posting one and counting rows, not
by reading code that is consistent about the rule everywhere it can be read. Both exits (declared
`Content-Length`, and a body cut off mid-read) now record through **one** function, under a new
closed outcome `request_too_large` — folding it into `invalid_request` would hide "somebody keeps
posting 20 MB" inside "somebody sent malformed JSON". **The row carries no identity**: the
credential was never verified there, and recording it would let anyone write another system's name
into the audit trail with one oversized request — an unverifiable claim is not evidence, the same
rule as "unpriced is not free". The body is not stored either. **A 401 still leaves no row, and that
is a decision**: an unauthenticated request is a *security* event for `FRD-500`/`501`/`503`, not a
usage row attributed to nobody — written into `FRD-122` so the question is already asked.
**`FRD-125` (2026-08-06) — the filter that was configured, displayed, and doing nothing.** A use
case set the LLM injection filter to **block**; an injection was sent; the gateway answered **200**
and the model complied. Cause: the classifier asks for a one-word answer in a four-token allowance
and dispatches **straight to the provider**, bypassing the catalog thinking resolution — so it never
says "do not think", a reasoning model thinks by default, all four tokens go on reasoning, and
`"INJECTION" in ""` is False. The same bug had silently disabled `model_route`. A verdict is now
**three-valued**: `undetermined` covers an upstream failure, an empty reply, neither word, and
**both** ("SAFE, no injection attempt" was asked for one word and gave two — a precedence rule
nobody can predict is not an answer). It **blocks by default**, reversing "fails open": `FRD-405`
settled this for rate limits — *the moment a control stops working is the worst moment to stop
applying it* — and a filter that passes everything while the builder shows it active is an absent
control wearing a present one's badge. `on_undetermined: allow` restores the old behaviour as a
**choice, on the audit row**. Also: a filter that ran and **passed** now records so ("found nothing"
vs "none configured" used to look identical), and `P1`/`P2` were **re-anchored** — a mutation whose
anchor moved protects nothing. **Operational, not a defect**: against `qwen3:0.6b` the LLM filter
answers INJECTION to everything — it is exactly as good as the model behind it, while the heuristic
cannot be undetermined at all. **Test lesson**: two live assertions were testing the *model* (seed
reproducibility across this server's cold prompt cache; a 0.6B picking the right category) and were
replaced by the property that is ours — the classifier gets an answer at all, with the **old** call
shape asserted still returning nothing so the test cannot start passing for a new reason.
**`FRD-125b` closed it too**: one caller request with an LLM step makes **two** model calls and left
**one** audit row — the classifier's tokens invisible three ways (reporting showed a spend they were
not in; `FRD-403`'s "unpriced is counted apart, never as zero" broken by counting them as *nothing*;
a model call `ADR-0013` says must be auditable that nothing recorded). Each call now leaves a row
named `pipeline:<step>` (so reporting separates what a use case *asked* from what *governing it*
cost) and is booked with **`requests=0`** — the caller made one request, and a second would inflate
request figures and could trip a request limit for traffic nobody sent. The hook is **one `finally`
in `run_pipeline`**, with the collector **passed in like `decisions`**, so a blocked step still
reports what deciding cost and both surfaces get it. The measured number: **the classifier costs
roughly as much as the answer it guards** — an LLM-filtered use case was reporting about half its
real spend. **Recording is not enforcing**: the first version wrote Postgres only, so reporting was
right and the guard — which reads the **shared counter** (`FRD-405`) — never saw it until the
counter expired; both stores now, verified live (429 at 40 200 against a 40 000 cap). The test for
it **passed against the broken code** at first, because on a *cold* counter the guard seeds from
Postgres: a test that never reached the path it was named after, warmed now.
**A demo somebody can walk through (`FRD-130`, 2026-08-07)** — `make showcase` starts the stack
with **Ollama in the `demo` profile** (separate pull step, so the health check stays honest), seeds
**three use cases** chosen so the roles see *different* things (`ucadmin` administers two of three
on purpose), budgets across **every axis** (cost/tokens/requests × use-case/member × day/month),
rate limits, pipelines and one API key each — then drives **real traffic** (`tools/demo_traffic.py`)
including a refused injection, because inserted rows would be a story about the product rather than
the product. Four first-run defects, all instructive: the seed gated on `AIRA_OLLAMA_URL` while the
stack uses `AIRA_OPENAI_SERVERS` (empty catalog); **801** leftover test use cases made the list
useless, so `--fresh` now clears *every* use case; `--fresh` then **permanently revoked** the demo
keys, because deleting a use case revokes its keys and revocation is terminal — recreating the same
slug is a **reset, not a retirement**, and the events have to say so; and budgets sized plausibly
(€0.50/month) sat at 0.02% against a model priced in fractions of a cent, so they are calibrated
against what the demo traffic actually costs.
**Developer round against the running model (`FRD-129`, 2026-08-07)** — 47 live cases walking both
surfaces: ordinary journeys, dropped connections on **every** path, and every figure checked in the
**database** rather than in the response. Nothing asserts an answer's *content* (that tests the
model, and flakes); what is asserted is that a request is recorded, weighed, priced and bounded —
and that **both surfaces leave the same facts**, compared as audit rows rather than by reading two
code paths. Two findings: the catalog declared a thinking mode (`minimal`) the server refuses **by
name**, filled in from the enum instead of from a measurement — the mirror of `FRD-114`'s rule, now
corrected in the seed with the measured set; and the resulting error was worse than the fault — the
caller got `502 UNAVAILABLE`/"Upstream returned 400." while the provider had said exactly which
field it objected to. An upstream **400** now answers **`400 FAILED_PRECONDITION`** carrying that
reason (same argument as `NoCapableModel`: operator-fixable, not an outage), while **401/403 stay
masked** — those are *our* credentials, the caller cannot act on them, and the message may name the
credential.
**Delivery order is fixed (ROADMAP Phase 8, 2026-08-06)**, derived from the owner's priority
(KIRA compatibility → model connections → documents → the review findings) and the dependency that
priority 1 needs priority 3: **`FRD-122` (audit) → `FRD-114` (metadata) → `FRD-115`+`119` (Vertex EU,
Gemini+Claude) → `FRD-110` (documents) → `FRD-107` Stage A (KIRA text contract, unsupported fields
**refused, never ignored**) → `FRD-111`/`112`/`113` → `FRD-107` Stage B → `FRD-120` (Foundry) →
Vault/diagnostics/export/redaction → IT Security.** Two deliberate deviations, both documented:
audit goes *first* (cheapest thing in the programme, and every later stage is tested against it),
and documents come *after* the EU connection (a document capability exercisable only against the
mock is not the capability that was asked for).
**The owner's canonical feature list is PRD §1.1** (plus §1.2 review findings, §1.3 priority) — read it before planning
anything. What it makes visible: **the governance features are largely built, the evidence features
are not.** Budgets, limits, routing, self-service and roles work; auditability (`FRD-122`), incident
response (`FRD-503`), anomaly detection (`FRD-500`/`501`) and model smoke tests (`FRD-504`) are the
gaps — and they are what make a governed system defensible *after* the fact. The list also settled
`ADR-0010`: **KIRA compatibility is a central feature, so `FRD-107` is unblocked (Option C — with a
sunset date and its usage in reporting).** **One decision open** (`ADR-0010`): does AIRA serve the predecessor's wire contract, so
clients migrate by changing a URL, or do the clients move to the Gemini surface? Recommendation:
compatibility surface **with a sunset date and its usage in reporting** — everything except
`FRD-107` is contract-independent and can start now. Three deviations from the predecessor are
deliberate and written down: TLS verification stays on, CORS is an allow-list not `*`, and
`GET /models` requires auth. The OpenAI surface (`FRD-106`) is deferred so parity is not competing
with a second new contract.
**The console stops promising what the server refuses (`FRD-206`, 2026-08-07)** — a role-by-role
walkthrough of the running UI produced fourteen findings; three shared one shape: **the console was
answering questions only the server can answer, and answering them generously.** A use-case *user*
was shown "Add member"/"Remove" and got a 403 from the screen that had just invited the click; IT
Security saw an **empty** console; anyone who could open the pipeline builder could rearrange a
graph they could never save. Object-level permission lives in guardian rows, so it is **not in the
token** and `/me` cannot carry it — the console filled the gap with an assumption. Now the object
says what this caller may do (`can_admin`/`can_manage`/`is_member`), computed by
`apps/usecases/access.py`, **the same predicates the viewset enforces with** (a restatement in
TypeScript would be the same defect with an extra copy to forget). The test that matters is an
**agreement test** — for each answer, attempt the request and require the status to match what the
object reported; `Z23`/`Z24` hardcode a reported permission to true and are caught by it, and
`G1`/`G3` were **re-anchored** because this change moved the code they pointed at. Three rules
generalise: **an action nobody can carry out is worse than an absent one** (absent reads as a
boundary, present-and-failing reads as a broken system — and the reader then distrusts the figures
on the same page), so every withheld action names who performs it and read-only stays *usable*;
**read-only means inert, not un-saveable** (the graph sits in a native `<fieldset disabled>` —
hiding Save alone lets somebody rearrange a pipeline for nothing); and **`is_member`, `can_manage`
and visibility are three different answers** — an oversight role sees every use case and must not be
offered a key, a member administers none and must be. IT Security's empty console was the same
mistake: `scope_queryset` used one role set for both "sees every use case" and "sees every figure",
now `OVERSIGHT_ROLES` ⊃ `GOVERNANCE_ROLES`. Also fixed: the session renews itself, and when it cannot, **a `401` on
`/api`/`/gw` sends the reader to the login rather than reporting "invalid credentials" on every
panel at once** — that reads as the backend rejecting them, and the next thing doubted is the
figures on the same page; `403` is deliberately untouched (a real answer about a real permission),
one login starts however many requests fail together, and the path is restored only if `state`
holds a same-origin path (it survives a round trip through the browser, so treating it as a
destination would be an open redirect); creating a use case
is a window that ends on its **settings**, not the list; "slug" became **technical id**, derived
from the name; the model editor is a window naming its model; reporting cards got short headings
plus **info buttons** carrying what each figure counts. Two defects were shipped inside this pass
and reported back, both instructive: `offline_access` was added to get a refresh token and **broke
login outright** — the realm forbids offline tokens, the code-to-token exchange answered
`not_allowed`, and Keycloak answers *that* without CORS headers, so the browser reported a CORS
error naming neither the scope nor the setting (the code flow already returns a refresh token; the
scope asks for one that outlives the session, which a governance console must not hold); and the
info buttons were a `title` attribute, so they **showed nothing at all** (the first repair opened
them on click, which worked and was still wrong — an "i" is a thing you point at, so it is hover +
focus + a click that pins, for a touch screen) — the very
defect the pass was about. Both share one cause: **three test layers ran and the fourth did not**,
and both changes live only in the fourth. No unit test performs an OIDC redirect, and none can tell
"renders a tooltip attribute" from "shows the reader anything". A change to the login flow, or to
whether a control does something when used, **is an e2e change**. That run also updated 16 e2e
tests whose screens this pass deliberately moved — five rewritten rather than repaired, because
their *meaning* changed.
**Access follows the group (`FRD-209`, 2026-08-08)** — membership was granted one person at a time
*and* had **two answers that disagreed**: the gateway read Keycloak groups `/use-cases/<slug>`,
Management read its own rows, and a use case created in the console produced only the second (the
defect `FRD-208`'s round surfaced). A grant now binds a **principal** — a group or a person — to a
use case with a role. The mechanism is the keeper: **guardian assigns object permissions to a user
*or a Django group***, so a group grant assigns them to a Django group mirroring the Keycloak path
and every request syncs the caller's paths onto their Django groups (as `FRD-201` already does for
roles) — `scope_queryset`, `may_admin`, `may_manage` needed **no change**. Two rules live in
`aira_common.access` so neither plane restates them: the routes are a **union**, and where roles
differ **the stronger wins**. Degradation refuses. The console has **one** picker for groups and
people (the question is "who should get this"), and without an admin client it falls back to what
Management knows **and says so**. **AIRA never writes to the directory.** The live round found three
defects of one family — *a correct half with nothing carrying it across*: (1) `use_case_group.granted`
had **no topic**, and `record_to_outbox` returns **silently** for unknown types — the **third**
instance after `aira.rate-limits` and `aira.anomaly-rules`, so a test now parses every `emit(...)`
and compares it against the map **in both directions**, which immediately found `pipeline.deleted`
as a **topic with no emitter**; (2) a **compacted** topic keyed by slug alone let a second grant
**erase the first** from the log; (3) **a token with no `groups` claim grants nothing** — the mapper
was on the SPA client and none of the service accounts (now in `INTEGRATIONS.md`). A fourth came from
tightening a weak assertion: a grant on the bare realm root `/` was accepted and can never match.
**Paging that is real, and a rule somebody can change (`FRD-208`, 2026-08-08)** — `FRD-207`'s
search and paging were **client-side**, and the useful question is where that matters: one list of
three. The **use-case list** is unbounded (801 seen) *and* its serializer answers three permissions
**per row**, so slicing in the browser left every computation happening — the reader waited just as
long for 25 rows. Server-paged now, explicitly ordered (an unordered queryset repeats and skips
rows); **1.6 s for 211 use cases across 9 pages**, measured. **Findings** page by **cursor** (an
append-only log — an offset page shows one row twice and misses another while somebody reads); the
**catalog stays client-side on purpose**, written into the viewset, because two console warnings
count over the *whole* catalog and paging would turn them into "N on this page". Three properties a
server-paged list needs: a debounce (nine letters ≠ nine round trips), a reset to page one on a new
search, and a switch rather than a queue so a slow answer cannot overwrite a newer one. **The bigger
finding: the console pointed at a screen that did not exist** — it said a use-case rule "is changed
on that use case" and there was no such panel. `FRD-206`'s defect one indirection out: not a button
that 403s, an instruction with no destination. There is a **Rules tab** on the use-case detail now
(the server always allowed it), and one shared `rule-form.ts` for both screens — refusing to edit a
rule's **kind** (it decides what the threshold *means*) or **name** (the server upserts by name, so
a rename creates a second rule). Test note: the search is asserted by **watching for the request
carrying `q=`**, because checking which rows are on screen passes for a client-side filter too.
**The console holds still and says what its controls do (`FRD-207`, 2026-08-08)** — `FRD-206`
stopped the console promising what the server refuses; a walkthrough asked the next question, *can
I read this?*, and produced twelve findings. Two were defects of the same shape — **a declaration
that is silently inert**. The "jiggle" was **measured**, not guessed: a `layout-shift` observer
reported five shifts in forty seconds, every one the Refresh button, because the live stamp changes
width twice a tick ("updating…" vs "updated 12s ago"; "9s" vs "10s"); the stamp now reserves its
widest form and busy is a dot in space it already occupies. And `routerLinkActive` was **never
imported**, so the nav marker had been styling nothing since the shell existed — Angular does not
complain about an attribute matching no directive. Also: `.table__actions` was `display:flex` **on
the `<td>`**, which stops a cell participating in its row (the reported "break" between a model row
and its buttons), and a filter row centred a bare checkbox against a labelled field, putting a pair
of controls on two lines. **A finding opens** (six columns is as much as a table can be read at) and
**a rule says what it does in English** — safe only because the vocabulary is closed;
`rule-language.ts` keeps *a ratio is not a threshold* and *`alert` is not enforcement* intact.
**Rules are editable** except their kind and name: a kind decides what a threshold *means*, so
changing it in place silently reinterprets a chosen number. Reporting shows **one breakdown at a
time** and exports what is on screen (four stacked tables scrolled the export control out of sight
and left two ideas of "which table"); `by_outcome` is shown, not exported, and says so. Search +
paging (`core/ui/table-view`) after a live round found **801** use cases; searching returns to page
one, and the pager renders even on a single page. The hover explanation was **written twice in a
week** and collided on a `data-testid` — now `core/ui/info-hint`, one pinned at a time. Test lesson:
the first rule-editor e2e **skipped itself** when no rules existed, so the richest new behaviour
would have been browser-tested never — *a test that skips when the data is inconvenient reports
green about nothing.*
**Phase 5 begun (`ADR-0014` + `FRD-500`, 2026-08-07)** — the *evidence* half. `ADR-0014`:
**detection is asynchronous, enforcement is not, and they meet at a written decision.** Evaluation
is fed by the **request log** — the same rows the report reads, so a detector cannot see anything
the report cannot, and it sees **refusals**, which is where much of the signal is (a thousand
rate-limited requests *is* the anomaly). An action is a decision with an **author**, an **expiry**
and a **record**; without those an automatic block is an outage with a good reason. Stage A is the
rule: seven kinds in a **closed** vocabulary (`aira_common.anomalies`) — a field/operator/value rule
engine fails on the first review, since `p95_latency > 900` reads fine and is unimplementable
against a store with no percentile function. **`alert` is the default and that is a safety
property** (a system whose first setting is `block` blocks wrongly once and is switched off
forever — deliberately the opposite of `FRD-125`'s classifier, because *that* control had already
been chosen and displayed as active). **A ratio is not a threshold**: `spend_spike` compares against
the preceding window, because a fixed number is a budget and there is one. A **global** rule is IT
Security's to author (its effects land on use cases its author cannot see) and **everybody's to
read**. `use_case` is **NULL**, never "", and an event carrying no scope key is **skipped rather
than made global**.
**Stage B (`FRD-501`, 2026-08-07)** — the engine. All seven kinds evaluate against the request log.
Scheduling: the writer **marks which scopes saw traffic** and a timer evaluates only those — per-row
evaluation is N queries per request, and a timer over all rules makes a quiet installation with 200
use cases run 200 pointless queries a minute forever. The **cooldown is the window**. Three
measurement rules: a rate over too few rows is not evaluated; **growth from nothing is not a spike**
(an empty previous window as infinite growth makes every use case's first hour an incident); and a
request of **unknown** size is excluded from *both* sides of the share. `refusal_rate` reads
`Outcome` directly rather than keeping a second list of "bad" outcomes, and counts `client_gone` —
one caller hanging up is not our failure, a thousand is the shape a detector exists for. Until
`FRD-503`, a `block` rule **records that it did not enforce**, in those words. Two findings: a
kind that needed **two** numbers was declared with one (`payload_size`'s byte figure had nowhere to
live) — invisible to stage A's 18 tests and six mutations, because **a configuration schema is only
proved by the code that consumes it**; and `FRD-602`'s scope assertion caught the new endpoint while
meaning something narrower, so it now says *each endpoint resolves the scope exactly once*, which
also catches one that resolves it zero times.
**Stage C (`FRD-503`, 2026-08-07)** — a finding becomes a control. A **suspension** is the written
decision `ADR-0014` promised: target, action, expiry, **author**, **reason**; read at the one
pre-dispatch gate, so a stopped caller does not pay for a classifier before being told; kept after
being lifted, because "blocked for two hours last Tuesday" is what a review asks. **Amends
`ADR-0014` §2**: a suspension is read every request and written when something goes wrong, which is
a *cache* problem, not shared state — so a 5-second cache over **Postgres**, which also survives a
Redis outage (the right direction for a control that stops traffic); the cost is that a *lift* takes
up to the TTL, and being late to remove a restriction is harmless. **429 not 403** (the credential is
valid; "come back later" is what 429 means), **`suspended` is its own audit outcome** (not folded
into `rate_limited`), and the **kill switch does not go through Kafka** — an incident control that
depends on the event bus fails exactly when the bus is the problem. A pattern named after happening
twice: **an enum member is not a specification** (`throttle` had no rate, as `payload_size` had no
byte figure). Two catches by the existing suite: the reporting module's scope assertion rejected the
new endpoints for resolving the scope **zero** times — correctly, they are bounded by *role*, so
they moved to `api/incidents.py`; and `N19` survived because every endpoint test ran with auth off,
which returns on the demo path **before** the role check — five tests passed around an untested
check.
**84 live cases against Phase 5 (2026-08-07)** — real Postgres, real gateway process, real model,
both planes over Kafka. **Five defects that 251 mutation properties and a green gate could not
see**, three of them older than this week. (1) **Two planes, one question, two answers**: the
gateway's kill switch was guarded by `has_oversight` — a *visibility* predicate — so `it-steuerung`
could stop traffic there while Management refused it a global rule; `INCIDENT_ROLES` is now one
definition both read. (2) `payload_size` **measured a column nothing wrote** — the middleware
counted the bytes, the column existed, no wire between them; the third recorded instance of *two
correct halves and no wire*. (3) A **refused request was counted as unpriced** (105 reported, 5
real), making the "lower bound" caveat permanent — *unknown is not zero, and zero is not unknown*;
a NULL outcome still counts, since that is a pre-`FRD-122` row. (4) **`aira.anomaly-rules` was
created by nothing** — the second time after `aira.rate-limits`, silent by construction, so a test
now compares the three hand-written topic lists against the constants **in both directions**. (5)
**38 mutation ids named more than one property**, making "N3 survived" ambiguous; later duplicates
renamed, harness now refuses them. Two test lessons, both **measuring from the wrong moment**: a
suspension takes up to the cache TTL to arrive, so "consumes no budget" and "pays for no classifier"
must count from *after* the block took effect.
**Stage D (`FRD-502`, 2026-08-08) — the screens.** Phase 5 had rules, an engine and enforcement and
no console, which is `FRD-206`'s complaint exactly. Three views: an **IT Security console**
(findings with the numbers they were drawn from, what is stopped now, and the rules — all three
together, because a finding without its rule is a number without a claim and an empty findings list
means nothing until the page says whether anything is watched); **Warnings per use case**, because a
warning only IT Security can see is one nobody who could fix the cause ever reads, leading with "this
use case is stopped" since otherwise a wall of 429s reads as a broken gateway; and **Traces** —
`GET /v1beta/traces` plus a tab, every request with who/which model/how it ended/what it cost.
**Metadata only, never a payload** — not the browsing `ADR-0009` deferred (that is *stored prompts*
to *non-members*; this is neither), and the field list is an **allow-list** so a column added
tomorrow cannot leak by a forgotten exclusion. **Cursor paging, not offset**: rows arrive while
somebody reads, and an offset page over an appending table repeats some and skips others
*invisibly*; the cursor is `(created_at, id)` because two rows share a millisecond, written out
rather than as a row comparison since SQLite has no tuple comparison and the hermetic tests run
there. Live is **polling** (`core/ui/live.ts`) with three guarantees, each a way live views fail: it
**stops** (on destroy, and while the tab is hidden), it is **visible** ("updated 12s ago", and the
reader can switch it off), and it **never stacks** (a tick during a slow response is skipped, or the
refresh becomes a load test against the endpoint already struggling). The console keeps
**visibility and authority apart** — `it-steuerung` sees everything and is offered no kill switch,
and the page names who performs it. One test lesson: `Live`'s teardown case failed with seven ticks
after destroy because the harness provided the service in the *testing module* while every screen
provides it on the *component* — `DestroyRef` resolves to whichever injector created it, and an
environment one outlives every component. **A harness that configures a service differently from
production tests a different service.** `N24`–`N27`.
**The security round (`ADR-0015`, `FRD-406`, 2026-08-08) — every finding fixed, nothing taken
away.** The instruction was to keep the framework's functionality, and that was the harder half:
the demo, the published demo key, `?key=`, the CLI break-glass key, a laptop's zero-configuration
start and a useful `/readyz` all had to survive their own fixes. **The one that mattered was found
by sending a request, not by reading**: the KIRA surface asked `if memberships and header not in
memberships`, so an **empty** membership list meant "anything goes" rather than "nothing" — a
caller belonging to no use case could name somebody else's, get a real answer, and have the tokens
billed to that budget and written into that audit trail. The Gemini surface refused the identical
request. Cause: **a rule restated by hand on a second surface**, the same shape as `FRD-126`,
`FRD-206` and `FRD-602`. It is now `use_case_refusal`, returning a *reason* rather than raising, so
the surfaces differ only in their envelope — and the deliberate exception survives inside it (an
**unbound** API key is break-glass and stays unrestricted).
**A convenience default is a production default, one variable away.** `ADR-0007` made Management
refuse to boot outside `local`; the gateway read `environment` for telemetry and acted on it
nowhere. Now: open routes, the published Postgres password and OIDC-with-no-audience each stop the
process, all reasons at once — **environment-shaped rather than stricter defaults**, with
`AIRA_DEMO_MODE` exempting outright, because a hardening pass that breaks the demo gets reverted.
Six more of the same character: a credential was kept out of exported spans and written **verbatim
to the access log** (the more widely readable of the two); **an absent claim is not a claim that
passed** (PyJWT accepts a token with no `exp` — `exp`/`iat`/`sub` now required, audience required
by *deployment* so a laptop still works); **the verdict is public, the diagnosis is not** (`/readyz`
stays unauthenticated for probes, but the body naming hosts, upstreams and fallbacks needs a
credential); **a control keyed by identity cannot bound a caller who has none** (authentication
*failures* bounded per address, **counting refusals only**, so a working credential never touches
the bucket); API keys may state an end date (**NULL means never** — an expiry that cannot be
omitted is one set to the year 3000); and `create_all` no longer runs beside Alembic, closing the
hazard `FRD-114` recorded. **`FRD-406` finally does something**: stored payloads are scrubbed of
credential shapes only — names, customer numbers and prose are *the work*, and a redactor that
mangles them produces payloads nobody uses and a deployment that turns storage off, which is worse.
An unusable pattern **stops the gateway** rather than matching nothing (`FRD-125`'s badge-wearing
absent control), and deployment patterns are **additive**, or the first organisation to name its own
token format stops redacting Google keys. Two test notes: the redaction requirement is proved twice
on purpose (class *and* route, the `FRD-124` lesson), and the bound's tests run with `redis_url=""`
because a Redis left running on the machine still held the bucket from a previous run — a property
of the process has to be tested as one. `H1`–`H17`; `A4` **re-anchored**.
**Agents and coding assistants (`FRD-131`–`FRD-133`, 2026-08-08)** — the third named use case, and
a check against the code rather than the docs: **semantic search and RAG chat already work**
(embeddings with batching and task types; documents, structured output, streaming — retrieval and
vector storage are the caller's by `ADR-0013`), and **coding assistants do not work at all**,
blocked on one field. `tools` is refused with a 400, and an assistant's whole loop *is* tool calling.
**The refusal is right and its stated reason is wrong**: the code cites `ADR-0013`, which says in
the same words it has always had that the gateway *may pass a tool definition through* and never
executes. The real reason was written nowhere — `CanonicalRequest` has no field one could travel in,
which is a **capability gap, not a boundary**, and the two get different treatment. `ADR-0013` now
says so, and needed the same clarification for caching: a cache *handle* (`cachedContent`) is
provider-side state and stays refused; a cache *marker* on content sent in full every time is a
price, not state. `FRD-131` carries tool calls **per use case, default off** (least privilege: a use
case that summarises documents has no business declaring functions), with the capability checked
**per hop** so a fallback skips an incapable candidate rather than returning a 200 the client parses
as a function call. Two traps recorded before they are hit: Anthropic already implements structured
output *as* a forced tool call (`FRD-119` §5.5), so tools + schema is refused by name; and OpenAI
streams a tool call's arguments **fragmented across chunks**. `FRD-132` **measures before choosing a
surface** — OpenCode against the running gateway, because a contract chosen by reading is one
maintained forever, and reviving `FRD-106` is now cheap since `FRD-123` built the OpenAI dialect as
an upstream. `FRD-133` (caching) is **written now and built last by owner decision**, so the
assistant work stands at full price and the saving comes out of `request_logs`. Two governance
consequences: an assistant makes **many model calls per human instruction** (the `FRD-125b` shape at
scale, except the calls are genuinely the caller's), so limits calibrated for a chatbot trip at once;
and **a tool result is content the model reads** and the injection filter cannot see — `FRD-110`'s
blind spot one step sharper.
**Stage A is run (`FRD-132`, 2026-08-08) — and the answer is B1: no new surface.** OpenCode 1.18.15
against the **existing Gemini surface** (`@ai-sdk/google` with an overridden `baseURL`,
`tools/opencode/opencode.json`): provider, auth, model selection, generation and SSE streaming all
worked unmodified, and it failed at exactly one thing — `tools`, refused by name. Reaching that
refusal *is* the successful outcome. `FRD-106` stays withdrawn, and this run is the evidence that
was missing when it was. **One trivial instruction produced three gateway requests** (served,
refused, `client_gone`), all audited — the assistant-shape warning is now a number.
**The finding that had nothing to do with surfaces**: `reasoning_effort: "none"` does not mean "do
not think", it means "do not emit a separate reasoning channel", and those coincide only on some
models. Same Ollama, same minute: `qwen3:0.6b` answers `"OK."` in **3 tokens**; `qwen3:4b` returns
**480 characters of raw chain-of-thought as the answer**, billed, with a 200 — and the seed
declared `disabled` as the *default* for whatever model was configured, so that was the ordinary
path. Fixed as **data**: both seeds now key thinking by model from a measurement, and an unmeasured
model is declared with **no thinking at all** (`FRD-114` FR-7). `minimal` also survived in the
`tools/` seed after the identical correction had been made in the *Management* seed on 2026-08-06 —
one definition, two files, one fixed. The rule: **a capability belongs to a model, not to a family,
a vendor or a runtime**, and a seed that writes one declaration for "whatever is configured" is the
mechanism that turns a measurement into an assumption.
**Tool calling (`FRD-131`, 2026-08-08) — carried, never executed, off by default.** Four stages,
each run against the whole suite before the next: tool call and tool result join `FRD-110`'s
ordered-parts union; the Gemini surface carries `functionCall`/`functionResponse`; the OpenAI
dialect handles the trap the FRD named in advance (**a streamed call arrives fragmented** — name
once, arguments as string pieces across deltas, reassembled by index and emitted whole); and
`tools_enabled` is **per use case, default off**, with a catalog capability checked **per hop** so
a fallback skips an incapable candidate rather than answering in prose. One existing rule had to
change and it is the telling one: `is_empty` refused a request with no text and no attachment, and
**the second turn of every agent exchange is exactly that**. **mypy caught what no test could** —
three adapters treated "not a text part" as "an attachment", an `AttributeError` waiting on two
upstreams; `DialectUnsupported` moved to `upstreams/base.py` rather than being imported from a
sibling dialect, the import the architecture assertion caught once before.
**`FRD-132` stage A: B1 — no new surface.** OpenCode 1.18.15 against the **existing Gemini
surface** worked unmodified except for `tools`; `FRD-106` stays withdrawn, and this run is the
evidence that was missing when it was. **A measurement then corrected a rule I had asserted**:
`toolConfig` was refused outright because its modes "hold on one vendor and silently do not on
another" — and a real client sends `AUTO` on every request, which *is* the default. It is carried
now; `ANY`/`NONE` are refused **because they are not built**, an honest reason.
**Then the live run found what stages 1–4 had missed, and it was the important one.** A real
assistant turn's audit row read `{"text": ""}`: the streaming path builds its stored response from
`text_delta`, and a tool call **has none** — the answer *is* the call. Every tool call in real
traffic (every assistant streams) was unrecorded, which is precisely the question `ADR-0013`
promises can be answered. Closed as **one fact on the trail**, not two at the exits: `AuditTrail`
carries `tools_declared`/`tool_calls`, `Accounting.served()` records them so both exits get it by
calling the same method, and `tool_summary()` is an allow-list — **names and counts only**, because
arguments are caller content and belong under `store_payloads` behind retention and redaction
(migration `0021`). `declared` sits beside `called` because *offered ten and asked for none* and
*offered none* are different events. The client was not receiving streamed calls either — the chunk
mapper carried only text — so recording and delivering were fixed together.
**Model selection is measured, and it produced a rule.** `qwen2.5:3b` calls tools in **2s/21
tokens**; `qwen3:4b` in 86s/352, nearly all discarded reasoning; **`qwen2.5-coder:7b` cannot call
tools at all** and returns the JSON as prose, while `ollama show` lists `tools` as a capability;
`qwen2.5:0.5b` called correctly once, then answered in prose, then invented arguments and a
parameter name outside the schema. Hence: **a vendor's capability flag is a claim, not evidence,
and one successful call is not a capability.** `TOOLS_BY_MODEL` holds what was *seen* and is
appended to only after a run — an entry for `qwen2.5:7b` was written while it was still downloading
and removed before the file was saved.
**Tool calls become evidence, and the console can ask for them (2026-08-08)** — the audit row
carries `tool_calls` (**names and a count, never the arguments** — a file path is content, and this
list is readable by every oversight role), and `GET /v1beta/traces` learned the four questions an
incident opens with: which system (`credential`), whose identity (`subject`), **which machine**
(`source_ip`), and which turns involved a function call — plus `mine`, offered even to roles that
see everything. Two rules: **`source_ip` is served only to a role that may act on an incident**, and
asking for it without one is **refused with a 403, never ignored** (a filter that silently does
nothing lets a reader conclude an address made no requests); and **a filter narrows, never widens**
— all of them apply after `visible_scope`. The console offers the field on the same predicate,
which now has **one** definition in the SPA (`core/auth/roles.ts`) instead of a role list retyped in
each screen. An **OpenCode configuration** is generated at key issuance — the only moment the
plaintext exists — naming only models whose catalog **declares** `tools`.
Three defects, each found by a different layer: `visible_scope` asked `is_governance` where the role
that needed it was **IT Security**, so the console built for investigating an incident came back
**empty** (every existing test of "oversight sees everything" used a global admin, which satisfies
both predicates); `loadMore` rebuilt its query by hand and paged **without** the filters page one
was fetched under; and adding two controls to a four-control filter row reintroduced `FRD-207`'s
wrap — caught only for `it-steuerung`, while the widest case is IT Security's six controls. A live
round then found the framework answering query-parameter errors in its **own** `422`/`detail` shape,
which a Google client reads as "unknown error": now **400 `INVALID_ARGUMENT` naming the parameter**,
with KIRA keeping its own envelope. `N40`–`N44`; **`N2` was stale, not undefended** — its anchor
named the predicate this change renamed, so the harness replaced nothing and reported a property no
test would notice losing. `tools/mutation_check.py` gained `--only=`.
The live round that followed found two more: **an id that identified two models** — the seed writes
a *fixed* KIRA number for "the local chat model" and had been run for a second one, so
`MultipleResultsFound` reached the caller as a **500**; the resolver now refuses an ambiguous id
(`503`, `FRD-114` FR-6a — `ADR-0011`'s ambiguous routing table one level down, since picking a row
would answer, bill and audit under a model the caller never named) and the seed **releases** the id
before taking it. And FastAPI was answering query-parameter errors in its **own** `422`/`detail`
shape, which a Google client reads as "unknown error": now **400 `INVALID_ARGUMENT` naming the
parameter**, each surface in its own envelope — the routing-handler finding of 2026-08-06, one layer
in.

**The requests view, read by somebody who did not build it (`FRD-505`, `ADR-0016`, 2026-08-09)** —
a walkthrough produced eight findings that were one complaint: **the view assumed the reader already
knew the answer.** The sharpest: `source_ip` was added as a **filter** and not as a **column**, so an
investigator could search for an address the screen never showed them. Now: a cross-use-case
**Requests** screen for the incident roles (the tab stays for the people who work inside a use case —
**one component, two homes**), the use case and the machine as columns, and *"show me the prompts
that threw a warning"* as a filter backed by a `flagged` column derived in `record_request` rather
than queried out of the JSON decisions (containment is written differently on SQLite and Postgres).
**`ADR-0016` reopens `ADR-0009`'s deferral, and not the way that paragraph expected**: `FRD-406`
shipped its credential half and declined its PII half on purpose, so the redactor could never make
this safe — the sensitive content and the useful content are the same content. Stored prompts are
readable by **Global Admin and IT Security** (and inside a use case by its own people, with a
`restrict_members_to_own_requests` switch its administrator owns), and **every read writes a record**
naming who, what, when and on what authority, written *before* the content is returned. **IT
Steuerung reads none of it** — every figure, no content. Three defects invisible to every hermetic
test: a **200 rendered in red** (`outcome` is NULL on pre-`FRD-122` rows and the badge fell through
to its danger branch), the control that opens a request **off screen** behind the table's horizontal
scroll ("I did not even know it was there" is the accurate description of an action that does not
exist), and three info hints that **said nothing** — `InfoHint` takes projected content and `text=`
is not an input, which Angular ignores silently. **The guard against that was itself inert**: written
first as an Angular spec using `import.meta.glob`, it failed to *load* and Vitest reported "0 tests"
while the total stayed green. Found by breaking a template on purpose; it lives in the Python suite
now and was shown to fire. **A guard that cannot fail is the thing it guards against, one level up.**
Also: a 42-character migration id applied its DDL and then failed writing `alembic_version`
(`varchar(32)`), and the phone-layout test caught a ten-pixel overflow the day a checkbox gained a
sentence-length label.
**A second round the same day** cut the table from **eleven columns to four** (when · from · what ·
how it ended; everything else is a detail *about* a request and moved into the opened row), put
dates in `dd.MM.yyyy` and marked a pipeline objection **red on the row**. And it found the one that
generalises: **typing two characters threw the reader out of the search field** — the input sat
inside the `@else` of `@if (loading())`, so the query it started tore down the block containing it.
**A control that starts a request must survive that request.** The guard for the *shape* missed its
own case at first, because `@else` carries no condition and reads as innocent; teaching it to
inherit the `@if` it belongs to immediately found a second instance. **Second time in two days that
a new guard had to be broken before it could be believed, and both times it was silently wrong in
the same direction: passing.**

**The catalog, and the question nothing could answer (`FRD-506`, 2026-08-09)** — *"wie kann ich
neue Modelle definieren, wenn ich keinen Key habe, oder testen ob es überhaupt ansprechbar wäre?"*
A catalog entry is a **declaration**: it needs no credential and proves nothing. Without a key no
adapter is registered, so a model sits in the catalog looking healthy while every request for it
returns `model_not_found` — which a caller reads as a typo. `GET /v1beta/models/{model}:check` now
answers **three separate facts** — declared · served · reachable — and `reachable: null` ("not
contacted") is never reported as healthy (`FRD-117`'s rule). **Never a generation**: a self-deployed
model can be scaled to zero, and a "does this work" button must not be what wakes it. The upstream's
error *text* is not repeated back — a provider's message can carry the URL, and the URL the key.
Also: **model declarations moved to the top** (adding one required scrolling past the whole
catalog), a row **opens to every field on file** (built as a list so it is exhaustive by
construction, with a test that populates all of them), and the rule editor's actions left the
wrapping field row where "Create rule" had been reading as one more setting.
**An audit of the test matrix, measured rather than asserted**: each branch of `payloads.py` broken
in turn, recording which parametrised rows noticed. **`is_oversight` was undefended** — removing it
drops an oversight role through to `OUT_OF_SCOPE`, also a 403, so a matrix checking only the status
passed with the role boundary gone; it asserts the *sentence* now. And **half an audit reports half
a matrix as pointless**: deleting a branch can only make code more permissive, so the four rows
guarding against *over*-restriction needed the inverse mutations to show their worth. `N46`–`N54`.

**The button nobody could reach (2026-08-09)** — `FRD-500` says a global anomaly rule is IT
Security's to author and the server has accepted one since it was written; the **console never
offered it**, so every global rule anywhere had been seeded straight into the database. `FRD-206`'s
defect **inverted**: not a control that refuses when used, but a capability with no way in — and only
the first kind announces itself. Also: rules, live suspensions and earlier decisions are **paged and
searchable** (one box covers both suspension lists, because "has this caller ever been stopped?" is
answered by the live list *together with* the record); the rule editor is a **grid** rather than a
wrapping row that packed five controls onto a line; reachability moved **into the editor window** and
deliberately **does not block saving** (declaring a model before its credential exists is the
ordinary order of work — `FRD-114`'s rule: deprecation warns, revocation blocks); and the showcase
seeds four rules across the vocabulary, never `block`, because a demo that stopped somebody's traffic
on a first run teaches `FRD-500`'s lesson backwards. **The seed lied by one**: a rule named a use
case it does not create and the loop's `continue` dropped it silently — three of four appeared, the
count looked plausible, and the missing one was the only rule that *acts*. Third instance of
"returns silently for something unknown", after `record_to_outbox` and the missing Kafka topics.

**Only catalogued models (`FRD-307`, 2026-08-09)** — owner decision: *only models in the catalog,
explicitly created by a Global Administrator, may be used.* `approved` defaults **false** in
Management (the default is the decision) and **true** in the gateway read-model (fed by events; an
older Management sends no such field, and reading its absence as "not approved" would retire the
whole catalog on a partial upgrade). Enforced as a dispatch condition, so it holds at **every hop**
of a fallback chain. **This narrows `FRD-114` FR-7**: the baseline for a model nobody catalogued is
now *nothing*, because the first version could be defeated by **deleting** a declaration — approval
was removable by removing the thing that carried it. Two refusals kept apart, because they need
different actions: *"not in the model catalog"* (add it) and *"has not been approved"* (release it).
**58 hermetic tests failed when the rule went on and not one was a defect** — every one used an
invented model, which is what a test does; the fix was to notice what those objects *are* (test
doubles, now marked as such) rather than to catalogue fiction in fifty files. That exemption needed
a boundary, and looking for one surfaced something worse: **the mock provider was registered in
every environment**, so a fake model could serve production traffic billed as free. It is now
registered only in `local`/demo. And **Vault was built and never wired**: `FRD-116` shipped on
2026-08-06 and no container was given `VAULT_ADDR` for three days, so every credential came from the
environment while the feature read as done — an unconfigured secret store is indistinguishable from
an absent one. Both containers get the variables now (secret-id in a **file**, never the
environment), `make vault-init` creates path, policy and AppRole, and **`/readyz` says where the
secrets came from**, which is the only reason this could go unnoticed at all.

**Everything was calculated; nothing read it (`FRD-603`, 2026-08-09)** — the smoke-test use case
showed neither tokens nor money on its own page, and the owner asked whether anything is calculated
when no budget is set. Measured before touching it: **59 requests, 10,664 tokens, 3,674,900 nanos**
in `request_logs`, priced, and **no row at all** in `budget_usage` — because it has no budget, and
consumption was only ever *displayed* as a fraction of a limit. `BudgetService.usage()` iterates the
use case's **budget rows**, the tab rendered every figure **inside a budget card**: no limit, no
denominator, no number — not even the numerator, which existed. **Two correct halves and no wire**,
in two different services. The fix is a **reader, not a calculation**:
`GET /v1beta/reporting?use_case=<slug>` narrows the report `FRD-601` already serves, and a
**Consumption** card sits on the **overview** beside the configuration tiles (this month, today) —
it shipped in the budgets tab, which was the defect's own shape, and the owner moved it. Source is the **request log**, so
the use-case page, the reporting screen and the export are three views of one number; `budget_usage`
stays what it is — an enforcement counter that exists only where somebody set a limit. Three rules:
**a filter narrows, never widens** (`scope = (use_case,)` is the natural way to write it, reads as a
narrowing and **is a widening** — every member of any use case could then be told any other's spend;
`N55`); **an empty report says whether it was allowed to be full** (`in_scope: false` — "not yours to
see" and "nothing happened" are both zero rows and only one is a measurement; `N56`); and **unknown
is never rendered as zero**, which is `FRD-403`'s unpriced rule one level up. No table, no column, no
migration. `windowFor`/`isoDay` moved into `core/ui/periods.ts` rather than being restated — the rule
inside them is an off-by-one that only appears in the evening. One correction found by reading the
code back: the two windows are **two requests** whose failure was tracked in **one boolean written by
both**, so a month already fetched was hidden when the day's request failed a moment later —
whichever finished last decided what the reader saw. **Partial is a third state**, not a variety of
unavailable; the test for it was written to fail first.

**Who answers for a credential (`FRD-604` Stage A, 2026-08-09)** — the installation ahead has an
**agentic coding** project where people issue their **own** keys and hand them to an assistant, and
a **RAG chatbot** served by one shared credential. One console, two opposite shapes. The
accountability chain already existed end to end — `ApiKey.owner` is a person, the issue event
carries their username, the gateway writes it onto **every** audit row beside the key prefix, and
the requests view filters by key — and **nobody was told**. The console recorded the issuer without
saying so at the moment of issuing, then printed that name beside an agent's traffic with no sign
that it means *who answers for the credential*, not *who wrote the request*: an investigator reads a
colleague's name next to a rogue agent and concludes a human typed it. Worse than an absent figure —
confident, and about a person. Stage A is four sentences and a badge (`via API key` on the row; an
OIDC caller is deliberately **unmarked**, because there the name *is* the person and marking both
makes the distinction useless), worded to hold for a shared key too. **Stage B done (2026-08-10)**:
`issued_by` beside `owner`, because signing in *as* a technical user needs shared credentials for a
governance console and destroys the one fact worth keeping. Two questions, kept apart — the owner
answers for the credential and is what every audit row carries (*a row describes what called*), the
issuer is the human who made it. A **string**, like `granted_by` and a suspension's author: who did
something is a fact about the past, so deleting the person must neither delete the record nor be
prevented by it; blank when they are the same person, since a distinction on every row is one
nobody reads. **The two refusals matter more than the feature**: an owner the directory does not
know, and an owner with **no access to this use case** — attaching a credential to an uninvolved
colleague would put their name beside an agent's traffic *deliberately*, which is this FRD's own
defect with the sign reversed. Typed, not picked from a directory, and written back into the FRD as
a deviation: the rule is *access to this use case*, and a picker over the membership list is
narrower than the rule, because a group-granted service account belongs to no membership row. Test lesson, second
instance after `N50`: three properties went red when broken and the fourth **could not** — a test
asserting an **absence** is defended by the mutation that *adds*, never by the one that removes.

**A role is held through a group (`ADR-0017`, `FRD-605`, 2026-08-09)** — the owner's rule: group
membership is the single point of truth. AIRA had **two** mechanisms answering "who is this" (realm
roles for the five roles, groups for use-case access), which is `FRD-209`'s defect one level up.
Mapping the realm role *onto* a group was the cheaper option and leaves the guarantee a
**convention**; `AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;…` makes a direct assignment
**structurally inert**, and the requirement was the guarantee. `realm_access.roles` is no longer
read. **The two use-case roles cease to exist** — administering a use case is a group's relationship
to *that* use case (`UseCaseGroupGrant`), and the proof they were redundant is that `may_admin`,
`may_manage`, `is_member` and `scope_queryset` needed **no change** while `IsUseCaseUser` turned out
to be used by nothing. The gateway's diff is **one call** (its vocabulary is all organisation-wide);
Management re-derived three predicates from what they *meant* — creating a use case is a Global
Administrator's act (a narrowing), the directory picker is "administers **any** use case" (removing
it would be `FRD-206` inverted), and a model test is `FRD-504`'s *whoever may call a model may test
one*. **The test migration was the audit**: the helper refuses the two dead roles **by name**, so
every one of thirteen files had to be looked at — and a blanket rewrite to `global-admin` made the
*boundary* tests pass for the wrong reason, since a Global Administrator is refused by nothing. The
frontend harness had the same trap: **a default nobody can hold is a harness testing a different
product.** Boot refusal is environment-shaped (`ADR-0015`). Verified live: `groups:
['/aira/global-admins']`, `realm_access: None`, oversight resolved.

**A security read of the whole code (`ADR-0018`, 2026-08-09)** — the request path held up (192-bit
keys in constant time, pinned JWT algorithm with `exp`/`iat`/`sub` required, payload access gated
and recorded, bounded bodies and schemas, no `eval`/raw SQL/disabled TLS, DRF authenticated by
default). **Three of four findings were in the space *between* the services** — a link AIRA trusts
completely and could not be told to verify. (1) **The event bus had no authentication and no way to
add it**: `apply_event` writes config topics straight into the read-model authorization comes from,
so anyone reaching the broker could publish `api_key.created` or `use_case_group.granted` and hold
admin on any use case — no credential, **no audit row**, because configuration arriving is not
unusual. (2) **Nothing required TLS to the identity provider or Vault**; over plaintext a
substituted JWKS mints tokens that verify. One rule in `aira_common.transport_security`, **loopback
exempt** — a rule worked around by `AIRA_ENVIRONMENT=local` disables every other check with it.
(3) **`X-Forwarded-For` was read from the left** under a docstring assuming a proxy that
*overwrites*, while the shipped nginx **appends** — so a caller chose their own audit address,
`FRD-505`'s incident filter, and the key the brute-force bound counts, which rotating the header
defeated. Read `AIRA_TRUSTED_PROXY_HOPS` from the **right** now. **The old test asserted the
leftmost entry — it had written the vulnerability down as expected behaviour.** (4) A comment
claimed the Vertex model segment was encoded; `httpx.URL(path=…)` leaves `/` and `..` alone and
*decodes* `%2f`, and `AzureRoutes` had solved it correctly one directory away. Two structural
guards, because the holes were invisible rather than wrong: every route must authenticate or be on
a **written** list (the Gemini surface's whole protection is one `dependencies=[...]` at mount
time), and building it taught that this FastAPI applies those from `_IncludedRouter`'s include
context — a guard reading only `route.dependant` reports the *protected* routes as holes and gets
"fixed" by exempting them. Test-quality pass: assertion-free tests were legitimate "does not raise"
cases; the real gap was the catalog validator's thirteen untested refusal branches (83% → 99%).
`W1`–`W4`. **Nothing the platform does changed.**

**Prompt caching, measured before it was designed (`FRD-133`, 2026-08-10)** — §5 named three
numbers that had to come out of `request_logs` first and left open that they might say *don't*.
They said build, and they **corrected the premise**: it is not the conversation that repeats. On 26
real OpenCode turns, `tools` is **69 %** of a request and `systemInstruction` **31 %**, while
`contents` is 0.1–5 %; 99.1 % of a large turn is repeated, the median gap between turns is 41 s
(13/14 inside five minutes) and 93.3 % of that use case's tokens are input. That is exactly
Anthropic's `tools → system → messages` hierarchy, so **two** breakpoints catch it — and it reverses
this FRD's own non-goal on automatic placement, because those boundaries are drawn by the API, not
guessed from a prompt. **The first measurement was wrong and pointed the other way**: comparing the
common *string prefix* of stored payloads gave 0.5 %, because the serialisation puts `contents`
before `systemInstruction` — it measured JSON key order, and the plausible number was the wrong one.
**What was already broken:** Anthropic cache tokens were folded into `prompt_tokens` and priced at
1.0× when a read is **0.1×** and a write **1.25×/2×** — wrong both ways, *under*-stating the
expensive one, so even a working cache was invisible and partly mis-billed. Built as accounting
first (three of four providers already report; only Anthropic takes a marker) then the marker,
because the other order produces a saving AIRA cannot show. Vendor facts from the vendors, including
§6's open question: **on Vertex the cache is isolated per organisation, not per workspace**, and
AIRA holds one credential per platform — hence per use case, default off. One rule is deliberately
unlike the rest: **a model that cannot cache is served uncached, never skipped** — every other
capability guards the *answer*, this one guards the *price*, and refusing a request over a price is
the opposite of what a fallback chain is for. `tools_enabled` had also existed only in the API since
`FRD-131`; both switches are in the console now.
**Stage C asked which parameters are worth offering, and the answer is one**: the **lifetime**
(`5m`/`1h`). Everything else is fixed by the vendor or settled by the measurement, and a control
with one correct answer is `FRD-206`'s complaint in another key. The lifetime is the exception
because **only the caller's own traffic settles it** — an hour costs about double to write and pays
only where the gap between turns regularly exceeds five minutes, the opposite of OpenCode's 41 s
and plausibly true of a chatbot a human reads between. Every control says what it **costs**, not
what it does; the two catalog price fields say which *direction* they go, because that is the
surprising part (cached input a tenth, a write 1.25×/2×) and a field labelled only "cached input
price" invites the ordinary rate — already the fallback, and then the wrong figure looks
deliberate. **Tuning is empirical only if the effect sits beside the setting**, so the consumption
panel gained a **Cached** share whose hint names the four different reasons for 0 %. And the part
no earlier test could see: the mapping tests prove the marker is *built* right and say nothing
about whether the configuration *arrives* — four hops where a dropped setting yields a served
request indistinguishable from one nobody asked to cache (`FRD-124`'s lesson). The mock now **says
what it was asked**, as it already does for thinking and attachments, and reports **no cache hit on
purpose**: fabricating one would make every "caching saves money" assertion true by construction.
The harness reported `U5` **STALE** rather than green — its anchor was the constant that became a
function — which is `N2`'s lesson working as built.
**And the console could not declare the capability it had just been given price fields for**: the
SPA restates the closed capability vocabulary as a TypeScript array and was missing **`tools`**
(since `FRD-131`) and **`prompt_caching`** — five checkboxes where there should be seven, which
announces itself through nothing, because an absent checkbox reads as a design decision. Fourth
instance of the same answer: **compare the hand-written list against the constant in both
directions** (after `aira.rate-limits`, `aira.anomaly-rules`, `use_case_group.granted`) — the other
direction matters too, since a box the gateway ignores is a declaration somebody believes they
made. Each capability now says what ticking it commits to, in a `Record<Capability, string>` so
the **compiler** refuses an undocumented one, shown to fail by deleting an entry. One e2e guard was
narrowed and proved sharp first: `expectFormControlsAligned` counted an info hint's trigger as a
row control, and a hint sits **inside a `<label>`** one line above its field, so every explained
field read as a staircase; excluded by *where it is*, then a 6 px misalignment was injected to
watch it fail — 12 px did **not**, because the guard bands rows at 40 px. `U1`–`U9`.

**A walkthrough of the new controls (2026-08-10)** — four reports, three of them defects in the
same day's work, and every one measured before it was touched. **The explanations misbehaved as
overlays**: the panel inherited `white-space: nowrap` from `.form-inline .field > label` (a
372-character sentence laid out as **one 2210 px line in a 478 px box**) — it already resets four
other typographic properties for exactly that reason and this one was missing from the list, since
**a panel openable from anywhere owns its typography**; and `position: absolute` **extends the
scroll container**, so opening one summons a scrollbar, reflows the page narrower, slides the "i"
out from under the pointer and flickers forever, while centring it on the button put it **58 px
outside the dialog**. Now `fixed`, clamped into the viewport, flipping above the anchor when there
is no room below, replacing a hand-written escape for the last table cell — one rule for every
edge. **And `fixed` is not always viewport-relative**: any ancestor with a `transform` becomes the
containing block, which the modal has, so the first attempt landed 201 px off; the origin is *read*
now (park at 0,0, see where that is, subtract) — reasoning about coordinate spaces is how the bug
was written. **Two switches were in the wrong panel**: function calling and prompt caching had
landed in the nearest form, data protection's, *between* "store prompts" and "keep them for N
days" — the pair a reader treats as one setting — and turning caching on answered with a sentence
about retention, a confident statement about the wrong thing; own section, own save, own message,
asserted as *what may come between the two controls*. And **`make showcase` could not bring up a
coding assistant** although `tools/opencode/README.md` had named a `coding-assistant` use case
since `FRD-132` — an instruction with no destination. The keeper among its four missing pieces:
**the Management-side model seed did not declare `tools`** while the gateway-side one did, from the
same measurement — second time that pair holds one fact and two answers (after `minimal`), and the
consequence was silent and total, the assistant refused by name with every explanation pointing at
the client. Now a `coding-assistant` use case (the only one with function calling on), limits sized
for an agent rather than a chatbot, and `tools/showcase_agent.py` writing an OpenCode config that
`make showcase` prints; verified live, a real tool call audited as `{"declared": 1, "called":
["read_file"]}`. Caching stays **off** there and the description says why: this runtime reports no
cached tokens, and a switch shown on while doing nothing is `FRD-125`'s badge in the place a reader
is likeliest to believe it. **And `make showcase` then tried to *pull* `aira-gateway` and
`aira-management`**: four services ran a second process out of a sibling's image and carried
`image:` with no `build:`, and compose pulls whatever it is not told how to build. It failed **only
on a machine that had never built them** — everywhere else the tag was already in the local store —
so it worked for everyone who had run the stack and broke for exactly the person the target is for.
Every service names its build now, and a test parses both compose files and refuses one that names
a locally-built image without saying how to build it. **Running the target to the end then found
two more of the same family** — *it works on a machine that has already done the thing by hand*.
The dev Vault runs `server -dev` and forgets on every restart, so after a `down` the path
`secret/aira` is gone, `load_secrets()` fails closed (correctly) and **every container refuses to
boot** — the showcase silently required somebody to have run `make vault-init` *after* the current
Vault container started, which is a trap laid by our own documentation. Provisioned by a `vault-init`
service now, with the migrations waiting on it, because ordering belongs in the file that owns
ordering rather than in one of four entry points — and putting that service **behind a profile was
a regression of its own**, since compose rejects the *whole project* when something depends on a
service the active profiles omit (`invalid compose project` without `--profile demo`, which is how
CI dumps logs when something has already gone wrong); it is gated by the **environment** instead,
and a test states the containment: whatever enables a service must enable everything it depends on; two mistakes while writing it are
the keepers — writing all three known secrets unconditionally made the **empty string win over the
environment** (Vault ranks above it: *absent and empty are different answers*, `no password
supplied`), and writing none failed with `Must supply data`, because **an empty write is not a
write** and the path still did not exist. And the demo **spent its own budgets**: they are
calibrated so a handful of requests fills each bar, so a second run answered 429 to six of ten
requests — including the injection case whose point is a *pipeline* refusal. `make showcase` now
clears what earlier runs **consumed** (Postgres *and* the shared Redis counters — clearing one
leaves the other refusing for a period nobody can see) and nothing the demo **is**; `make
showcase-traffic` deliberately does not, since filling the bars until a limit is reached is what
that target is for. Two consecutive runs now produce the same thing: ten served, one refused.
**On a machine that had never run it** (`.env` deleted, DB and Kafka volumes removed) it **reported
success while serving nothing** — ten requests, ten refusals, *"not in the model catalog"*: the seed
**wrote the catalog and never announced it** (`local_models` created the rows and emitted no event;
only the viewset emitted, so a console-declared model reached the gateway and a seeded one did not).
Invisible until `FRD-307` made a catalogued, approved model the *only* servable kind — from then on
an unannounced catalog refuses **everything**. Fourth instance of *two correct halves and no wire*,
after `record_to_outbox`, the missing topics and `payload_size`; it emits the **viewset's**
`_payload`, since a second hand-written payload is a second place to forget that prices travel as
decimal strings. The target reported success over it because the traffic script failed only on a
5xx — **nothing served is a failure now**, and the Makefile no longer swallows its exit code. The
model pull gained three attempts and an explanation (a single failed download used to surface as
compose blaming `management-seed`, a service that never ran).
**And then nobody could log in**: `make showcase` prints five accounts and Keycloak answered
*invalid username or password*, because it imports a realm **only if it does not exist** — so every
edit to the realm file reaches a fresh machine and no other, and the repository said so in a README
(*a demo that works for whoever wrote it*). `keycloak-init` compares the running realm against the
file and re-imports when something is missing; idle otherwise, gated to `local`/`demo`, and never
aimed at a directory AIRA does not own. Three defects while building it: the check asked
`GET /groups` (which since Keycloak 26 omits `subGroups`) and `GET /users` (which omits service
accounts), so it **deleted a healthy realm** on the first run; a realm delete is finished
*asynchronously*, so the create was overtaken and the script announced a realm that did not exist;
and **the repair fixed the directory and corrupted what reads it** — a re-import minted new
subjects, `ADR-0007` binds Django users to the `sub`, and `ucadmin` came back as
`ucadmin-279b6b7b`, owning nothing. The answer is that **the demo's identities are fixtures and
must not move**: every user and group in the realm file carries a stable id now, so a re-import
keeps every binding — `FRD-130`'s deterministic-demo-key rule, one identity system over.
**CI then failed on two tests that pass everywhere this project has been written**, one defect in
two costumes: **a unit test that reads the developer's machine is a test whose green is about that
machine.** `BaseAiraSettings` loads `.env` from the working directory — right for `make
run-gateway`, wrong for the hermetic suite — so a Google key in it put a Gemini upstream in the
registry and gave one test the *other* models it asserted were undeclared; and `/readyz` makes
**real TCP checks**, so a readiness test passed on any machine with the stack running. A root
`conftest.py` disables the dotenv and clears `AIRA_*`/`VAULT_*`; the two tests then say what they
need — one registers a second mock provider, the other opens a socket and points Postgres and Kafka
at it (the probe stays real; stubbing `check_tcp` was the worse option). **Reproduced by stopping
the stack.** Also: the showcase **advertised a console it had never waited for** (it waited on the
gateway alone, then printed `SPA http://localhost:4200`) — it calls `wait-healthy` now, the same
check CI uses, and its output says which ports have a user interface and which do not.
**The two build systems also ran at different levels, and that was not cosmetic**: both Python
images build from the repository root, the frontend built from `management/frontend`, and
**Docker reads `.dockerignore` only from the context root** — so the repo's ignore file never
applied to the frontend and its `COPY . .` copied a developer's **287 MB `node_modules` on top of
the tree `npm ci` had just installed** (proved with a marker file: the host's copy wins). The image
was therefore built against whatever was on that machine's disk — esbuild and rollup ship *native*
binaries — which is a generator of "it builds for me" and nothing else, with the `npm ci` layer
above it doing nothing. One context root now, named copies instead of `COPY . .`, context **1.5 MB
instead of 302 MB**, and a test requires every image to share the root.

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
