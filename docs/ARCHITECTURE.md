# Architecture

The C4 view of AIRA Gateway: context, containers, components, and the decisions that shape them.

For the *runtime* story — what happens to one request from the moment it arrives — see
[`REQUEST-LIFECYCLE.md`](REQUEST-LIFECYCLE.md). For why a particular structure was chosen, the
[ADRs](adr/) are the record; this document points at them rather than repeating them.

> **Diagrams render on GitHub.** They are Mermaid, so they also render in most IDEs and in any
> Markdown viewer with Mermaid support. Where a diagram would only restate a list, there is a list.

---

## 1. What this is, in one paragraph

AIRA Gateway puts **one governed API** in front of several LLM platforms. A use case calls it
instead of calling Google, Microsoft or a self-hosted model directly; in exchange the organisation
gets attribution, budgets, rate limits, a configurable pre-dispatch pipeline, a complete audit
trail, spend reporting, anomaly detection and an incident kill switch. The scope is deliberately
narrow ([`ADR-0013`](adr/ADR-0013-auditable-model-access-not-agents.md)): **auditable model access,
not agents** — no retrieval, no conversation state, no tool execution, no workflow orchestration.

---

## 2. C4 Level 1 — System context

```mermaid
graph TB
    subgraph people[" "]
        direction LR
        dev["Use-case developer<br/><i>calls the API from an application</i>"]
        ucadmin["Use-case administrator<br/><i>configures a use case</i>"]
        itsec["IT Security<br/><i>rules, findings, kill switch</i>"]
        itgov["IT Steuerung / governance<br/><i>spend and usage, read-only</i>"]
        admin["Global administrator<br/><i>model catalog and prices</i>"]
    end

    aira["<b>AIRA Gateway</b><br/>Unified, governed access to LLMs<br/><i>data plane + control plane</i>"]

    subgraph upstreams["Model platforms"]
        direction LR
        vertex["Google Vertex AI<br/><i>Gemini, Claude — EU regions</i>"]
        foundry["Microsoft Foundry<br/><i>Azure OpenAI + MS models</i>"]
        selfhost["Self-hosted<br/><i>Ollama / OpenAI-compatible</i>"]
    end

    subgraph platform["Organisation platform"]
        direction LR
        idp["Keycloak / OIDC<br/><i>identity, roles, groups</i>"]
        vault["HashiCorp Vault<br/><i>secrets</i>"]
        otel["OpenTelemetry backend<br/><i>traces, metrics, logs</i>"]
    end

    dev -->|"REST: Gemini or KIRA dialect<br/>API key or OIDC bearer"| aira
    ucadmin --> aira
    itsec --> aira
    itgov --> aira
    admin --> aira

    aira -->|"HTTPS, per-request<br/>credential from Vault"| vertex
    aira --> foundry
    aira --> selfhost

    aira -->|"validates tokens (JWKS)<br/>reads roles and groups"| idp
    aira -->|"AppRole + KV-v2"| vault
    aira -->|"OTLP"| otel

    style aira fill:#4f46e5,color:#fff,stroke:#4338ca
```

**The five roles** are the ones the realm defines and both planes enforce
([`FRD-201`](features/FRD-201-keycloak-rbac.md), [`ADR-0009`](adr/ADR-0009-gateway-knows-roles.md)).
Two distinctions inside them cost real defects and are therefore explicit:

| Question | Predicate | Who |
|---|---|---|
| may **see** every use case | `has_oversight` | global-admin, it-steuerung, it-security |
| may see every **figure** | `is_governance` | global-admin, it-steuerung |
| may **act** in an incident | `may_act_on_incidents` | global-admin, it-security |

Collapsing the first two left IT Security with an empty console; collapsing the first and third let
a read-only governance role stop traffic. Both are recorded in
[`FRD-206`](features/FRD-206-console-truthfulness.md) and
[`FRD-503`](features/FRD-503-incident-response.md).

---

## 3. C4 Level 2 — Containers

