# AIRA Gateway — Delivery Roadmap (Phases)

> Status: **Draft v0.1** · Last updated: 2026-10-14
> Companion to `docs/PRD.md`. Each phase lists its goals, deliverables, and the FRDs it contains.
>
> Phases 0–6, 8 and 9 are delivered, Phase 7 in part. Where a phase's plan was not built, the
> phase says so in place and why. A feature's status is in its FRD's header and in the
> [generated index](features/README.md); what is open across phases is in
> [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md).

Guiding principles:
- **Vertical slices**: each phase ends with something runnable and demoable via `docker compose up`.
- **Test-first**: every phase carries its own unit/integration tests; coverage gates from Phase 0.
- **Demo mode always works**: the mock upstream + seed data are maintained across all phases.
- **Kafka contract early**: the event schema is established before both components depend on it.
  *As built:* the contract is a shared vocabulary — topics in `aira_common.kafka`, event types
  checked across the planes by `tools/tests` — not a versioned schema.

---

## Phase 0 — Foundation & Infrastructure
**Goal:** A reproducible local platform and engineering baseline everything else builds on.

**Delivered.**

Deliverables:
- Monorepo layout (`gateway/`, `management/backend/`, `management/frontend/`, `deploy/`, `docs/`).
- Docker Compose stack: PostgreSQL, Keycloak, Kafka (+ schema registry), Vault (dev mode). The
  schema-registry container still runs and no code uses it; removing it is open.
- **Local observability backend**: OpenTelemetry Collector + **Grafana otel-lgtm** (traces +
  metrics + logs), both components wired to export via OTLP from day one (ADR-0004).
- CI pipeline with lint + type-check + tests + **coverage gate**.
- Shared conventions: logging, config, error handling, OpenTelemetry bootstrap.
- **Seed framework**: one command to load test data (extended each phase).

FRDs: `FRD-000-foundation-infra`, `FRD-001-observability-baseline`, `FRD-002-seed-and-demo-mode`;
later `FRD-130` (a demo somebody can walk through, `make showcase`).

---

## Phase 1 — Gateway MVP (data plane)
**Goal:** A working unified API that authenticates, attributes, dispatches to a mock upstream, and
persists everything.

**Delivered**, except the two lines struck below.

Deliverables:
- **Gemini-compatible** endpoints (`/v1beta/models/{model}:generateContent`, `:streamGenerateContent`,
  `:embedContent`, `GET /v1beta/models`) → canonical internal schema. **This ships first** (existing
  projects run on Gemini); ~~the **OpenAI-compatible** surface is added later as `FRD-106`
  (ADR-0005)~~ — withdrawn on 2026-08-07 (Phase 8).
- **API-key auth** (issue/hash/verify/revoke) + **OIDC bearer** validation against Keycloak.
- Request attribution to user / ~~project~~ / use case. A project is a non-goal (PRD §2.2).
- **Mock upstream** provider; request/response **persistence**; source-IP capture; tracing spans.
- ~~Kafka usage/audit event emission (schema v1).~~ **Not built.** The gateway publishes no events;
  the console reads its HTTP API, and the audit trail as an event stream is `FRD-616`.

FRDs: `FRD-100-gemini-api`, `FRD-101-auth-apikey-oidc`, `FRD-102-attribution`,
`FRD-103-request-response-persistence`, `FRD-104-mock-upstream`, `FRD-105-tracing-and-ip`.
~~Later: `FRD-106-openai-api` (OpenAI-compatible surface on the same canonical schema).~~ Withdrawn,
and never written.

---

## Phase 2 — Management Foundation (control plane)
**Goal:** The management app exists with SSO, RBAC, and use-case CRUD; Angular shell in place.

**Delivered.**

Deliverables:
- Django + DRF backend; Keycloak SSO (OIDC); roles held through groups (three built in, `ADR-0017`; more an installation defines, `ADR-0025`) + object-level perms (guardian).
- **Use-case self-service**: create/edit use cases, assign members, membership → access.
- Angular SPA shell: login (OIDC), role-aware navigation, use-case list/detail.
- Config event schema (Management → Gateway) v1 over Kafka; Gateway read-model skeleton. *As
  built:* JSON events named by an `event_type` header, from a transactional outbox (`FRD-204`);
  there is no version field and no schema registry in use.

