# AIRA Gateway — Project Requirements Document (PRD)

> **AIRA** = **AI R**est **A**PI Gateway
> Status: **Draft v0.1** · Owner: Vadim Scheibe · Last updated: 2026-10-14
>
> §1.1 ist der Maßstab, an dem geplant wird — eine veraltete Zeile darin ist teurer als eine
> veraltete Zeile irgendwo sonst. Am 2026-08-09 meldeten vier Zeilen „fehlt" für Dinge, die seit
> Tagen liefen (6, 16, 20) oder zur Hälfte (7).

---

## 1. Purpose & Vision

AIRA Gateway is an **AI gateway** that provides a single, unified,
provider-agnostic REST API in front of multiple upstream LLM platforms. It centralizes
**authentication, authorization, routing, governance, observability, and security** for all
AI traffic in the organization.

The core idea: every AI request in the company flows through AIRA. AIRA knows **who** is asking
(user / ~~project~~ / use case — a project is a non-goal, §2.2), **what** they are asking, **which
model** should serve it (routing, fallback, cost optimization), **whether it is allowed** (policy,
prompt-injection, budget), and records **every request attributed to a use case** (request +
response persistence, tracing, audit) so that operations and IT security have full visibility and
control. A request refused before it is attributed is recorded on its trace span, not in the audit
table (§5.1, FR-GW-7).

### Vision statement
> A governed, observable, and secure single point of entry for all AI usage in the organization — with
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

### 1.1 The central features (owner's definition, 2026-08-06)

Stated by the owner as what AIRA Gateway *is*. This table is the reference the rest of the document
serves, and the **Stand** column is deliberately honest — a feature named here and not built is a
gap, not an aspiration.