```mermaid
graph TB
    client["Client application"]
    browser["Browser"]

    subgraph aira["AIRA Gateway"]
        direction TB
        spa["<b>Frontend</b><br/>Angular 22 SPA<br/><i>nginx</i>"]
        gw["<b>Gateway API</b><br/>FastAPI · Python 3.14<br/><i>data plane</i>"]
        mgmt["<b>Management API</b><br/>Django + DRF<br/><i>control plane</i>"]
        relay["<b>Outbox relay</b><br/><i>Management → Kafka</i>"]
        consumer["<b>Config consumer</b><br/><i>Kafka → gateway read-model</i>"]
        retention["<b>Retention worker</b><br/><i>hourly payload expiry</i>"]
    end

    subgraph stores["State"]
        direction LR
        pgw[("PostgreSQL<br/><i>aira_gateway</i>")]
        pgm[("PostgreSQL<br/><i>aira_mgmt</i>")]
        redis[("Redis<br/><i>counters</i>")]
        kafka[["Apache Kafka<br/><i>compacted config topics</i>"]]
    end

    idp["Keycloak"]
    models["Model platforms"]

    client -->|"/v1beta · /kira/api/external"| gw
    browser --> spa
    spa -->|"/api → :8002"| mgmt
    spa -->|"/gw → :8001"| gw
    spa -->|"OIDC code flow + PKCE"| idp

    gw --> pgw
    gw --> redis
    gw --> models
    gw -->|"JWKS"| idp
    mgmt --> pgm
    mgmt -->|"JWKS"| idp

    mgmt -->|"transactional outbox"| relay
    relay --> kafka
    kafka --> consumer
    consumer --> pgw
    retention --> pgw

    style gw fill:#4f46e5,color:#fff
    style mgmt fill:#4f46e5,color:#fff
```

### Why two planes, and why they talk over Kafka

The data plane must answer a request without asking anybody. So Management **owns** configuration
and the gateway keeps a **read-model** of it: use cases, memberships, API keys, pipelines, budgets,
rate limits, the model catalog and anomaly rules all arrive as events on compacted topics
([`FRD-204`](features/FRD-204-config-distribution-kafka.md)). The gateway never calls Management on
the request path — not for a price, not for a capability, not for a rule.

The event flow is a **transactional outbox**: `emit()` runs inside the same database transaction as
the change it describes, so an event is never published for a change that rolled back and never
lost for one that committed.

**One exception, deliberate**: the incident kill switch is written straight to the gateway
([`FRD-503` §4.3](features/FRD-503-incident-response.md)). A control that depends on the event bus
fails exactly when the bus is the problem.

### The containers, precisely

| Container | Image / entry point | Ports | What it is |
|---|---|---|---|
| `gateway` | `aira-gateway` · `uvicorn` | 8001 | The API clients call |
| `gateway-consumer` | same image · `python -m aira_gateway.consumer` | — | Applies config events to the read-model |
| `gateway-retention` | same image · `python -m aira_gateway.retention` | — | Deletes expired payloads hourly |
| `management` | `aira-management` · Django | 8002 | Control-plane REST API |
| `management-relay` | same image · `manage.py relay` | — | Publishes the outbox to Kafka |
| `frontend` | `aira-frontend` · nginx | 4200 | The SPA, plus `/api` and `/gw` proxies |

Migrations and topic creation run as one-shot containers before the long-lived ones
(`gateway-migrate`, `management-migrate`, `kafka-topics`); see
[`SETUP.md`](SETUP.md).

---

## 4. C4 Level 3 — Inside the Gateway API

