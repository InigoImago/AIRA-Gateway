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
  `make mutants` (`tools/mutation_check.py`) does this for **231 properties** across auth, budgets,
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