| # | Feature | Stand | Where |
|--:|---|---|---|
| 1 | Einheitliche Bereitstellung von Modellen | **weitgehend** — Gemini **und** Anthropic über Vertex in der EU (`FRD-115`/`119` done), die offenen Modelle der Agent Platform (GLM, DeepSeek) über denselben Zugang — nur an `global` angeboten und deshalb je Modell namentlich freigegeben (`FRD-115` FR-2a, `ADR-0034`), Google AI Studio (`FRD-304` done), OpenAI-Dialekt/selbst gehostet (`FRD-123` done), Microsoft Foundry **gebaut, aber nur hermetisch** — es gab nie ein Azure-Abonnement, gegen das es gelaufen wäre. **Nicht gebaut:** selbst bereitgestellte Model-Garden-Endpunkte (Nemotron u. Ä., `FRD-115` FR-2a) | `FRD-100` done, `FRD-120` done (unbelegt gegen echtes Azure) |
| 2 | Rollenzuweisung | **fertig** — seit `ADR-0017` (2026-08-09) ist **Gruppenmitgliedschaft die einzige Quelle einer Rolle**: die drei Organisationsrollen kommen aus konfigurierten Keycloak-Gruppen, eine direkt zugewiesene Realm-Rolle bewirkt nichts, und `use-case-admin`/`use-case-user` existieren nicht mehr als Rollen (Grant je Use Case) | `FRD-201`, `ADR-0009`, `ADR-0017`, `FRD-605` |
| 3 | Kompatibilität mit der KIRA-API | **fertig (2026-08-06)** — vollständiger Vertrag: Text, Dokumente, Thinking, strukturierte Ausgabe, Batch-Embedding. Was ein *Modell* nicht kann, wird weiterhin abgewiesen, nie ignoriert | `FRD-107` done `FRD-111`–`113` done |
| 4 | Auditierbarkeit | **teilweise** — Anfragen **fertig (2026-08-06)**: alle fünf Lücken geschlossen; jede einem Use Case zugeordnete Anfrage hinterlässt eine Zeile, bedient oder abgewiesen, auch wenn der Aufrufer vorher auflegt. **Konfigurationsänderungen sind nicht auditiert:** nur Rollenänderungen haben eine Historie (FR-MG-10) | `FRD-122` done, `FRD-128` done |
| 5 | Speicherung von Requests/Responses: *welches System wann was womit* | **fertig (2026-08-06)** — das aufrufende System ist über den Key-Prefix unterscheidbar; eine Ablehnung nach der Zuordnung zu einem Use Case erzeugt eine Zeile. Eine Ablehnung davor (401, 403, 400 ohne Use Case) schreibt keine — ihr Beleg ist der Trace-Span, den der Weiterleitungskanal an ein SIEM gibt (`FRD-618`) | `FRD-103` done, `FRD-122` done |
| 6 | Incident Response | **fertig (2026-08-07)** — eine Sperre ist eine *geschriebene Entscheidung*: Ziel, Aktion, Ablauf, **Autor**, **Grund**; am einen Pre-Dispatch-Gate gelesen, nach dem Aufheben aufbewahrt (429, eigener Audit-Ausgang `suspended`, **nicht über Kafka**) | `FRD-503` done |
| 7 | Blockierung gefährlicher Anfragen | **teilweise** — Prompt-Injection-Filter done, PII-Ersetzung im Prompt done (`pii_filter`, je Use Case zuschaltbar, LLM-gestützt), Betriebs-Kill-Switch done (`FRD-503`), und mit `FRD-504` lässt sich **messen**, was ein Modell abweist. Als *Kontrollen* fehlen weiterhin: Jailbreak über die Injection hinaus, Exfiltration, Ausgabefilter. Kein Filter liest Anhänge (`FRD-110`) oder Tool-Ergebnisse (`FRD-131`) | `FRD-300` done, `FRD-309` done, `FRD-503` done, `FRD-504` done |
| 8 | Model Routing anhand der Definition | **fertig** | `FRD-300`, `FRD-306` |
| 9 | Modell-Fallback | **fertig** — capability-homogen, und seit 2026-10-11 greift die Kette bei **jedem** Modellzugriff: auch bei einer gestreamten Antwort, solange den Aufrufer noch nichts erreicht hat, und je Pipeline-Schritt für das Modell, das er fragt. Offen: kein Fallback *nach* dem ersten Chunk, kein Wiederholen desselben Modells | `FRD-302` done, `ADR-0012` §3, `ADR-0032` |
| 10 | Unabhängigkeit von Google / Microsoft | **belegt für drei Plattformen** — die Architektur-Assertion ist ein Test: kein Code oberhalb der Adapter kennt den Vendor, und `FoundryTransport` hat den OpenAI-Dialekt **ohne eine Zeile Änderung** wiederverwendet. Gegen ein echtes Azure-Abonnement weiterhin unbelegt | `ADR-0011` done, `FRD-115`/`119` done, `FRD-120` done |
| 11 | Übersicht über alle Use Cases | **fertig** — Liste done, und das Register: je Use Case eine Zeile mit Zweck, Verarbeitungshinweisen, Modellen und wo sie liefen, Speicherung, Aufbewahrung und wer Inhalte lesen darf — sortierbar, als CSV | `FRD-202` done, `FRD-608` done |
| 12 | Self-Service: Filter- und Routing-Pipeline | **fertig** | `FRD-303`, `FRD-306` |
| 13 | Zugelassene Modelle je Use Case | **done** (2026-08-11) — zwei Tore mit zwei Verantwortlichen: ein Global Administrator gibt ein Modell für die *Installation* frei (`FRD-307`), ein Administrator *dieses* Use Case wählt daraus aus (`FRD-308`). Beide sind Dispatch-Bedingungen an **jedem Hop**, also kommen weder `model_route` noch die Fallback-Kette daran vorbei. Leer heißt **keins**. Die Auswahl erfolgt in der Konsole gegen den Katalog statt als Freitext; der `allow_check`-Schritt, der nur den vom Aufrufer genannten Namen prüfte, ist entfallen | `FRD-307` done, `FRD-308` done, `FRD-114` done |
| 14 | IT-Security-Unterstützung: Modell-Smoke-Tests und Jailbreak-Versuche | **fertig (2026-08-09), schmaler als entworfen** — ein flacher Katalog aus 100 Fragen, von einem Menschen beurteilt; ein Stand ist der **letzte Lauf**, nie eine Summe über alle. Seit `ADR-0020` wird ein Lauf an die **Pipeline eines Use Case** gestellt, nicht an ein Modell: gegen eine filternde Pipeline misst er den Filter, gegen eine leere das Modell — die zwei Modi des Entwurfs sind ein Mechanismus. Nicht gebaut: Wiederholungen als Rate, maschinelle Erwartungen | `FRD-504` done, `ADR-0020` |
| 15 | Budgetübersicht und Budgetgrenze | **fertig** — seit `FRD-603` (2026-08-09) zeigt ein Use Case seinen Verbrauch (Monat und Tag) **auch ohne gesetztes Budget**; die Zahlen lagen immer im Request-Log und wurden nur als Anteil eines Limits angezeigt | `FRD-400`–`403`, `FRD-601`, `FRD-603` |
| 16 | Anomalieerkennung | **fertig (2026-08-07/08)** — sieben Regelarten in geschlossenem Vokabular, ausgewertet gegen das Request-Log (auch Ablehnungen nach der Zuordnung), `alert` als Standard, IT-Security-Konsole + Warnungen je Use Case. Eine Regel für abfließende Daten gibt es nicht (FR-MG-6) | `FRD-500`/`501`/`502` done |
| 17 | Zentrale Übersicht über alle Use Cases | **fertig** — siehe 11 | `FRD-608` done, `FRD-601` done |

### 1.2 Additional features from the code review (2026-08-06)

Not in the list above, found by reading the code against it. Recorded here at the owner's request
so they are not forgotten — several are small, and one of them is the difference between the
product's central claim being true and being asserted.