```mermaid
graph TB
    subgraph surfaces["Surfaces — parse and render only"]
        gemini["Gemini surface<br/><i>/v1beta/models/…</i>"]
        kira["KIRA surface<br/><i>/kira/api/external</i>"]
        ops["Reporting · incidents · diagnostics"]
    end

    subgraph shared["api/serving.py — the shared sequence"]
        prep["<b>prepare_for_dispatch</b><br/>owns the ORDER of the pre-dispatch steps"]
        acct["<b>accounting</b><br/>owns every exit: served, refused, cancelled"]
    end

    subgraph controls["Controls"]
        susp["Suspensions"]
        rl["Rate limiter<br/><i>token bucket</i>"]
        budget["Budgets<br/><i>reserve → settle</i>"]
        pipe["Pipeline<br/><i>injection filter · allow-list · routing</i>"]
        cat["Model catalog<br/><i>capabilities, prices</i>"]
    end

    subgraph core["Provider-agnostic core"]
        canon["CanonicalRequest / Response<br/><i>ordered parts, thinking, schema</i>"]
        chain["Dispatch chain<br/><i>conditions, skip-with-reason</i>"]
    end

    subgraph up["upstreams/ — the only code that knows a vendor"]
        vt["Vertex transport"]
        ft["Foundry transport"]
        ot["OpenAI-compatible transport"]
        gd["Gemini dialect"]
        ad["Anthropic dialect"]
        od["OpenAI dialect"]
    end

    audit["Audit writer<br/><i>bounded queue, off the hot path</i>"]
    anom["Anomaly service<br/><i>timer over touched scopes</i>"]

    gemini --> prep
    kira --> prep
    prep --> susp --> rl --> budget --> pipe --> cat
    prep --> canon
    canon --> chain
    chain --> vt & ft & ot
    vt --> gd & ad
    ft --> od
    ot --> od
    gemini --> acct
    kira --> acct
    acct --> audit
    audit -.->|"marks touched scopes"| anom
    anom -.->|"writes a decision"| susp

    style prep fill:#4f46e5,color:#fff
    style acct fill:#4f46e5,color:#fff
```

### The three structural rules

**A surface parses; the layer decides.** Both API dialects call `prepare_for_dispatch` and
`accounting` rather than assembling the steps themselves. Every guarantee that layer makes is a
guarantee about the *order* — rate limit before the pipeline, declaration after routing, reservation
last — and an order cannot be shared by sharing the steps. A test
(`test_surface_layering.py`) fails on a surface that calls a step directly.
([`FRD-126`](features/FRD-126-one-pre-dispatch-sequence.md),
[`FRD-128`](features/FRD-128-one-post-dispatch-sequence.md))

**Transport × dialect × model identity.** A transport owns reaching a cloud (endpoint, credential,
region); a dialect owns the wire shape; the caller's model name is never the platform's addressing.
An architecture assertion parses every module outside `upstreams/` and fails if a vendor name
appears in code. ([`ADR-0011`](adr/ADR-0011-upstreams-platform-dialect-identity.md))

**Hide the plumbing, declare the semantics.** Capability flags say *whether*, never *how*, and
**undeclared means unsupported**. A difference that changes the *answer* is never hidden: a model
that cannot read the attachment is skipped by name, not sent the prompt without it.
([`ADR-0012`](adr/ADR-0012-one-catalog-many-platforms.md),
[`FRD-114`](features/FRD-114-model-capability-metadata.md))

---

## 5. C4 Level 3 — Inside the Management API

```mermaid
graph LR
    subgraph api["REST API — /api/v1"]
        me["/me"]
        uc["/use-cases/…"]
        cat["/models"]
        anom["/anomaly-rules"]
    end

    subgraph apps["Django apps"]
        usecases["usecases<br/><i>+ access.py: the permission predicates</i>"]
        apikeys["apikeys"]
        pipelines["pipelines"]
        budgets["budgets"]
        ratelimits["ratelimits"]
        anomalies["anomalies"]
        catalog["catalog"]
        outbox["outbox"]
        seed["seed"]
    end

    rbac["rbac.py<br/><i>roles → Django groups,<br/>django-guardian object perms</i>"]
    db[("aira_mgmt")]
    relay["relay → Kafka"]

    api --> apps
    apps --> rbac
    apps --> db
    apps -->|"emit() inside the transaction"| outbox
    outbox --> relay

    style usecases fill:#4f46e5,color:#fff
```