FRDs: `FRD-200-mgmt-backend-foundation`, `FRD-201-keycloak-rbac`, `FRD-202-usecase-crud`,
[`FRD-209`](features/FRD-209-access-by-group.md) (access by Keycloak group, with a directory search),
`FRD-203-angular-shell`, `FRD-204-config-distribution-kafka`,
`FRD-205-api-key-issuance` (self-service API-key issuance in Management + `api_key.*` Kafka events →
Gateway read-model; the Gateway keeps validation only — see ADR-0006).

---

## Phase 3 — Routing, Pipeline & Fallback
**Goal:** Use-case-driven pre-dispatch pipeline with sequential/parallel execution and fallback,
configured in the UI and enforced by the gateway.

**Delivered**, with two changes to the plan. Parallel branches are a **non-goal**: the engine is
an ordered walk (`FRD-300`, PRD §2.2). The allow-check step is **gone**: which models a use case may
call is the installation's approval and the use case's release, checked at every hop
(`FRD-307`, `FRD-308`). The step types are an injection filter, PII replacement (`FRD-309`) and
LLM routing (`FRD-306`).

Deliverables:
- Pipeline engine in the gateway: ordered steps, **sequential**~~/parallel~~ branches,
  injection-filter and ~~allow-check~~ step types, **model routing** + cost-based **rerouting**,
  **fallback** chains.
- **Pipeline builder** UI (Angular) — ordered~~/parallel~~ steps + fallback editing.
- First **real upstream adapters** (Gemini Enterprise, Microsoft Foundry) behind the mock-compatible
  interface. *As built:* the first was Google AI Studio (`FRD-304`); Vertex and Foundry came in
  Phase 8, and Foundry has never run against Azure (`FRD-120`).

FRDs: `FRD-300-pipeline-engine` (routing is specified there; `FRD-301` was never written),
`FRD-302-model-fallback`, `FRD-303-pipeline-builder-ui`, `FRD-304-upstream-adapters`.

---

## Phase 4 — Governance: Budgets & Quotas
**Goal:** Cost/usage control per use case and per member, enforced and visible.

**Delivered**, except threshold alerts.

Deliverables:
- **Budget** definitions (per use case / per member), usage accounting, enforcement in the gateway.
- UI for setting budgets and viewing consumption; ~~alerts on threshold breach~~ — not built. The
  console colours a bar from 80 % and a caller can read its own allowance (`FRD-139`), but nobody is
  told before a limit refuses (Backlog).

FRDs: `FRD-400-budget-model`, `FRD-401-budget-enforcement`, `FRD-402-budget-ui`; then `FRD-403`
(cost), `FRD-404` (retention), `FRD-405` (rate limits) and `FRD-406` (redaction).

---

## Phase 5 — Anomaly Detection & IT Security
**Goal:** Self-service anomaly rules + IT Security console with incident response.

**Delivered**, except data-leak detection.

Deliverables:
- **Anomaly rules** self-service: rate-based, size-based, ~~data-leak detection~~ — not built: the
  seven kinds watch rates, spend, sizes and addresses (`FRD-500`).
- Detection ~~pipeline (gateway signals → Kafka → evaluation)~~ and **AnomalyEvent** store. *As
  built:* the evaluator runs in the gateway, off the request path, over the audit trail itself
  (`FRD-501`, `ADR-0014`); nothing passes through Kafka.
- **IT Security console** done (`FRD-502`, 2026-08-08): cross-use-case findings, suspensions and the
  kill switch, the rules behind them, warnings per use case for the members who could fix the cause,
  and a per-use-case request view — all refreshing live. Stored content came with `FRD-505`: a role
  that may read it sees the prompt, and every read is recorded (`ADR-0016`, `FRD-622`).
- **Incident response** engine: configurable **throttle / alert / block** per rule.

- **The question catalogue** — `FRD-504`, written 2026-08-06 as model smoke tests and jailbreak
  batteries; **built**, and since `ADR-0020` (2026-08-16) a run is put to a **use case's own
  pipeline** rather than to a model. The two modes the draft asked for are one mechanism: run it
  against a filtering pipeline and you measure the filter, against a bare one and you measure the
  model. Testing a model is a use case whose pipeline starts at it. Still outstanding from the
  draft: a **rate over repeated attempts** rather than a single answer, because a model that
  refuses nine times out of ten is the finding.