| # | Feature | Stand | Where |
|--:|---|---|---|
| 18 | **Verarbeitung von Dokumenten** (PDF, Bilder u. a. im Request) — KIRAs Kernfall | **fertig (2026-08-06)** — geordnete Teile, 15 Medientypen, Signaturprüfung, Grenzen; ein Modell, das den Typ nicht lesen kann, **lehnt ab** statt zu halluzinieren | `FRD-110` done |
| 19 | **Erweiterbarkeit als messbare Eigenschaft** — eine neue Modellfamilie ist ein Katalogeintrag plus höchstens ein Dialekt | **viermal belegt** — Vertex/Gemini, Vertex/Anthropic, OpenAI-Dialekt; Foundry hat den Dialekt **unverändert** wiederverwendet und der Diff verließ `upstreams/` nicht, und die offenen Modelle auf Vertex haben ihn auf einem zweiten Transport wiederverwendet. Die Assertion ist ein Test, kein Vorsatz | `ADR-0011`, `FRD-115` §10, `FRD-120` |
| 20 | **Secrets aus Vault** — Richtlinie und Implementierung stehen seit Phase 0 auseinander | **fertig (2026-08-06)** — AppRole + KV-v2 als pydantic-Settings-Quelle über der Umgebung, **fail closed**; gegen eine echte AppRole im Stack verifiziert | `FRD-116` done |
| 21 | **Betriebsdiagnostik** — Build-Identität, Upstream-Health, Trace-Header, CORS, OpenAPI 3.0 | **fertig bis auf FR-7 (2026-08-06)** — Erreichbarkeit wird im Hintergrund geprüft und von `/readyz` *gelesen*, veraltet zählt als degradiert, `x-trace-id` als reines ASGI ganz außen; **das zweite OpenAPI-3.0-Dokument ist bewusst nicht gebaut** | `FRD-117` done (FR-7 offen) |
| 22 | **Maskierung sensibler Inhalte** in gespeicherten Payloads | **Credentials fertig (2026-08-08)** — API-Keys, Bearer-Token, JWTs, `Authorization:`, PEM-Blöcke, plus Deployment-Muster (additiv). **PII bewusst nicht**: Namen und Kundennummern sind der Grund, *warum* gespeichert wird — die PII-Hälfte ist abgelehnt (`ADR-0016`): gespeicherte Inhalte sind nach Rolle lesbar, und jede Lesung wird protokolliert. Wer personenbezogene Daten weder senden noch speichern will, ersetzt sie per `pii_filter` (`FRD-309`) oder schaltet die Speicherung je Use Case ab (`FRD-404`) | `FRD-406` done (PII abgelehnt), `FRD-309` done |
| 23 | **Export der Auswertung** (CSV mit Content Negotiation) | **fertig (2026-08-06)** — CSV als *Renderer* desselben Endpunkts, per `Accept` gewählt; BOM, CRLF, RFC 4180, unpreisliche Zeile als Nachsatz | `FRD-602` done |
| 24 | Mehrere Keycloak-Backends / Gruppen aus UserInfo | **teilweise** — das Gateway akzeptiert mehrere Issuer, jeder mit eigenem Schlüsselsatz und eigener Audience, gewählt am `iss` des Tokens (`AIRA_OIDC_ISSUERS`); Management prüft Tokens eines einzigen Issuers. **Gruppen aus UserInfo bleiben abgelehnt** (FR-3) | `FRD-118` FR-1 done |

Feature 19 deserves the emphasis the owner put on it (*"so dass es einfach erweiterbar wäre"*).
Extensibility is a claim until something checks it, so it has a test rather than an intention:
**adding a model family must not change anything above `upstreams/`** (`FRD-115` §10). If a diff
does, the canonical core is vendor-shaped and the core is what gets fixed — not the adapter.

### 1.3 Priority (owner, 2026-08-06)

1. **Kompatibilität mit der KIRA-API**
2. **Anbindung der Google- und Microsoft-Modelle** (Gemini, Anthropic, Azure OpenAI, Microsoft
   eigene) — *einfach erweiterbar*
3. **Verarbeitung von Dokumenten**
4. The findings from the review (features 4, 5, 6, 14, 16 and 18–24)

One conflict, stated rather than smoothed over: **priority 1 depends on priority 3.** `FRD-107` §5.2
is explicit that a KIRA surface built before the capabilities exist would accept fields it silently
ignores — a caller could not tell that their document or their thinking budget was dropped, which is
worse than a refusal. The delivery order in `docs/ROADMAP.md` Phase 8 resolves it: the capabilities
first, and a **staged** KIRA surface whose first stage ships early and **refuses** what it cannot yet
honour instead of ignoring it. Simple clients then migrate months before the complex ones, and
nobody is misled in the meantime.

Three observations the table makes visible and prose would hide:

- **Governance and evidence are both built; what is open sits at the edge of the evidence.**
  Budgets, limits, routing, self-service and roles work, and so do the audit trail, incident
  response and anomaly detection — the three that make a governed system *defensible after the
  fact*. What is missing is a record of who changed the configuration (FR-MG-10), the audit rows as
  a stream a SIEM can take (`FRD-616`), and anybody being told about a finding or a budget running
  out. [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md) keeps the list.
- **Feature 5 is more specific than "store requests".** *Which system* called is the credential:
  every audit row carries the API key's prefix (or the OIDC client) in `credential`, apart from the
  person, so five keys of one use case are five identities in the log — the shape a leaked
  credential needs.
- **Feature 3 settles `ADR-0010`.** Naming KIRA compatibility as a central feature is the decision
  that ADR was waiting for.

---

## 2. Goals & Non-Goals

### 2.1 Goals
- **G1 — Unified API surface**: A single provider-agnostic REST interface for all clients. The
  **Gemini-compatible** surface ships **first** (existing projects already run on it);
  ~~an **OpenAI-compatible** surface is added later~~ — **withdrawn** (`FRD-106`, 2026-08-07):
  `FRD-132` measured a coding assistant on the Gemini surface and no new surface was needed. A
  **KIRA-compatible** surface (`FRD-107`) serves the predecessor's clients. All surfaces normalize
  to one canonical internal schema.