**One definition of who may do what.** `apps/usecases/access.py` holds `may_admin`, `may_manage`
and `is_member`; the viewsets enforce with them *and* the serializer reports them to the console, so
the console cannot offer an action the server refuses. An agreement test attempts each request and
requires the status to match what the object reported.
([`FRD-206`](features/FRD-206-console-truthfulness.md))

**The SPA's screens**, and which plane each reads: use-case list/detail (management), the pipeline
builder (management, dry-run against the gateway), budgets and rate limits (management, consumption
from the gateway), models and prices (management), reporting and its CSV export (gateway), the
**Security console** and per-use-case **Warnings** and **Traces** (gateway). The last three refresh
themselves through one primitive, `core/ui/live.ts`: it polls, it stops on destroy and while the tab
is hidden, it never stacks a request behind a slow one, and it shows the reader how stale the view is
with a switch to turn it off ([`FRD-502`](features/FRD-502-security-console-and-traces.md)).

---

## 6. Data stores, and what each is for

| Store | Holds | Why this one |
|---|---|---|
| `aira_mgmt` (Postgres) | use cases, memberships, keys (hashed), pipelines, budgets, limits, rules, catalog, outbox | System of record for **configuration** |
| `aira_gateway` (Postgres) | read-model of all of the above, plus `request_logs`, `anomaly_events`, `access_suspensions`, `budget_usage` | System of record for **what happened**; the gateway cannot serve without it |
| Redis | rate-limit buckets, budget reservations | Shared counters across replicas; written on **every** request ([`ADR-0008`](adr/ADR-0008-redis-shared-counters.md)) |
| Kafka | compacted config topics | Ordered, replayable configuration distribution |
| Vault | credentials | Read as a **settings source**, never injected into `os.environ` ([`FRD-116`](features/FRD-116-vault-secrets.md)) |

**Degradation is decided, not accidental.** Without Redis, rate limits fall back to a per-instance
bucket (bounding, not fail-open) and budgets to the Postgres path (enforcing but racy); `/readyz`
answers 200 with `degraded: true`, and the degradation is frozen onto each audit row so a request
can be read in the light of the conditions it met.

---

## 7. Where the money and the evidence live

```mermaid
graph LR
    req["Request"] --> log["request_logs<br/><i>one row per request,<br/>served or refused</i>"]
    log --> report["Reporting<br/><i>spend, usage, CSV</i>"]
    log --> detect["Anomaly engine<br/><i>the same rows</i>"]
    detect --> event["anomaly_events"]
    detect --> susp["access_suspensions"]
    susp --> gate["Pre-dispatch gate"]
    gate --> req

    style log fill:#4f46e5,color:#fff
```

Detection reads **the audit trail and nothing else**
([`ADR-0014`](adr/ADR-0014-detection-is-asynchronous-enforcement-is-not.md)). Two consequences,
both wanted: a detector cannot see anything the report cannot, so "the alert says X but the report
says Y" is unreachable — and it sees *refusals*, which is where much of the signal is.

Money is **integer nano-units, never a float**, and crosses APIs as decimal strings
([`FRD-403`](features/FRD-403-cost-budgets.md)). Unpriced traffic is counted apart, never as zero —
and a *refused* request is neither: nothing ran, so its cost is a genuine zero rather than an
unknown.

---

## 8. Reading further

| For | Read |
|---|---|
| One request, end to end | [`REQUEST-LIFECYCLE.md`](REQUEST-LIFECYCLE.md) |
| Running it | [`SETUP.md`](SETUP.md) |
| Every environment variable | [`CONFIGURATION.md`](CONFIGURATION.md) |
| Connecting real systems | [`INTEGRATIONS.md`](INTEGRATIONS.md) |
| Why a decision was made | [`adr/`](adr/) |
| What a feature must do | [`features/`](features/) |
| What is not built yet | [`GAP-ANALYSIS.md`](GAP-ANALYSIS.md) |
