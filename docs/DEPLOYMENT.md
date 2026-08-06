# Deployment

How to bring AIRA up — standalone on one machine, or connected to infrastructure you already
run. Every value in here is taken from the code; where something is not implemented yet, this
document says so rather than describing an intention.

- [1. What actually runs](#1-what-actually-runs)
- [2. Standalone (one machine)](#2-standalone-one-machine)
- [3. Integrating with existing infrastructure](#3-integrating-with-existing-infrastructure)
- [4. Configuration reference](#4-configuration-reference)
- [5. Preparing Keycloak](#5-preparing-keycloak)
- [6. Production checklist](#6-production-checklist)
- [7. Known gaps](#7-known-gaps)

---

## 1. What actually runs

AIRA is five processes plus infrastructure. Only the first two serve traffic; the other three are
workers and a static bundle.

| # | Process | Command | Listens | Required |
|---|---------|---------|---------|----------|
| 1 | **Gateway API** (data plane) | `uvicorn aira_gateway.main:app` | HTTP :8001 | yes |
| 2 | **Management API** (control plane) | ASGI `aira_management.config.asgi:application` | HTTP :8002 | yes |
| 3 | **Config consumer** | `python -m aira_gateway.consumer.worker` | — | yes, if the SPA is used |
| 4 | **Outbox relay** | `python manage.py relay` | — | yes, if the SPA is used |
| 4b | **Retention pruner** | `python -m aira_gateway.retention` | — | **yes** — nothing deletes stored prompts otherwise |
| 5 | **Management SPA** | static bundle from `ng build` | served by any web server | yes, for the UI |

Infrastructure:

| Component | Required | Used for |
|-----------|----------|----------|
| **PostgreSQL** | **yes** | two databases: `aira_gateway`, `aira_mgmt` |
| **Keycloak** (or any OIDC provider) | **yes** in practice | user login, roles, use-case membership |
| **Apache Kafka** | **yes**, if the SPA is used | config distribution control plane → data plane |
| OpenTelemetry collector | optional | traces/metrics/logs (`AIRA_OTEL_ENABLED`) |
| HashiCorp Vault | **not used by any code today** | see [Known gaps](#7-known-gaps) |
| Schema Registry | **not used by any code today** | events are plain JSON with an `event_type` header |

All five run from **three images**, built from this repository:

| Image | Dockerfile | Runs |
|---|---|---|
| `aira-gateway` | `gateway/Dockerfile` | the gateway, the config consumer, the retention pruner, and `alembic upgrade head` |
| `aira-management` | `management/backend/Dockerfile` | the API, `manage.py migrate`, and the outbox relay |
| `aira-frontend` | `management/frontend/Dockerfile` | the built SPA behind nginx, which also proxies `/api` and `/gw` |

Both Python images are multi-stage (build with `uv`, ship only the resolved virtualenv), run as a
non-root user (uid 10001) and carry a `HEALTHCHECK`.

### How the two planes talk

They never call each other over HTTP. Management writes to a **transactional outbox** in its own
database; the **relay** publishes those rows to Kafka; the gateway's **consumer** applies them
into local read-model tables. A change made in the UI therefore becomes effective in the gateway
only after the relay has run and the consumer has applied it.

```
Management API ──▶ outbox table ──▶ relay ──▶ Kafka ──▶ consumer ──▶ gateway read-model
                                  (periodic)   (5 compacted topics)
```

The one exception is the SPA, which calls the gateway directly for two read-only views (pipeline
dry-run and budget consumption) — that is why the gateway needs OIDC configured.

---

## 2. Standalone (one machine)

Everything on one host, infrastructure in Docker, applications from source. This is the
development and demo setup, and it is what the `Makefile` targets automate.

### Prerequisites

- Docker + Docker Compose
- Python **3.14** and [`uv`](https://docs.astral.sh/uv/)
- Node **26** (for the SPA)

### Everything in containers (one command)

```bash
make up-full
```

That builds the three images and starts infrastructure plus all five application processes, in
the right order: migrations and topic creation run to completion before the services that depend
on them. Then open <http://localhost:4200> and log in as `ucadmin` / `demo-password`.

```bash
make logs-apps     # tail only the application containers
make down-full     # stop everything (volumes are kept)
make build-images  # build the images without starting anything
```

The relay runs as a loop container (`AIRA_RELAY_INTERVAL`, default 10s) so configuration
propagates on its own — see the note in [§6](#6-production-checklist) about scheduling it
properly outside of Compose.

### From source (for development)

Use this when you are editing code and want reload-on-save.

```bash
# 1. dependencies
make sync

# 2. infrastructure (postgres, keycloak, kafka, schema-registry, vault, otel-lgtm)
make up

# 3. databases
make seed                # management: migrate + demo roles/users
make migrate-gateway     # gateway: alembic migrations

# 4. Kafka topics (compacted; auto-create is off, so this is required)
make kafka-topics
```

Then start the processes, each in its own terminal:

```bash
make run-gateway-oidc    # gateway on :8001, OIDC enabled
make run-backend         # management on :8002
make consume             # config consumer (long-running)
make run-frontend        # SPA dev server on :4200, proxies /api and /gw
```

And whenever you change something in the UI that the gateway must see (members, API keys,
pipelines, budgets):

```bash
make relay               # publishes pending outbox rows — one shot, not a daemon
```

Open <http://localhost:4200> and log in with `ucadmin` / `demo-password`.

Demo accounts (all `demo-password`): `admin` (global-admin), `itsec` (it-security), `itgov`
(it-steuerung), `ucadmin` (use-case-admin), `ucuser` (use-case-user).

### Why `run-gateway-oidc` and not `run-gateway`

The SPA sends its Keycloak bearer token to the gateway for the pipeline dry-run and the budget
consumption view. Without `AIRA_OIDC_ENABLED=true` the gateway cannot verify that token and both
views degrade (they say so in the UI); everything else keeps working.

### Verifying the installation

```bash
make test                # hermetic unit tests (no stack needed)
make test-integration    # server-side checks against the running stack
make test-e2e            # browser end-to-end (see e2e/README.md for prerequisites)
```

---

## 3. Integrating with existing infrastructure

Point AIRA at what you already run. Each subsection lists the minimum you have to provide.

### 3.1 PostgreSQL

AIRA needs **two databases**. They may live on the same server; the gateway and management never
share tables.

```sql
CREATE DATABASE aira_gateway;
CREATE DATABASE aira_mgmt;
```

Both services take the same connection variables but a different `AIRA_POSTGRES_DB` — so give
each process its own environment:

| Variable | Gateway | Management |
|---|---|---|
| `AIRA_POSTGRES_HOST` | your host | your host |
| `AIRA_POSTGRES_PORT` | `5432` | `5432` |
| `AIRA_POSTGRES_DB` | `aira_gateway` | `aira_mgmt` |
| `AIRA_POSTGRES_USER` / `AIRA_POSTGRES_PASSWORD` | a dedicated role | a dedicated role |

Schema management differs per service — run both on every deployment:

```bash
cd gateway && alembic upgrade head            # gateway
cd management/backend && python manage.py migrate   # management
```

There is no TLS or connection-pool setting exposed yet; the URL is assembled in
`GatewaySettings.database_url()` and `aira_management.config.database`.

### 3.2 Keycloak / OIDC provider

Any OIDC provider works in principle — the code verifies a JWT against the issuer's JWKS. What
AIRA *requires from the token* is specific, see [§5](#5-preparing-keycloak).

```bash
AIRA_OIDC_ISSUER=https://sso.example.com/realms/aira
AIRA_OIDC_AUDIENCE=aira-gateway     # strongly recommended, see below
AIRA_OIDC_ENABLED=true              # gateway only; management is always OIDC
# AIRA_OIDC_JWKS_URI=...            # only if it is not <issuer>/protocol/openid-connect/certs
```

> **Set the audience.** With `AIRA_OIDC_AUDIENCE` empty, audience verification is skipped and
> *any* token the issuer minted — including one for an unrelated client — is accepted. The gateway
> logs a warning (`oidc_audience_unset`) at startup when this is the case.

The `AIRA_OIDC_JWKS_URI` override exists for providers that do not follow Keycloak's URL layout.

### 3.3 Kafka

Seven **compacted** topics carry the configuration. Auto-creation is off in the reference stack,
so create them (partition/replication counts are yours to choose; compaction is not optional —
the gateway rebuilds its read-model by replaying them).

**Create all of them.** A missing topic produces no error anywhere: Management writes its outbox,
the relay cannot publish, and the gateway simply never learns about that kind of configuration.
The symptom is a setting that appears saved in the UI and does nothing.

| Topic | Carries |
|---|---|
| `aira.usecases` | use-case create/update/delete |
| `aira.memberships` | membership changes |
| `aira.api-keys` | API-key issuance and revocation (hash only, never plaintext) |
| `aira.pipelines` | pipeline configuration |
| `aira.budgets` | budget definitions |
| `aira.rate-limits` | request-rate limits (FRD-405) |
| `aira.models` | model catalog and prices (FRD-403) |

```bash
kafka-topics.sh --create --topic aira.usecases --config cleanup.policy=compact ...
```

Both services take `AIRA_KAFKA_BOOTSTRAP_SERVERS` (comma-separated). The consumer joins the group
`aira-gateway` with `auto_offset_reset=earliest`.

**No authentication or TLS is configurable today** — the producer and consumer are constructed
with the bootstrap servers only (`aira_common.kafka`, `aira_gateway.consumer.worker`). A broker
requiring SASL/TLS needs a code change.

### 3.4 Observability

```bash
AIRA_OTEL_ENABLED=true
AIRA_OTEL_ENDPOINT=http://otel-collector:4318     # OTLP/HTTP, no trailing path
AIRA_OTEL_SAMPLE_RATIO=0.1                        # parent-based; 1.0 samples everything
```

Traces, metrics and logs are exported over OTLP/HTTP. Spans carry `aira.*` attributes
(subject, use case, model, tokens, latency); credential-bearing query parameters are redacted
before they reach a span (ADR-0007).

### 3.5 Model prices

Cost budgets are calculated from prices a Global Administrator maintains under **Models &
prices** (`/api/v1/models/`): per model, the price of 1,000,000 input and 1,000,000 output
tokens, in `AIRA_CURRENCY`. Input and output must both be set — a one-sided price would produce
a cost figure that looks complete and silently omits half the spend.

A model with no price still serves requests; its consumption is counted under
`unpriced_requests` and left out of the spend figures rather than counted as free. Check that
warning after adding a model, or the budget everyone is watching will be quietly too low.

### 3.6 Upstream LLM provider

Without a provider key the gateway serves only the deterministic **mock** provider (`mock-1`) —
useful for demos and tests, and the reason the stack works out of the box.

```bash
AIRA_GOOGLE_API_KEY=...                                   # registers the real Gemini provider
AIRA_GEMINI_MODELS=gemini-2.0-flash,gemini-1.5-flash      # which models to expose
AIRA_GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
```

`AIRA_GEMINI_BASE_URL` is what you point at a proxy or a regional endpoint. The key is sent as a
query parameter to the upstream and is never logged or exported.

### 3.7 Reverse proxy and TLS

The `aira-frontend` image already contains an nginx that serves the SPA and proxies `/api` and
`/gw` (upstreams configurable via `AIRA_MANAGEMENT_UPSTREAM` and `AIRA_GATEWAY_UPSTREAM`). It
listens on **8080** and terminates no TLS — put your own proxy in front of it.

If you serve the SPA yourself instead, route:

| Path | To |
|---|---|
| `/` | the SPA's static bundle |
| `/api/…` | Management API |
| `/gw/…` | Gateway (strip the `/gw` prefix) |

The gateway's own API (`/v1beta/…`) is normally published separately for API clients — it is the
data plane and does not need to sit behind the SPA's origin.

If a proxy sits in front, decide about the client IP recorded in the audit log:

```bash
AIRA_TRUST_FORWARDED_FOR=true    # ONLY if your proxy overwrites X-Forwarded-For
```

Left at `false` (the default), the socket peer is recorded. Turn it on only when the header cannot
be forged by clients — otherwise the audit trail becomes client-controlled (ADR-0007).

### 3.8 The SPA is configured at build time

`management/frontend/src/app/core/auth/auth.config.ts` **hardcodes** the issuer and client id:

```ts
issuer: 'http://localhost:8080/realms/aira',
clientId: 'aira-gateway',
```

To deploy against your own Keycloak you currently have to edit that file and rebuild
(`ng build`). There is no runtime configuration file yet — see [Known gaps](#7-known-gaps).

The `redirectUri` derives from `window.location.origin`, so it follows wherever you host the
bundle — but that origin must be registered in the Keycloak client (§5).

---

## 4. Configuration reference

All settings are environment variables with the prefix `AIRA_`. They are also read from a `.env`
file in the **process working directory** (see `aira_common.config.BaseAiraSettings`).

### Common to both services

| Variable | Default | Meaning |
|---|---|---|
| `AIRA_ENVIRONMENT` | `local` | `local` \| `staging` \| `production`. Anything but `local` activates the management safety checks and HSTS. |
| `AIRA_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `AIRA_LOG_JSON` | `true` | JSON lines (`true`) or human-readable console output |
| `AIRA_DEMO_MODE` | `false` | Gateway: seeds a deterministic demo API key. Management: allows `seed_demo`. **Never in production.** |
| `AIRA_CURRENCY` | `EUR` | Currency all model prices and cost budgets are expressed in (FRD-403). One per installation; no conversion happens, so it must match the prices you enter. |
| `AIRA_OTEL_ENABLED` | `false` | Export OTLP |
| `AIRA_OTEL_ENDPOINT` | `http://localhost:4318` | OTLP/HTTP endpoint |
| `AIRA_OTEL_SAMPLE_RATIO` | `1.0` | Trace sampling ratio |
| `AIRA_POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | `localhost` / `5432` / per service / `aira` / `aira-local` | Database connection |
| `AIRA_KAFKA_BOOTSTRAP_SERVERS` | `localhost:29092` | Comma-separated broker list |
| `AIRA_OIDC_ISSUER` | `''` | OIDC issuer URL |
| `AIRA_OIDC_AUDIENCE` | `''` | Expected `aud`. **Empty disables audience verification.** |
| `AIRA_OIDC_JWKS_URI` | `''` | Override; derived from the issuer when empty |
| `AIRA_TEST_DATABASE` | `false` | In-memory SQLite. Test harness only. |

### Gateway only

| Variable | Default | Meaning |
|---|---|---|
| `AIRA_OIDC_ENABLED` | `false` | Accept OIDC bearer tokens. **Required for the SPA's dry-run and consumption views.** Without it only API keys are accepted. |
| `AIRA_AUTH_REQUIRED` | `true` | `false` opens all API routes with a synthetic `demo` principal. Demo only. |
| `AIRA_REQUIRE_USE_CASE` | `false` | Reject authenticated requests that carry no use-case selector |
| `AIRA_STORE_PAYLOADS` | `true` | Persist request/response bodies in `request_logs`. **Kill switch**: `false` here means no use case can store them, whatever its own setting says (FRD-404). |
| `AIRA_DEFAULT_RETENTION_DAYS` | `7` | Payload retention for requests that carry **no** use case. Use-case traffic follows the period set on its use case (FRD-404). |
| `AIRA_LOG_RETENTION_DAYS` | `0` | Delete whole `request_logs` rows older than this. `0` keeps them forever — the cost reporting reads them, so opt in deliberately. |
| `AIRA_ENFORCE_BUDGETS` | `true` | Reject over-budget requests with 429 |
| `AIRA_REDIS_URL` | `redis://localhost:6379/0` | Shared counter store for rate-limit buckets and budget reservations (ADR-0008). **Set this whenever more than one gateway instance runs**: without it each instance keeps its own counters, so N instances allow N × the configured rate and a budget can be overshot. Empty disables it — see the degradation table below. |
| `AIRA_ENFORCE_RATE_LIMITS` | `true` | Enforce the per-use-case/per-member request rate. A use case with no configured limit stays unlimited either way. |
| `AIRA_BUDGET_ESTIMATE_OUTPUT_TOKENS` | `1024` | Tokens assumed for a request whose output length the caller did not bound, used to reserve budget before the real usage is known. Corrected the moment the response arrives. |
| `AIRA_LOG_QUEUE_SIZE` | `512` | Request-log rows buffered off the request path. A full queue writes inline rather than dropping. **`0` writes every row synchronously**, which is the pre-FRD-405 behaviour — choose it if a request must be durably logged before its response goes out. |
| `AIRA_TRUST_FORWARDED_FOR` | `false` | Honour `X-Forwarded-For` for the recorded source IP |
| `AIRA_MAX_REQUEST_BYTES` | `8388608` (8 MiB) | Hard ceiling on a request body; larger bodies get 413 before being buffered |
| `AIRA_GOOGLE_API_KEY` | `''` | Registers the real Gemini provider when set |
| `AIRA_GEMINI_MODELS` | `gemini-2.0-flash,gemini-1.5-flash` | Models exposed by that provider |
| `AIRA_GEMINI_BASE_URL` | Google's v1beta endpoint | Upstream base URL |

#### The SPA's reverse proxy must re-resolve its upstreams

The container image serves the SPA through nginx and proxies `/api` to management and `/gw` to the
gateway, so the browser keeps calling one origin and the bearer token never crosses to a third.

nginx resolves a hostname written literally in `proxy_pass` **once**, when the configuration
loads, and holds that address for the life of the process. In an orchestrator every redeploy
hands a container a new address, so a literal upstream leaves the proxy talking to an address
nobody is listening on — the SPA reports the backend as unreachable while both services are
perfectly healthy, and only restarting nginx fixes it.

The shipped config therefore passes each upstream through a variable and configures a `resolver`,
which defers resolution to request time. If you replace this config, keep that shape.
`AIRA_DNS_RESOLVER` defaults to Docker's embedded DNS (`127.0.0.11`); set it to your platform's
resolver elsewhere (in Kubernetes, `kube-dns.kube-system.svc.cluster.local`).

#### What happens when Redis is unavailable

Deliberate, not accidental — a cache outage must not become a product outage (ADR-0008):

| | Behaviour | Consequence for you |
|---|---|---|
| Rate limiting | falls back to a **per-instance** bucket | Still bounded, but N instances allow N × the limit until Redis returns. Not fail-open: Redis being down is exactly when a runaway caller does the most damage. |
| Budget enforcement | falls back to the **Postgres** read-then-book path | Still enforced, but concurrent requests stop seeing each other's reservations, so a limit can be overshot by requests in flight. |

Both log a warning. Postgres remains the system of record for budget usage in every case, so a
Redis restart costs the in-flight reservations and never the period's accounting.

**Production Redis**: the reference stack runs it without authentication or TLS, which is fine on
a Compose network and not fine anywhere else. Use `rediss://user:password@host:6379/0` and give it
its own instance or database — the keys are namespaced (`rl:*`, `budget:*`) but not isolated.

### Management only

| Variable | Default | Meaning |
|---|---|---|
| `AIRA_SECRET_KEY` | dev value | Django signing key. **Must be unique and secret.** |
| `AIRA_DEBUG` | `true` | Forced to `false` outside `local` regardless of this value |
| `AIRA_ALLOWED_HOSTS` | `*` | Comma-separated hostnames |

**Outside `AIRA_ENVIRONMENT=local` the management service refuses to start** while `AIRA_SECRET_KEY`
is the development default, `AIRA_ALLOWED_HOSTS` contains `*`, or `AIRA_DEBUG` is on. That is
deliberate (ADR-0007): a silent start with a well-known signing key is worse than a failed one.

### Minimal production environment

Gateway:

```bash
AIRA_ENVIRONMENT=production
AIRA_POSTGRES_HOST=db.internal
AIRA_POSTGRES_DB=aira_gateway
AIRA_POSTGRES_USER=aira_gateway
AIRA_POSTGRES_PASSWORD=<secret>
AIRA_KAFKA_BOOTSTRAP_SERVERS=kafka-1:9092,kafka-2:9092
AIRA_OIDC_ENABLED=true
AIRA_OIDC_ISSUER=https://sso.example.com/realms/aira
AIRA_OIDC_AUDIENCE=aira-gateway
AIRA_GOOGLE_API_KEY=<secret>
AIRA_TRUST_FORWARDED_FOR=true      # only behind a header-overwriting proxy
AIRA_OTEL_ENABLED=true
AIRA_OTEL_ENDPOINT=http://otel-collector:4318
```

Management (same database server, different database):

```bash
AIRA_ENVIRONMENT=production
AIRA_SECRET_KEY=<unique secret>
AIRA_ALLOWED_HOSTS=aira.example.com
AIRA_DEBUG=false
AIRA_POSTGRES_HOST=db.internal
AIRA_POSTGRES_DB=aira_mgmt
AIRA_POSTGRES_USER=aira_mgmt
AIRA_POSTGRES_PASSWORD=<secret>
AIRA_KAFKA_BOOTSTRAP_SERVERS=kafka-1:9092,kafka-2:9092
AIRA_OIDC_ISSUER=https://sso.example.com/realms/aira
AIRA_OIDC_AUDIENCE=aira-gateway
AIRA_OTEL_ENABLED=true
AIRA_OTEL_ENDPOINT=http://otel-collector:4318
```

The consumer and the relay read the same variables as their service (gateway and management
respectively) — give them the identical environment.

---

### 4a. Vertex AI in the EU (FRD-115)

The Generative Language adapter (`AIRA_GOOGLE_API_KEY`) is the **laptop** path: one key, no GCP
project, a global endpoint. It cannot make a residency statement and is not a production candidate
where one is required.

```bash
AIRA_VERTEX_PROJECT=my-gcp-project
AIRA_VERTEX_CREDENTIALS='{"client_email":"…","private_key":"…"}'   # FRD-116 moves this to Vault
AIRA_ALLOWED_REGIONS=eu,europe-west1               # empty → the EU regions of every cloud
AIRA_VERTEX_MODELS=eu/google/gemini-2.5-pro,eu/anthropic/claude-sonnet-4-5@20250929
```

Each model is `region/publisher/model` — the three things the URL and the dialect choice need.

**Four things fail at startup rather than at dispatch**, on purpose. A gateway that starts and then
fails every request looks like an upstream outage; one that starts and quietly serves a non-EU
region is worse than one that will not start at all.

| Refused at boot | Because |
|---|---|
| A model in a region outside `AIRA_ALLOWED_REGIONS` | residency is a configuration claim, and this is what makes it hold |
| Credentials that are not a usable service-account key | a credential problem must not present as an upstream problem |
| A model spec that is not `region/publisher/model` | a typo here would otherwise become a 404 per request |
| The same model name on two adapters | otherwise the region and credential that served a request are a silent choice |

**`AIRA_ALLOWED_REGIONS` is not Vertex's list.** It is the deployment's residency policy and every
transport is measured against it — Google's `europe-west1` and Azure's `westeurope` sit in the same
setting, because "which regions may we use" is one question with a vendor-specific vocabulary. A
per-cloud list would mean a per-cloud audit, and the one added last is the one nobody remembers to
check. Left empty it means the EU regions of every supported cloud, never "no constraint".

**Provenance.** Every request records `provider`, `publisher` and `region`; reporting can break
down by them. That is what turns "we are in the EU" from an assertion into something an auditor can
check per request.

**The service-account key is the most valuable secret here.** It is never logged, never in a span
and never in an error message — but an environment variable is an interim arrangement: it appears
in `docker inspect`, in process listings and in orchestrator manifests. `FRD-116` moves it to
Vault, and until then this is a documented gap rather than a design.

## 5. Preparing Keycloak

The reference realm is `deploy/compose/keycloak/realms/aira-realm.json`. Reproduce these four
things in your own realm.

### 5.1 A public client for the SPA

| Setting | Value | Why |
|---|---|---|
| Client ID | `aira-gateway` (or your own, then edit `auth.config.ts`) | |
| Access type | **public** | it is a browser app |
| Standard flow | **on** | authorization code |
| Direct access grants | **off** | the password grant is a credential-phishing surface on a public client |
| PKCE method | **S256** | without it, an intercepted code can be redeemed |
| Valid redirect URIs | your SPA origin, e.g. `https://aira.example.com/*` | **never `*`** — a wildcard lets an attacker redirect the code to a site they control |
| Web origins | your SPA origin | |

### 5.2 The five realm roles

`global-admin`, `it-security`, `it-steuerung`, `use-case-admin`, `use-case-user`.

Keycloak is the **source of truth** for roles (FRD-201): on every authenticated request the token's
`realm_access.roles` are synced onto the user's Django groups. A role that exists only in Django is
removed at the next login.

### 5.3 A groups mapper

The gateway derives use-case membership from group paths, so the token must carry them:

| Mapper setting | Value |
|---|---|
| Type | Group Membership |
| Claim name | `groups` |
| Full group path | **on** |
| Add to access token | **on** |

### 5.4 Groups per use case

Membership for the **data plane** is the group `/use-cases/<slug>`. A user who is a member of a
use case in the Management UI but not in the matching Keycloak group can administer it, but
cannot send requests through the gateway for it — and does not see its consumption numbers.

> This split is a known rough edge, not a design goal: use cases are administered in Management,
> while the gateway authorizes OIDC callers from Keycloak groups. Until they are reconciled (see
> [Known gaps](#7-known-gaps)), create the group when you create the use case.

---

## 6. Production checklist

Security (all from ADR-0007):

- [ ] `AIRA_ENVIRONMENT` is not `local`, `AIRA_SECRET_KEY` is unique, `AIRA_ALLOWED_HOSTS` is
      explicit, `AIRA_DEBUG` is off — the service refuses to start otherwise
- [ ] `AIRA_OIDC_AUDIENCE` set on **both** services
- [ ] `AIRA_DEMO_MODE` off, and `seed_demo` never run (it creates well-known accounts, including
      a superuser, with a fixed password)
- [ ] Keycloak client: no wildcard redirect URIs, direct access grants off, PKCE S256
- [ ] `AIRA_TRUST_FORWARDED_FOR` matches reality — on only behind a header-overwriting proxy
- [ ] TLS terminated in front of both services; the SPA served over HTTPS (the client refuses the
      code flow against a remote issuer over plain HTTP)
- [ ] Database credentials, `AIRA_SECRET_KEY` and the provider key injected as secrets, not baked
      into images or committed

Operations:

- [ ] Both migration commands run on every deploy
- [ ] All seven Kafka topics exist and are **compacted** (`make kafka-topics` creates them)
- [ ] The consumer runs as a long-lived process and is restarted on failure
- [ ] **The retention pruner is scheduled.** `python -m aira_gateway.retention` does one pass and
      exits. If nothing runs it, **nothing is ever deleted** and the period configured per use
      case is a promise nobody keeps. The reference stack runs it hourly in a container; outside
      Compose use cron, a systemd timer or a CronJob.
- [ ] **The relay is scheduled.** `manage.py relay` publishes pending rows once and exits — a cron
      job, systemd timer or Kubernetes CronJob every minute is the intended shape. Without it,
      nothing a user configures in the UI ever reaches the gateway.
- [ ] Prices on file for every model in use — check the unpriced warning on **Models & prices**;
      unpriced traffic is excluded from every spend figure (FRD-403)
- [ ] `AIRA_STORE_PAYLOADS` reviewed against your data-protection rules — with it on, full prompts
      and responses are written to `request_logs`. They are deleted after each use case's
      retention period (default **7 days**, FRD-404), but the *content* redaction hook is still a
      no-op (`aira_gateway.persistence.redaction.NoOpRedactor`): nothing is masked before storage
- [ ] Retention periods reviewed with whoever is accountable for each use case, and payload
      storage switched **off** for any use case whose data must not be persisted at all
- [ ] `/healthz` (liveness) and `/readyz` (readiness) wired into your orchestrator on both services

---

## 6a. Upgrading: deploy every component, or a migration can be undone

The gateway and its consumer call `create_all` at startup — a development convenience that
predates Alembic and is still there. It has one consequence that only shows up during an upgrade:

> **A container running the previous image can recreate a table the new migration renamed or
> dropped**, and will then fail every event against it.

Observed on 2026-08-06, upgrading to `0013_model_capabilities`, which renames `model_prices` to
`model_catalog`. The gateway and Management were rebuilt; the *consumer* was not, and its
`create_all` recreated the empty `model_prices` — after which every model event failed against a
table Alembic had already renamed. Nothing crashed: the consumer logged and the declarations simply
never arrived, which presents as "the feature does not work".

So, when a release contains a migration that renames or drops:

1. Run the migration.
2. Deploy **every** component that opens the database — gateway, consumer, retention — not only the
   one whose feature changed.
3. Check for a resurrected table (`\dt`) before assuming the migration held.

The durable fix is to stop calling `create_all` outside tests. It is on the backlog rather than in
this release, because doing it needs the demo and CI paths to build their schema from migrations
instead — which is a change worth making deliberately.

## 7. Known gaps

Stated plainly, because a deployment guide that hides them wastes your time:

| Gap | Consequence | Status |
|---|---|---|
| **No Kubernetes/Helm** | Compose only; no manifests or charts | Planned (see `docs/ROADMAP.md`) |
| **Images are not published** | `make up-full` builds them locally; there is no registry push or tagging scheme beyond `AIRA_IMAGE_TAG` | — |
| **Vault is not used by any code** | It runs in the reference stack but nothing reads from it; secrets come from environment variables | PRD §9 intends Vault; not implemented |
| **Schema Registry is not used** | Events are plain JSON with an `event_type` header | Runs in the stack, unused |
| **SPA configuration is build-time** | Changing issuer or client id requires editing `auth.config.ts` and rebuilding | No runtime config file yet |
| **Kafka has no auth/TLS settings** | A broker requiring SASL/TLS needs a code change | `aira_common.kafka` takes bootstrap servers only |
| **The relay is not a daemon** | Must be scheduled externally, or configuration never propagates | By design (transactional outbox), but unscheduled by default |
| **Membership is split** between Management and Keycloak groups | Consumption views and data-plane access need the Keycloak group; the UI membership alone is not enough | ADR-0007 addendum, follow-up recorded |
| **No content redaction** | Payloads are stored verbatim until their retention period expires; nothing masks sensitive values inside them | `NoOpRedactor`; retention itself is done (FRD-404) |