- **G2 — Strong AuthN/AuthZ**: SSO via bearer token (OIDC/Keycloak) *and* self-generated API keys.
- **G3 — Attribution**: Every request is attributed to a user, ~~a project,~~ a use case, or a
  user-within-a-use-case. *A project is a non-goal (§2.2).*
- **G4 — Intelligent routing**: Model rerouting (route "easy" requests to cheaper models),
  sequential ~~/parallel~~ model execution, and model fallback. *Parallel execution is a non-goal
  (§2.2).*
- **G5 — Full persistence**: Store requests and responses (store as much as possible), with tracing
  and source-IP capture.
- **G6 — Self-service governance**: Use-case owners configure their own pre-dispatch pipeline,
  budgets, and anomaly rules through the management UI.
- **G7 — Security & incident response**: IT Security gets cross-use-case anomaly visibility, can
  mark/block requests, and configure incident response (throttle / alert / block).
- **G8 — Governance oversight**: IT Steuerung (IT Governance) sees all use cases, their processing
  logic, and aggregate statistics.
- **G9 — Production quality**: Full unit-test coverage, strong observability, secrets in Vault,
  event-driven integration via Kafka.
- **G10 — Demo mode**: The whole system can be run and demonstrated without real upstream
  credentials, with automatically seeded test data.

### 2.2 Non-Goals (for now)
- Not building our own LLM inference — AIRA orchestrates upstream providers only.
- Not a general-purpose API management product — scope is AI/LLM traffic.
- No fine-tuning / training pipelines.
- Multi-region active-active HA is out of scope for the initial phases (single-cluster first).
- **No OpenAI-compatible inbound surface** (`FRD-106`, withdrawn 2026-08-07). The OpenAI wire
  dialect is used as an *upstream* (`ADR-0011`).
- **No parallel pipeline execution** (G4, FR-GW-5, FR-MG-3). The engine is an ordered walk
  (`FRD-300`): a step can rewrite what the next one reads, so the order is part of what a pipeline
  means.
- **No "project"** (G3, FR-GW-3, §7). The use case is the unit of attribution, grants, budgets and
  reporting.

---

## 3. Stakeholders & Roles

Five initial roles. Since `ADR-0017` the last two are **grants on one use case**, not roles. Since
`ADR-0025` the first three are built-in **roles** — sets of permissions, held through a Keycloak
group — and an installation may define further roles in the console, each bound to one checked
group (`FRD-614`). The Global Administrator and IT Security are fixed; IT Steuerung's permissions
may be changed. [`ROLES.md`](ROLES.md) is the current model.

| Role | Description | Key capabilities |
|------|-------------|------------------|
| **Global Administrator** | Full system owner | Manage everything: users, use cases, global config, providers, system settings. Administering every use case is a Management permission (`usecase.manage_all`); calling a use case through the gateway still takes a grant on it (`ADR-0007`) |
| **IT Security** | Security oversight (restricted view) | Cross-use-case anomaly visibility, mark/block requests, define incident response. ~~**Cannot** see all business content by default — scoped visibility~~ — since `ADR-0016` it reads stored content where a use case keeps it, and every read is recorded (`FRD-505`, `FRD-622`) |
| **IT Steuerung (IT Governance)** | Governance & oversight | Read-only overview of all use cases, their descriptions & processing logic, aggregate statistics |
| **Use Case Administrator** | Owns a specific use case | Configure pre-dispatch pipeline, model routing/fallback, budgets, anomaly rules for *their* use case; manage members |
| **Use Case User** | Member of a use case | Consume the gateway within the use cases they are assigned to |

> **Least-privilege principle**: roles are additive and object-scoped. A Use Case User only sees
> their assigned use case(s); IT Security sees security-relevant metadata across use cases. Stored
> content is readable to a role that holds `payload.read_any` — IT Security's fixed set does — and
> every read is recorded; redaction masks credentials only (`FRD-406`, `ADR-0016`).

---

## 4. System Overview

AIRA consists of **two self-developed components** plus **open-source infrastructure**.

```
   Clients (SDKs, apps, agents, coding assistants)
   Gemini-compatible · KIRA-compatible
                   │
                   ▼
   ┌────────────────────────────────────────────────┐
   │  Component 1: Gateway API (FastAPI)            │ ───▶ Upstreams:
   │   • Unified API  • AuthN/Z  • Attribution      │      Vertex AI: Gemini, Anthropic,
   │   • Pipeline  • Routing/Fallback               │        managed open models
   │   • Request/Response persistence  • Tracing    │      Google AI Studio · Foundry
   │   • Anomaly detection  • Suspensions           │      OpenAI-compatible · Mock
   └────────────────────────────────────────────────┘
        ▲                           ▲
        │ Kafka: configuration      │ HTTP (/gw): reporting, requests,
        │ (outbox → read-model)     │ findings, suspensions
   ┌────┴───────────────────────────┴───────────────┐
   │  Component 2: Management & Monitoring          │
   │  Angular SPA  +  Django REST Framework         │
   │   • Use-case self-service  • Pipeline builder  │
   │   • Budgets  • Anomaly rules  • Model catalog  │
   │   • IT Security console  • IT Governance views │
   └────────────────────────────────────────────────┘

   Shared infrastructure: PostgreSQL · Redis · Keycloak (SSO) · HashiCorp Vault · Kafka
```