FRDs: `FRD-500-anomaly-rules`, `FRD-501-anomaly-detection-engine`,
[`FRD-502`](features/FRD-502-security-console-and-traces.md),
`FRD-503-incident-response`, [`FRD-504`](features/FRD-504-model-smoke-tests.md),
[`FRD-505`](features/FRD-505-requests-and-prompts.md).

**Phase 5 carries three of the owner's seventeen central features** (PRD §1.1): incident response,
dangerous-request blocking beyond the injection filter, and anomaly detection. Together with
`FRD-122` they are the *evidence* half of the product. Incident response and anomaly detection are
delivered; blocking stays partial (PRD §1.1 row 7).

---

## Phase 6 — IT Steuerung (Governance) & Analytics
**Goal:** Oversight and aggregate reporting for governance.

**Delivered.**

Deliverables:
- **IT Steuerung views**: all use cases with descriptions + processing logic (read-only), aggregate
  statistics and cost/usage analytics. — **`FRD-601` done (2026-08-06)**: spend and usage
  reporting, scoped so governance sees every use case and everyone else sees their own.
  **`FRD-603` done (2026-08-09)**: a use case's own page states what it consumed this month and
  today, **whether or not a budget is set** — the figures were always recorded and only ever
  displayed as a fraction of a limit.
- Global monitoring dashboards. *As built:* the console's reporting, and one Grafana dashboard read
  from traces.
- ~~Still open here: charts, and the read-only "processing logic" view of every use case.~~ Both
  delivered: the charts are `FRD-626`, and the processing-logic view is the register,
  [`FRD-608`](features/FRD-608-governance-overview.md). Export is `FRD-602`; per-request browsing is
  `FRD-505` (`ADR-0016`).

FRDs: ~~`FRD-600-governance-views`~~ — never written; the governance view is `FRD-608`.
`FRD-601-spend-and-usage-reporting`, `FRD-602-report-export`, `FRD-603-use-case-consumption`,
`FRD-626`; the rest under this phase are in the index below.

---

## Phase 7 — Hardening & Production Readiness
**Goal:** Production polish and a path off the developer machine.

**Partly delivered.** Capacity is measured (`FRD-136`), releases are published and run from the
registry (`FRD-137`, `FRD-138`), what a role may do is a set of permissions an installation can
change (`FRD-614`), and the stages may share a Kafka cluster and a Redis Sentinel (`FRD-623`).
Several gateway instances (`FRD-127`) and the audit trail as an event stream (`FRD-616`) are
specified and not built; Kubernetes/Helm has no FRD yet.

Deliverables:
- Security hardening & pen-test fixes; retention/redaction policies finalized.
- ~~Performance/load testing~~ — **delivered early** as
  [`FRD-136`](features/FRD-136-capacity-the-gateway-is-not-the-bottleneck.md), because the owner
  needed a concurrency answer before the phase: a repeatable harness against a free upstream
  double, the cost of a request in each of the three real workloads, and the three places the
  deployment gives way. Sizing for a reader: [`DEPLOYMENT.md`](DEPLOYMENT.md) §1a. HA considerations
  remain, under [`FRD-127`](features/FRD-127-several-gateway-instances.md).
- ~~Published images~~ — **delivered** as
  [`FRD-137`](features/FRD-137-publishing-versioned-images.md) (`ADR-0029`, `ADR-0030`), ahead of
  the charts because a chart has nothing to pull without it: pushing an annotated tag publishes the
  three images to GHCR behind every CI gate, with the tag's own message as the release notes. The
  version lives in the tags only.
- ~~Running them~~ — **delivered** as
  [`FRD-138`](features/FRD-138-running-a-published-release.md) (`ADR-0031`): two Compose overlays,
  one for where the images come from and one for where the infrastructure is, so a deployment runs
  a version it did not build (`RELEASE=vX.Y.Z` on any `up*` target) and an integrated one supplies
  its own Postgres, realm, broker and Vault. Proven by running the whole integration suite against
  `v0.1.0` pulled from the registry. This is the piece Helm will want next.
- **Kubernetes/Helm** charts; secrets via Vault in-cluster; production observability stack.
- Coverage/quality audit against the near-100% target.