### 4.1 Component 1 — Gateway API (FastAPI)
The data-plane. High-throughput, stateless-where-possible service that:
- Exposes a **unified, Gemini-compatible** REST API first, and a **KIRA-compatible** surface
  (`FRD-107`). ~~OpenAI-compatible surface added later~~ — withdrawn (`FRD-106`).
- Authenticates via **bearer token (OIDC)** or **API key**.
- **Classifies & attributes** each request to user / ~~project~~ / use case.
- Applies the **use-case pre-dispatch pipeline** (injection filter, PII replacement, routing). Which
  models a use case may call is not a step: the installation's approval and the use case's release
  are dispatch conditions at every hop (`FRD-307`, `FRD-308`), and the allow-check step is gone.
- **Routes** to the appropriate model/upstream, with rerouting (cost-based) and **fallback**.
- **Persists** the full request and response of every request attributed to a use case, where the
  use case stores payloads (`FRD-404`).
- Emits **tracing** spans and captures **source IP**.
- Detects **anomalies** against its own audit trail and enforces **suspensions** (`FRD-501`,
  `FRD-503`).
- ~~Publishes events to **Kafka** (usage, anomalies, audit)~~ — **not built** (FR-GW-12). The audit
  trail, findings and suspensions stay in the gateway's Postgres and are served over its HTTP API;
  a stream of them is `FRD-616`, not built.

### 4.2 Component 2 — Management & Monitoring (Angular + DRF)
The control-plane. Human-facing UI + config API that:
- Provides **use-case self-service** (create use case, assign members).
- Lets Use Case Admins build the **pre-dispatch pipeline** (ordered steps, fallback; parallel
  steps are a non-goal, §2.2).
- Manages **budgets** per use case / per member.
- Lets users define **anomaly rules** (rate, size; ~~data-leak detection~~ — not built, FR-MG-6).
- Gives **IT Security** a cross-use-case console (anomalies, mark/block, incident response).
- Gives **IT Steuerung** oversight dashboards and aggregate statistics.
- Owns configuration; pushes it to the Gateway via **Kafka** into the gateway's read-model.
- Reads the gateway's HTTP API for everything the gateway records — reporting, requests, findings,
  suspensions — through `/gw`.

### 4.3 Inter-component communication
Configuration travels over **Kafka**; what the gateway records is read from it over **HTTP**.
- Management → Gateway: configuration changes — use cases, grants, API keys, pipelines, budgets,
  rate limits, models, anomaly rules, roles — written to a transactional outbox, published by a
  relay, and applied by the gateway's consumer to its read-model. An event is JSON named by an
  `event_type` header: ~~versioned events~~ — there is no version field and no schema registry in
  use; cross-plane tests in `tools/tests` keep the two planes in step.
- Gateway → Management: ~~usage events, request/response metadata, anomaly signals, audit
  events~~ — **not over Kafka** (FR-GW-12, not built). The console reads the gateway's HTTP API
  instead. The kill switch writes straight to the gateway, so the incident path does not depend on
  the bus.

Each plane's Postgres is its system of record; Kafka carries configuration. (Topic design and the
config-distribution mechanism: `FRD-204` and [`ARCHITECTURE.md`](ARCHITECTURE.md).)

---

## 5. Functional Requirements

### 5.1 Gateway API (Component 1)

- **FR-GW-1 Unified interface**: Provide a **Gemini-compatible** surface **first** (Google
  Generative Language API v1beta: `POST /v1beta/models/{model}:generateContent`,
  `:streamGenerateContent`, `:embedContent`, `GET /v1beta/models`). ~~An **OpenAI-compatible**
  surface (`/v1/chat/completions`, `/v1/embeddings`, `/v1/models`) is added later (FRD-106).~~
  Every surface normalizes to one internal canonical schema, then translates to the target
  upstream's dialect. See ADR-0005. *Withdrawn:* the OpenAI-compatible surface (`FRD-106`, 2026-08-07) —
  `FRD-132` measured a coding assistant on the Gemini surface, and no new surface was needed. A
  KIRA-compatible surface (`FRD-107`) was added for the predecessor's clients.
- **FR-GW-2 Authentication**: Accept (a) OIDC bearer tokens validated against Keycloak, and
  (b) self-generated API keys (hashed at rest, prefix-identifiable, revocable, scoped). Per ADR-0006,
  API keys are **issued in Management** (self-service, show-once) and distributed to the Gateway via
  Kafka into a local read-model; the **Gateway only validates**. OIDC validation lives in the Gateway.
- **FR-GW-3 Attribution**: Resolve each request to `user`, ~~`project`~~, `use_case`, and
  `user∈use_case`. Attribution derives from the credential + request context. *Non-goal:* a
  project (§2.2).
- **FR-GW-4 Routing & rerouting**: Route requests to a model/upstream based on use-case config;
  support **cost-based rerouting** (classify request difficulty → cheaper model when possible).
- **FR-GW-5 Sequential/parallel execution**: Execute pipeline steps and/or multiple models
  sequentially ~~or in parallel~~ per use-case configuration. *Non-goal:* parallel execution
  (§2.2) — the engine is an ordered walk (`FRD-300`).
- **FR-GW-6 Fallback**: On upstream error / timeout / policy failure, fall back to a configured
  alternative model/upstream.
- **FR-GW-7 Persistence**: Persist request and response payloads and metadata ("store as much as
  possible"), with configurable retention and redaction hooks. *As built:* every request
  attributed to a use case leaves a row, served or refused (`FRD-122`, `FRD-128`). A request
  refused before attribution — no or an invalid credential, no use case named, a use case the
  caller may not use — writes none: its trace span is its record, and the delivery channel takes
  it to a SIEM (`FRD-618`).
- **FR-GW-8 Tracing**: Distributed tracing (OpenTelemetry) across gateway → pipeline → upstream.
- **FR-GW-9 Source IP capture**: Record originating IP (respecting proxy headers / trusted hops).
- **FR-GW-10 Pipeline enforcement**: Execute the use-case's pre-dispatch pipeline (injection
  filter, allow/deny checks, routing) before dispatch; honor throttle/alert/block decisions.
  *As built:* the steps are an injection filter, PII replacement (`FRD-309`) and routing; allow and
  deny are the installation's approval and the use case's release, checked at every hop rather than
  once as a step (`FRD-307`, `FRD-308`).
- **FR-GW-11 Budget enforcement**: Reject/limit requests that exceed use-case or per-member budgets.
- **FR-GW-12 Event emission**: Publish usage/audit/anomaly events to Kafka. *Not built:* the
  gateway publishes nothing; the console reads its HTTP API, and an audit stream is `FRD-616`.
- **FR-GW-13 Demo/mock upstream**: A built-in mock provider so the gateway works end-to-end without
  real credentials.

### 5.2 Management & Monitoring (Component 2)

- **FR-MG-1 Use-case self-service**: Create/edit use cases (name, description, processing logic,
  members). Assigned members automatically gain access to that use case.
- **FR-MG-2 Membership & roles**: Assign users to use cases and to roles; object-scoped access.
- **FR-MG-3 Pipeline builder**: Visually configure the pre-dispatch pipeline — ordered steps,
  **sequential** ~~**or parallel**~~ branches, step types (e.g. LLM prompt-injection filter,
  ~~allow-check,~~ routing), and **model fallback** chains. *As built:* injection filter, PII
  replacement and routing; parallel branches are a non-goal (§2.2), and the allow-check became the
  per-use-case release (`FRD-308`).
- **FR-MG-4 Model routing config**: Configure routing rules and cost-based rerouting per use case.
- **FR-MG-5 Budget control**: Set budgets per use case and per member; view consumption.
- **FR-MG-6 Anomaly self-service**: Define anomaly rules, e.g. too many requests per time window,
  oversized requests, requests that leak internal information. *Not built:* a rule for leaked
  information — the seven kinds watch rates, spend, sizes and addresses (`FRD-500`).
- **FR-MG-7 IT Security console**: Cross-use-case anomaly overview; **mark** or **auto-block**
  requests; define **incident response** (throttle / alert / block) per rule.
- **FR-MG-8 IT Steuerung views**: Read-only overview of all use cases (descriptions + processing
  logic) and aggregate statistics.
- **FR-MG-9 Observability dashboards**: Logging, tracing, monitoring surfaced per use case and
  globally.
- **FR-MG-10 Audit trail**: All configuration and security actions are audited. *Not met:* only
  role changes have a history (who, when, before and after). API keys, group grants, retiring a use
  case and suspensions name their author; model approvals and prices, budgets, rate limits,
  pipelines, anomaly rules and use-case settings record neither author nor history.
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
  decoupling; Postgres as system of record. *Partly met* (`FRD-127`): no request depends on state
  one process holds and the counters are shared in Redis, but Compose cannot run a second gateway
  and nothing enforces the migration rule a rolling update needs.
- **NFR-5 Performance**: Gateway adds low overhead to the request path; persistence and event
  emission are async / non-blocking on the hot path.
- **NFR-6 Reliability**: Graceful degradation; fallback on upstream failure; at-least-once event
  delivery with idempotent consumers.
- **NFR-7 Auditability & compliance**: Immutable audit log; data retention & redaction controls;
  role-scoped data access for compliance. *Not met* for the audit log: configuration changes have
  no history except roles (FR-MG-10), and nothing makes `request_logs` immutable — retention
  clears payloads after the use case's period and, when `AIRA_LOG_RETENTION_DAYS` is set, deletes
  whole rows. Retention, redaction and role-scoped access are built.
- **NFR-8 Maintainability**: Clear module boundaries, typed code (Python type hints, TypeScript),
  linting/formatting, documented APIs (OpenAPI). *Partly met:* Management's API has no OpenAPI
  document, and the gateway serves its own only locally or in a demo.
- **NFR-9 Portability**: Runs locally via Docker Compose; designed to migrate to Kubernetes/Helm.
- **NFR-10 Configurability**: Behavior driven by use-case config, not code changes.

---

## 7. Data Model (high-level)

Core entities as built (details in FRDs). Management holds the configuration; the gateway holds a
read-model of it, and everything it records.
- **User**, bound to the token's subject (`OidcIdentity`, `ADR-0007`), and **UseCase**, reached
  through grants: **UseCaseMembership** (person × use case × `user`/`admin`) and
  **UseCaseGroupGrant** (group × use case × `user`/`admin`) — `FRD-209`