FRDs: ~~`FRD-700-hardening`, `FRD-701-k8s-helm`, `FRD-702-perf-and-ha`~~ — none was written.
Performance is `FRD-136` and high availability `FRD-127`; the phase's FRDs are in the index below.

---

## Phase 8 — KIRA parity
**Goal:** AIRA carries every capability of the **predecessor API**, so that it can be
decommissioned.

**Delivered.** What follows is the plan as it was made, kept for its reasoning; the tables say what
became of each item.

A review found the gap is not where it might be assumed. In breadth AIRA is well ahead of the
contract it has to carry — use cases, budgets, rate limits, the pipeline and the management UI are
all ours to keep. In the **core request path** it is behind: the canonical message carries one
field, `text`, so documents and images are rejected at the door, thinking budgets do not exist, and
structured output does not exist. There is also a difference in *where* the model runs — the
contract assumes EU-regional endpoints with a service account; we use the global Generative
Language
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
dispatch timeout and the readiness probe must treat differently from quota. *As built:* that
self-deployed side is not built (`FRD-115` FR-2a); Agent Platform's managed open models (GLM,
DeepSeek) are, on the same transport.

`ADR-0010` framed the programme and its one open decision — whether AIRA also serves the
predecessor's **wire contract** — and is now **accepted (Option C)**: the owner's feature definition
(PRD §1.1) names KIRA-API compatibility as central, so the compatibility surface is built, with a
sunset date and its usage visible in reporting.

### Delivery order (2026-08-06)

The owner's priority is KIRA compatibility, then the Google/Microsoft model connections, then
document handling, then the review findings (PRD §1.3). Priority 1 depends on priority 3, so the
order below serves the priorities rather than restating them.

| Stufe | Was | Warum hier |
|---|---|---|
| **0** done | ~~`FRD-122` — vollständiger Audit-Trail~~ **fertig 2026-08-06** | Klein, additiv, kein Eingriff in den Request-Pfad — und **jede spätere Stufe wird dagegen getestet**. Ablehnungen, das aufrufende System und `requested_model` vs. `model` sind genau das, was man bei Fallback über zwei Anbieter und einer zweiten API-Fläche braucht. Zuerst, weil es danach mühsamer nachzurüsten ist als jetzt. |
| **1** done | ~~`FRD-114` — Modell-Metadaten~~ **fertig 2026-08-06** | Voraussetzung für alles: Publisher, Capabilities, Default-Cap, Adressierung, Hosting. |
| **2** done | ~~`FRD-115` + `FRD-119` — Vertex EU, Gemini + Anthropic~~ **fertig 2026-08-06** | Priorität 2, erste Hälfte. Erst danach ist überhaupt ein produktionsfähiges (EU-)Modell erreichbar. |
| **3** done | ~~`FRD-110` — Dokumente~~ **fertig 2026-08-06** | Priorität 3. Bewusst **nach** Stufe 2: ohne dokumentenfähiges Modell in der EU wären Dokumente nur gegen den Mock nutzbar. |
| **4** done | ~~`FRD-107` Stage A — KIRA-Fläche~~ **fertig 2026-08-06**, inkl. Dokumenten | Priorität 1, so früh wie ehrlich möglich. Nicht unterstützte Felder werden **abgewiesen**, nie ignoriert (`FRD-107` §5.2). Einfache Clients migrieren hier. |
| **5** done | ~~`FRD-111`, `FRD-112`, `FRD-113`~~ **fertig 2026-08-06** | Thinking, strukturierte Ausgabe, Embedding-Optionen. |
| **6** done | ~~`FRD-107` Stage B~~ **fertig 2026-08-06** | Dieselben Felder wurden von abgewiesen zu bedient — **Vertrag unverändert**, direkt mit Stufe 5 ausgeliefert, weil eine Fähigkeit zu bauen und sie an der Kompatibilitätsfläche weiter abzuweisen niemandem nützt. |
| **7** done | ~~`FRD-120` — Microsoft Foundry~~ **fertig 2026-08-06** | Priorität 2, zweite Hälfte. **Der Diff hat `upstreams/` nicht verlassen** — Feature 19 damit belegt, nicht behauptet. Hermetisch getestet; es gibt hier keine Azure-Subscription. |
| **8** done | ~~`FRD-116`, `FRD-117`, `FRD-602`, `FRD-124`, `FRD-406`~~ **fertig** | Vault, Diagnostik, Export, Maskierung. `FRD-117` ohne FR-7, `FRD-406` nur die Credential-Hälfte. |
| **9** done | ~~`FRD-504`, `FRD-500`/`501`/`503`~~ **fertig** | IT-Security: Smoke-Tests, Anomalien, Incident Response. |
| **9** done | ~~`FRD-131`, `FRD-132`, `FRD-133`~~ **fertig** | Agenten und Coding-Assistenten: Tool Calling (per Use Case, Default aus), Flächenentscheidung nach Messung — keine neue Fläche (`FRD-132` B1) —, Prompt-Caching zuletzt. |