- **ApiKey** (SHA-256 hash, prefix, owner, bound to one use case, revocable, expiring)
- **Model** — the catalog: approval, prices, capabilities, provider (`FRD-114`, `FRD-307`,
  `FRD-403`); a use case's **release** is the set of models it may call (`FRD-308`)
- **PipelineConfig** — a use case's ordered steps and its fallback chain; routing is a step
  (`model_route`), and each step may carry a chain of its own (`FRD-302`)
- **Budget** (per use case, per member, or the installation's own) and **RateLimit**; the gateway
  keeps running usage per scope and period in `budget_usage`, with reservations in Redis
  (`FRD-401`, `FRD-405`)
- **request_logs** — one row per request attributed to a use case: who and which credential, the
  model asked for and the one that served, outcome, pipeline decisions, usage and cost, source IP,
  trace ID, and the request and response payloads
- **AnomalyRule** (kind, threshold, action) and **AnomalyEvent** (a finding); a **suspension**
  (`access_suspensions`) is the incident response that a rule or a person writes
- **payload_access** — every read of stored content (`FRD-505`)
- **StoredRole** and **RoleChange** — a role's permissions and group, and their history

Named here before and not built: ~~**Project**~~ (a non-goal, §2.2), ~~**AuditEvent**~~ (only roles
have a change history, FR-MG-10) and ~~**IncidentResponsePolicy**~~ (the response is the rule's
action). **RoutingRule**, **FallbackChain**, **UsageRecord** and **ResponseLog** are parts of the
entities above rather than entities of their own.

---

## 8. Integrations

| System | Purpose | Notes |
|--------|---------|-------|
| **Keycloak** | SSO / OIDC | Bearer-token auth for Gateway; SSO for management UI; role source. The gateway accepts several issuers (`FRD-118`). Active Directory is reached **through** Keycloak — LDAP federation, and Windows sign-on by Kerberos in the browser (`FRD-140`, `ADR-0035`) |
| **PostgreSQL** | System of record | Config + logs + usage; separate schemas/DBs per component |
| **Redis** | Shared counters | Rate limits and budget reservations, shared by every gateway instance (`ADR-0008`); without it rate limits are counted per instance and budgets are racy — documented degradations |
| **Kafka** | Event bus | Config distribution, Management → Gateway. ~~usage/audit/anomaly events between components~~ — not built (FR-GW-12) |
| **HashiCorp Vault** | Secrets | Upstream credentials, signing keys, DB creds |
| **Gemini Enterprise Agent Platform** | Upstream | Gemini + Anthropic models in EU regions, and managed open models (GLM, DeepSeek) at `global`, permitted by name (`ADR-0034`). Self-deployed endpoints are not built (`FRD-115` FR-2a) |
| **Google AI Studio** | Upstream | Gemini over the Generative Language API with an API key; registered only where `global` is permitted (`FRD-304`) |
| **Microsoft Foundry** | Upstream | OpenAI + Microsoft models; built and tested hermetically, never run against Azure (`FRD-120`) |
| **OpenAI-compatible servers** | Upstream | Any server speaking the OpenAI chat API, each under its own name — Ollama in the local stack (`FRD-123`) |
| **Mock provider** | Demo | Built-in, no external calls |
| **OpenTelemetry Collector** | Telemetry ingestion | OTLP receiver; both components export traces/metrics/logs here |
| **Grafana otel-lgtm** | Observability backend | Local single-container Grafana + Loki + Tempo + Prometheus; OTLP-native (see ADR-0004; supersedes SigNoz) |
| **SIEM or any OTLP receiver** | Delivery channel | The collector forwards a span per request and per model call, and the logs and metrics (`FRD-617`–`621`; Splunk HEC and Azure Monitor as recipes). The audit rows stay in the gateway's database (`FRD-616`) |

---

## 9. Security & Compliance

- OIDC + API-key authentication; API keys hashed (SHA-256, looked up by prefix).
- RBAC with object-level scoping; IT Security has security-metadata visibility ~~with configurable
  payload redaction~~. *As built:* it reads stored content, and every read is recorded
  (`ADR-0016`); redaction masks credentials only (`FRD-406`).
- All secrets in Vault; short-lived credentials where possible.
- Prompt-injection filtering and ~~data-leak detection~~ as pipeline steps. *As built:* injection
  filtering and PII replacement (`FRD-300`, `FRD-309`); data-leak detection is not built (FR-MG-6).
- Full audit trail; ~~immutable,~~ queryable by IT Security / Governance. *As built:* it covers every
  request attributed to a use case; nothing makes it immutable, and configuration changes are not
  in it (NFR-7, FR-MG-10).
- Configurable data retention & redaction for stored requests/responses (PII handling).

---

## 10. Observability & Operations

- **Telemetry pipeline**: both components export via **OTLP** to an **OpenTelemetry Collector**,
  which forwards to a local **Grafana `otel-lgtm`** backend (traces + metrics + logs unified). This
  is the local observability target on developer hardware from Phase 0 (see ADR-0004).
- **Logging**: structured JSON logs with correlation/trace IDs (also shipped via OTLP).
- **Tracing**: OpenTelemetry spans across gateway, pipeline steps, upstreams; source IP recorded.
- **Monitoring**: metrics (latency, throughput, error rate, cost, budget consumption, anomalies).
  *As built:* latency, throughput and errors are metrics from the OpenTelemetry instrumentation;
  cost, budget consumption and anomalies are figures in the console.
- **Dashboards**: per-use-case and global (Grafana); security-specific views for IT Security.
  *As built:* one Grafana dashboard (`deploy/compose/grafana/dashboards/aira-overview.json`), read
  from traces; per use case and globally, the console — requests, consumption, usage over time,
  the register — and IT Security's views there (`FRD-502`).
- **Alerting**: anomaly-driven alerts feeding incident response. *As built:* a rule's action is
  alert, throttle or block (`FRD-503`), and an alert is a finding in the console — nothing sends
  mail or calls a webhook.
- **Vendor portability**: apps emit vendor-neutral **OTLP** and only talk to the Collector, so the
  backend is swappable without app changes — a production move to **Dynatrace, Grafana Cloud,
  Datadog, Honeycomb, New Relic, Elastic**, etc. is a Collector-exporter change (and can fan out to
  several backends at once). See ADR-0004.

---

## 11. Demo Mode

- Built-in **mock upstream** provider (deterministic, no external credentials).
- **Automated seeding** command loads: demo users mapped to all roles, ~~projects,~~ use cases with
  pipelines/budgets/anomaly rules, and sample request/response traffic + anomalies — so every
  feature is demonstrable after one command, `make showcase`
  ([`deployment/showcase.md`](deployment/showcase.md)).

---

## 12. Testing Strategy

- **Unit tests** for all business logic (Gateway + DRF backend + Angular), enforced coverage
  thresholds in CI.
- **Contract tests** for the Kafka event schemas and the unified API. *As built:* there are no
  event schemas; cross-plane tests in `tools/tests` check that both planes read one vocabulary.
- **Integration tests** with ephemeral Postgres/Kafka/Keycloak/Vault (Compose/~~testcontainers~~).
  *As built:* against the running Compose stack (`tests/integration/`, `make test-integration`).
- **E2E smoke tests** in demo mode. *As built:* in a real browser against the stack (Playwright,
  `make test-e2e`).
- *Added:* **mutation checks** — `make mutants` breaks a guarded property and requires a test to
  notice. Details: [`TESTING.md`](TESTING.md).

---

## 13. Technology Stack

| Layer | Choice |
|-------|--------|
| Gateway API | **Python 3.14, FastAPI**, Pydantic, httpx, SQLAlchemy + Alembic, OpenTelemetry |
| Management backend | **Django + Django REST Framework**, django-guardian; tokens verified by `aira_common.oidc` (PyJWT) |
| Management frontend | **Angular (TypeScript)**, `angular-oauth2-oidc`; the pipeline builder uses no graph library |
| Data | **PostgreSQL** |
| Shared counters | **Redis** (rate limits, budget reservations — `ADR-0008`) |
| Eventing | **Apache Kafka** |
| SSO | **Keycloak** |
| Secrets | **HashiCorp Vault** |
| Observability | **OpenTelemetry Collector** → **Grafana otel-lgtm** (local; ADR-0004) |
| Packaging/Runtime | **Docker Compose** (local) → **Kubernetes/Helm** (future) |
| Testing | pytest + coverage, Vitest + jsdom (Angular), Playwright (browser end-to-end) |
| Language | **English** for docs, code, and identifiers |

---

## 14. Assumptions, Constraints & Risks

**Assumptions**
- Upstream platforms (Gemini Enterprise, MS Foundry) expose usable APIs; exact contracts confirmed
  per integration FRD.
- Initial deployment is single-node Docker Compose on the developer machine.

**Constraints**
- All docs and code in English; secrets only in Vault; near-100% unit-test coverage required.
- Two-component split communicating via Kafka. *As built:* Kafka carries configuration; the
  console reads the gateway's HTTP API for what the gateway records (§4.3).

**Risks**
- R1: Upstream API differences complicate the unified schema → mitigate with adapter layer + mock.
- R2: "Store as much as possible" raises data-privacy/retention concerns → redaction & retention
  controls from the start.
- R3: Kafka-based config distribution adds eventual-consistency complexity → versioned config +
  read-model with fallbacks. *As built:* a transactional outbox and an idempotent read-model; the
  events carry no version (§4.3).
- R4: Near-100% coverage target adds cost → bake testing into every FRD/phase.

---

## 15. Glossary

- **Use Case**: A bounded AI application context with its own members, pipeline, budgets, rules.
- **Pre-dispatch pipeline**: Ordered steps applied before a request reaches an upstream model:
  `injection_filter`, `pii_filter` (personal data replaced before the prompt is sent and stored)
  and `model_route` (routing). Parallel steps are a non-goal; there is no allow-check step.
- **Rerouting**: Cost-based redirection of "easy" requests to cheaper models.
- **Fallback**: Alternative model/upstream used when the primary fails.
- **Incident response**: Configured reaction to an anomaly — throttle, alert, or block.
- **IT Steuerung**: IT Governance oversight role.

---

## 16. Related Documents
- `docs/ROADMAP.md` — phased delivery plan.
- `docs/features/` — individual Feature Requirement Documents (FRDs); the status of each is in its
  header and in the generated [index](features/README.md).
- [`docs/GAP-ANALYSIS.md`](GAP-ANALYSIS.md) — these requirements against what is built.