Two deliberate deviations from a naive reading of the priority list, both stated so they can be
overruled rather than discovered:

- **`FRD-122` is first, not last.** It is one of "my points" and it is also the cheapest thing in the
  programme. Every stage after it produces traffic that ought to be evidenced, and retrofitting the
  audit once four vendors and two API surfaces are live is strictly harder than doing it while there
  is one of each.
- **Documents come after the EU connection**, not before, because a document capability that can only
  be exercised against the mock is not the capability that was asked for.

| FRD | What | Blocking? |
|---|---|---|
| ~~`FRD-110`~~ | Documents and images in a request | **Done (2026-08-06).** Ordered parts, allow-list + signature check + bounds, per-model media types checked after routing, reservation counts them, audit keeps a description not the bytes. A model that cannot read it **refuses**. |
| ~~`FRD-111`~~ | Thinking modes and budgets | **Done (2026-08-06).** Sieben Modi, pro Modell validiert, Level→Budget aus dem Katalog; die Reservierung enthält das Budget, und eine Kette überspringt einen Kandidaten, der es nicht kann. Gedanken werden zurückgegeben, solange der Use Case es nicht abschaltet (`include_reasoning`, Standard an, `FRD-135`). |
| ~~`FRD-112`~~ | `responseSchema` — forced JSON output | **Done (2026-08-06).** Ein Feld, das wir nicht kennen, wird **benannt** abgelehnt; Grenzen für Größe/Tiefe/Anzahl; Gemini per Parameter, Anthropic per `output_config` (JSON-Schema) neben den Tools; die Fähigkeitsprüfung läuft **pro Hop**. |
| ~~`FRD-113`~~ | Embedding task types, batches, dimensions | **Done (2026-08-06).** Ein Batch von n wiegt **n** gegen Limit und Budget — sonst wäre das Limit auf dem Papier intakt und in der Praxis weg. |
| ~~`FRD-123`~~ | A real local model (Ollama) over the OpenAI dialect | **Done.** Ollama runs in the stack (`make verify-up`), over the dialect `FRD-120` reuses. |
| ~~`FRD-114`~~ | Model capability metadata | **Done (2026-08-06).** Capabilities, publisher/platform/addressing, output caps, thinking/embedding/attachment declarations, hosting, deprecation. Undeclared = baseline only. |
| ~~`FRD-115`~~ | Vertex AI / Model Garden in the EU | **Done (2026-08-06)** except FR-2a's self-deployed endpoints. Shared `TokenSource`, region allow-list enforced at startup, ambiguous routing table refuses to boot, provenance on every audit row. |
| ~~`FRD-119`~~ | Anthropic models on Vertex: the second dialect | **Done (2026-08-06)** for what the canonical core carries today. Thinking / structured output / attachments land with `FRD-111`/`112`/`110`. |
| ~~`FRD-120`~~ | Microsoft Foundry (Azure OpenAI + Microsoft's own) | **Built, hermetically verified only** (stage 7): no Azure subscription has ever run it. See `ADR-0011` |
| `FRD-121` | Document conversion for models that cannot read documents | **optional — probably do not build first**, `ADR-0012` §4 / `ADR-0013` |
| ~~`FRD-122`~~ | A complete audit trail — refusals, asked-vs-served, decisions, degradation | **Done (2026-08-06).** Refusals of an attributed request are recorded (one refused before attribution is on its span), the calling system is identified, asked-vs-served is distinguishable, pipeline decisions and degradation are on the row, and reporting counts refusals. |
| ~~`FRD-116`~~ | Secrets actually read from Vault | **Done.** Policy and implementation were apart since Phase 0; Vault is now a settings source, fail-closed. |
| ~~`FRD-117`~~ | Version info, upstream health, CORS, OpenAPI 3.0, trace header | **Done except FR-7** — the second OpenAPI 3.0 document is not built. |
| `FRD-118` | Several Keycloak backends, groups from UserInfo | **FR-1 built** — the gateway accepts several issuers; FR-3 (groups from UserInfo) declined. |
| ~~`FRD-602`~~ | CSV export of the usage report | **Done.** |
| ~~`FRD-107`~~ | The KIRA wire format itself | **Done — Stage A + B (2026-08-06).** Stage A carried documents because `FRD-110` landed first; Stage B (thinking, structured output, embedding options) shipped with `FRD-111`–`113`. |

**Withdrawn (2026-08-07):** the OpenAI-compatible **surface** (`FRD-106`) is not wanted. It was
raised as a thought experiment about generalisation and served that purpose — `FRD-126` and
`FRD-128` came out of it — but no OpenAI-shaped API will be exposed to callers. The OpenAI *wire
dialect* stays, as an **upstream** (`ADR-0011`): Azure, Vertex's managed open models and
self-hosted servers speak it, and that is unaffected. The two are different things and only one of them was ever deferred rather than
declined.

The question was reopened once, on 2026-08-08, and answered by measurement: `FRD-132` stage A
served OpenCode on the Gemini surface, so the withdrawal stands (B1: no new surface). `FRD-106`
has no file.

---

## Phase 9 — Agents and coding assistants (`FRD-131`–`FRD-133`)

**Goal:** the third named use case — connecting coding assistants and agents such as **OpenCode** —
alongside the two that already work (RAG chat, semantic search over embeddings).

**Delivered.** Tool calling is built (`FRD-131`); the surface question was answered by measurement
— the Gemini surface serves OpenCode, no new surface (`FRD-132` B1); prompt caching is built
(`FRD-133`).

A review on 2026-08-08 established where the gap was, against the code rather than the docs:

- **Semantic search** works today. `:embedContent` with batching and task types (`FRD-113`),
  budgeted, priced, audited. Vector storage is the caller's, by `ADR-0013`.
- **RAG chat** works today for the generation half: documents (`FRD-110`), structured output,
  streaming, system instruction. Retrieval is the caller's.
- **Coding assistants did not work at all**, and the blocker was one field: `tools` was refused
  with a 400. Every assistant's loop *is* tool calling. Closed by `FRD-131`.

### Order, and why

| Step | FRD | Why here |
|---|---|---|
| 1 done | `FRD-132` **stage A** | Point OpenCode at the running gateway and record what breaks. Cheap, and it decides whether a new surface is needed at all — a question this project has repeatedly got wrong by reading instead of sending. Needs a host where Ollama and OpenCode can be exercised together. |
| 2 done | `FRD-131` | Tool calling in the canonical core, **per use case, default off**. Nothing works without it, whatever the surface. |
| 3 — not needed | `FRD-132` ~~**stage B**~~ | Stage A answered B1: no new surface. The plan was: only if stage A says so, an OpenAI-compatible surface (this would revive `FRD-106`, withdrawn 2026-08-07 — a named client that cannot be served without it is exactly the evidence that was missing then). Much cheaper now: the OpenAI *dialect* already exists as an upstream (`FRD-123`). |
| 4 done | `FRD-133` | Prompt caching. **Written now, built last, by owner decision (2026-08-08):** the assistant work must stand at full price and be measured that way, so the saving is decided from `request_logs` rather than from an estimate. |

**Least privilege is the design, not a setting bolted on.** `tools_enabled` is per use case and
defaults to **off**: a use case that summarises documents has no business declaring functions, and
the smallest set of use cases that need tool calling is the right set to have it.

Two things this phase changes about the governance model, recorded in `FRD-132` §5 rather than
discovered later: an assistant makes **many model calls per human instruction**, so rate limits and
budgets calibrated for a chatbot trip immediately and "requests" means something different on the
reporting screen; and a **tool result is content the model reads**, which the injection filter
cannot see — the same blind spot `FRD-110` recorded for PDFs, one step sharper. The `tool_results`
filter scope `FRD-131` names is not built ([`GAP-ANALYSIS.md`](GAP-ANALYSIS.md) §3.4).

---

## Backlog — agreed, not yet scheduled

Work that is decided but deliberately not in the current phase. Ordered as agreed with the
product owner; the reason for the order is recorded so it is not re-litigated later. Gaps nobody
has agreed to schedule are in [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md).

| Item | FRD | Why it waits |
|---|---|---|
| ~~Per-caller rate limiting + the budget guard/record race~~ | `FRD-405` | **Done (2026-08-05).** Token buckets and atomic budget reservations over Redis (ADR-0008); the audit write also moved off the request path. |
| **Content redaction** — masking sensitive values *inside* a stored payload | `FRD-406` | **Credentials: done (2026-08-08).** API keys, bearer tokens, JWTs, `Authorization:` values and PEM private key blocks are masked before storage, plus any pattern a deployment adds (`AIRA_REDACT_PATTERNS`, additive). **PII: deliberately not**, because names and customer numbers are what the payload is stored for and a redactor that mangles them ends with storage switched off entirely — for that, the control is `FRD-404`'s per-use-case switch, and since `FRD-309` a use case may replace personal data before the prompt is sent and stored (`pii_filter`). The PII half is declined, not deferred (`ADR-0016`). |
| ~~Spend and usage reporting~~ | `FRD-601` | **Done (2026-08-06).** Gateway `GET /v1beta/reporting` + a **Reporting** screen: totals and breakdowns by use case, model and member over a chosen period, scoped by the caller's role (ADR-0009). Per-request *browsing* came with `FRD-505` (`ADR-0016`). |
| ~~A use case's consumption without a budget~~ | `FRD-603` | **Done (2026-08-09).** Consumption was rendered only inside a budget card, so an unlimited use case showed neither tokens nor spend while `request_logs` held both. `GET /v1beta/reporting?use_case=` plus a Consumption card above the budgets. |
| Budget threshold alerting | `FRD-402` follow-up | Open. The console colours a bar from 80 % and a caller can read its own allowance (`FRD-139`), but a breach is still a 429 — nobody is told before the wall is hit. |
| ~~Membership reconciliation (Keycloak groups ↔ Management)~~ | `FRD-209` | **Closed.** A grant names a group or a person, and both planes resolve access with `aira_common.access.resolve`, so there are no longer two answers to drift apart. |
| ~~Pagination~~ | `FRD-207`/`FRD-208` | **Done (2026-08-08).** The use-case list is server-paged and explicitly ordered; findings are **cursor**-paged because they are an append-only log; the model catalog stays client-side on purpose, because two console warnings count over the *whole* catalog. |
| ~~Read-model tombstones~~ | — | **Done (2026-08-05).** Deleting a use case left its keys authenticating and its budgets, limits and pipeline in force; the tombstone now cascades and migration 0011 cleared what earlier deletions had left. |

---

## Phase → FRD index (summary)

Every FRD, grouped by the phase its own header names (`tools/features_index.py` reads the same
header); a feature's status is in the [generated index](features/README.md).

| Phase | Theme | FRDs |
|------:|-------|------|
| 0 | Foundation & Infra, and the demo | 000–002, 130 |
| 1 | Gateway MVP; platform; observability | 100–105, 134, 139, 140, 615, 617–621 |
| 2 | Management Foundation | 200–209, 605 |
| 3 | Routing/Pipeline/Fallback | 125, 300, 302–304, 306–309, 506 |
| 4 | Budgets & Quotas | 400–406 |
| 5 | Anomaly & IT Security | 500–505, 604 |
| 6 | Governance & Analytics | 507, 601, 603, 606–613, 622, 625, 626 |
| 7 | Hardening & Prod | 127, 136–138, 614, 616, 623 |
| 8 | KIRA parity | 107, 110–124, 126, 128, 135, 602, 624 |
| 9 | Agents & coding assistants | 131–133 |

Numbers that were used and have no document: `FRD-106` (the OpenAI-compatible surface, withdrawn),
`FRD-129` (a label for a live test round), `FRD-301` (routing is in `FRD-300`), `FRD-600` (the
governance view is `FRD-608`) and `FRD-700`–`702` (performance is `FRD-136`, availability
`FRD-127`). Before `FRD-127` was given to several gateway instances, it labelled the fix for a
cancelled KIRA stream.

> Phases are sequential in dependency but the roadmap allows overlap where safe (e.g. Angular shell
> work in Phase 2 can begin while Phase 1 stabilizes).
