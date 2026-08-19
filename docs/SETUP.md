# Setup

> **This page has been split into four**, one per way of running AIRA, each written step by step for
> somebody doing it for the first time. It is kept as a redirect because links to it exist.
>
> | | For |
> |---|---|
> | [**Showcase**](deployment/showcase.md) | seeing the whole product work, with real traffic |
> | [**Standalone**](deployment/standalone.md) | running it on one machine, everything in containers |
> | [**Development**](deployment/dev.md) | changing the code, with reload on save |
> | [**Integrated**](deployment/integrated.md) | your infrastructure, your Keycloak, your models |
>
> Reference that applies to all four: [configuration](CONFIGURATION.md),
> [integrations](INTEGRATIONS.md), [operations](DEPLOYMENT.md), [roles](ROLES.md).

---

Four ways to run AIRA Gateway, from "I want to look at it" to "I want it on our infrastructure".
Pick the row that matches your intent.

| I want to… | Read | Needs |
|---|---|---|
| See it working, with data and a real model | [§2 Demo](#2-demo--everything-including-a-real-model) | Docker |
| Run the whole thing locally, no source checkout beyond this repo | [§3 Standalone](#3-standalone--everything-in-containers) | Docker |
| Develop with reload-on-save | [§4 Development](#4-development--from-source-no-application-containers) | Docker, Python 3.14 + uv, Node 26 |
| Deploy onto existing infrastructure | [§5 Integrated](#5-integrated--onto-your-own-infrastructure) | see [`INTEGRATIONS.md`](INTEGRATIONS.md) |

Every configuration value is in [`CONFIGURATION.md`](CONFIGURATION.md). What each container is and
why is in [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 1. Prerequisites

| Setup | Docker | Python 3.14 + [uv](https://docs.astral.sh/uv/) | Node 26 | Disk |
|---|:--:|:--:|:--:|---|
| Demo | yes | — | — | ~4 GB (model weights) |
| Standalone | yes | — | — | ~2 GB |
| Development | yes (infrastructure) | yes | yes | ~2 GB |
| Integrated | for images | — | — | — |

Versions are pinned ([`ADR-0003`](adr/ADR-0003-toolchain-versions.md)): `.python-version` and
`pyproject.toml` for Python, `.nvmrc` and `package.json` `engines` for Node. `make sync` installs
both dependency sets.

```bash
make help     # every target, with one line each
```

---

## 2. Demo — everything, including a real model

The fastest way to see the product rather than the infrastructure. Starts the stack, pulls a small
local model, seeds three use cases with budgets, rate limits, pipelines and API keys, and then
drives **real traffic** through the gateway — including a prompt injection the filter refuses.

```bash
make showcase
```

Then open <http://localhost:4200> and sign in. Every role sees something different, on purpose:

| User | Password | Sees |
|---|---|---|
| `admin` | `demo-password` | everything; the only role that may price a model |
| `ucadmin` | `demo-password` | administers **two** of the three use cases |
| `ucuser` | `demo-password` | member of one; read-only everywhere |
| `itsec` | `demo-password` | every use case's configuration; may stop traffic |
| `itgov` | `demo-password` | every use case and every figure; changes nothing |

`ucadmin` administering two of three is deliberate — switching to that account and finding two is
the fastest way to see the scoping is real rather than a filter in the frontend.

```bash
make showcase-traffic  # drive more traffic; the budget bars move
make down-full        # stop it (volumes and model weights are kept)
```

The model download is a separate step from the container's health check, so a restart does not look
like a hang. Details: [`FRD-130`](features/FRD-130-demo-showcase.md).

---

## 3. Standalone — everything in containers

Infrastructure plus all six application processes, on one machine, with nothing installed but
Docker.

```bash
make up-full
```

```mermaid
graph LR
    subgraph oneshot["one-shot, in order"]
        m1["gateway-migrate"] --> m2["management-migrate"] --> m3["kafka-topics"] --> m4["management-seed"]
    end
    oneshot --> longlived
    subgraph longlived["long-lived"]
        gw["gateway :8001"]
        mg["management :8002"]
        fe["frontend :4200"]
        cs["gateway-consumer"]
        rl["management-relay"]
        rt["gateway-retention"]
    end
```

| Endpoint | URL |
|---|---|
| SPA | <http://localhost:4200> — override with `AIRA_PUBLISH_FRONTEND_PORT` |
| Gateway | <http://localhost:8001> · `/healthz` `/readyz` `/docs` — `AIRA_PUBLISH_GATEWAY_PORT` |
| Management API | <http://localhost:8002> · `/healthz` `/readyz` — `AIRA_PUBLISH_MANAGEMENT_PORT` |
| Keycloak | <http://localhost:8080> (`admin` / `admin`) — `AIRA_PUBLISH_KEYCLOAK_PORT` |
| Grafana (traces) | <http://localhost:3000> — `AIRA_PUBLISH_GRAFANA_PORT` |

**Every published port is a variable**, not only these five: `AIRA_PUBLISH_POSTGRES_PORT`,
`…_REDIS_PORT`, `…_KAFKA_PORT`, `…_SCHEMA_REGISTRY_PORT`, `…_VAULT_PORT`, `…_OTLP_GRPC_PORT`,
`…_OTLP_HTTP_PORT`, `…_OLLAMA_PORT`, `…_KEYCLOAK_HEALTH_PORT`. Set them in
`deploy/compose/.env` or in the environment; `AIRA_BIND_HOST` moves them off `127.0.0.1`. This
exists because a second system on the same machine collided with the defaults and the only way out
was editing the Compose file.

The three application services also still accept their older names — `AIRA_FRONTEND_PORT`,
`AIRA_GATEWAY_PORT`, `AIRA_MANAGEMENT_PORT` — and the `AIRA_PUBLISH_` form wins when both are set.
Prefer the new one: it says *published*, which distinguishes it from `AIRA_POSTGRES_PORT`, a
**setting** naming the port the gateway connects to.

Everything that talks to the stack follows these — the Makefile, `tools/`, the integration suite
and the browser suite all resolve their addresses through `tools/stack_addresses.py`, so moving a
port moves them too. `make test-integration` proves that against `docker compose config` itself.

**When a forwarder sits in front** — `sbx ports … --publish 14200:4200`, a VM, an SSH tunnel —
the container still serves 4200 and the browser says `14200`. Keycloak compares against **the
browser**, so name the visible port: `AIRA_CONSOLE_PORT=14200`. It defaults to the published port,
which is right when nothing remaps it and wrong in exactly the case somebody reaches for when
another system already holds the port.

**The login follows the console's port as well**, which was not true until 2026-08-19: the Keycloak
realm pinned its redirect URIs to a literal `4200`, so moving `AIRA_PUBLISH_FRONTEND_PORT` gave a
console that loaded and a login that failed with *"Invalid parameter: redirect_uri"* — an error
naming the realm, not the port. Keycloak now substitutes the port on realm import. Note that this
happens **at import**, and the import is skipped for a realm that already exists: after moving the
port on a stack that has run before, recreate Keycloak's state
(`docker compose down -v keycloak postgres`, or `make destroy`) or edit the two lists in the
console's client under *Clients → aira-gateway* in the admin console.

> **Reaching the stack from outside this machine** — a sandbox, a VM, another host — needs
> `AIRA_BIND_HOST=0.0.0.0` as well. The default is `127.0.0.1` on purpose (this file publishes
> credentials, and Compose's plain `"4200:4200"` would put them on every interface), so a
> port-forwarder that lands on a non-loopback address finds nothing listening. That is a
> *reachability* failure with a healthy stack behind it, and `docker compose ps` cannot see it.

```bash
make ps            # status and health of every service
make logs-apps     # tail only the application containers
make down-full     # stop, keep volumes
make destroy       # stop and delete volumes (fresh state)
```

### Verifying it

```bash
curl -s localhost:8001/readyz | jq
```

`status: ready` with `degraded: false` means Postgres, Kafka and the counter store all answered.
`degraded: true` with a 200 is a **deliberate** state: the gateway still serves, with a named
control on a fallback ([`CONFIGURATION.md` §6](CONFIGURATION.md#6-what-happens-when-something-is-missing)).

---

## 4. Development — from source, no application containers

Infrastructure in Docker, the six processes from source with reload-on-save.

```bash
make sync              # Python (uv) + frontend (npm) dependencies
make up                # infrastructure only
make seed              # management DB: migrate + demo roles and users
make migrate-gateway   # gateway DB: Alembic migrations
make kafka-topics      # the compacted config topics (auto-create is OFF)
```

Then, each in its own terminal:

```bash
make run-gateway-oidc  # gateway         :8001
make run-backend       # management API  :8002
make consume           # config consumer (long-running)
make run-frontend      # SPA             :4200
```

### Two things that will catch you

**The relay is not running.** After changing members, keys, pipelines, budgets, limits or rules in
the UI, run `make relay` — that is how the gateway learns about them. `make up-full` runs it in a
loop for you.

**Use `run-gateway-oidc`, not `run-gateway`.** The SPA's dry-run and consumption views send the
browser's bearer token to the gateway, which needs OIDC enabled to validate it. Both degrade
gracefully without it, but the views will say they cannot show you anything.

### The test layers

```bash
make ci                # what CI checks: lint + types + unit tests with coverage gates
make test-integration  # against the live stack
make test-e2e          # real browser (Playwright)
make mutants           # break each guarded property and check the tests notice
```

Each layer exists for what the one below cannot see; the reasoning and the traps are in
[`TESTING.md`](TESTING.md).

---

## 5. Integrated — onto your own infrastructure

The applications are stateless containers. Point them at your Postgres, your Kafka, your Keycloak
and your model platforms; nothing else is required.

```mermaid
graph TB
    subgraph yours["Your infrastructure"]
        pg[("PostgreSQL<br/><i>2 databases</i>")]
        kafka[["Kafka<br/><i>8 compacted topics</i>"]]
        kc["Keycloak / OIDC"]
        redis[("Redis<br/><i>optional but recommended</i>")]
        vault["Vault<br/><i>optional</i>"]
        otel["OTLP collector<br/><i>optional</i>"]
    end

    subgraph deploy["What you deploy"]
        gw["gateway<br/><i>N replicas</i>"]
        cs["gateway-consumer<br/><i>1</i>"]
        rt["gateway-retention<br/><i>scheduled</i>"]
        mg["management<br/><i>N replicas</i>"]
        rl["management-relay<br/><i>1</i>"]
        fe["frontend<br/><i>static + proxy</i>"]
    end

    deploy --> yours
```

**Replica counts that matter:** the consumer and the relay are **single-writer** by design — run one
of each. The gateway and Management scale horizontally *provided* Redis is present; without it, rate
limits become per-instance and N replicas allow N × the limit.

**The retention worker must be scheduled** (hourly is what the Compose stack does). If nothing runs
it, nothing is ever deleted and the retention promise is a setting rather than a behaviour.

Everything else — which databases, which topics, which Keycloak clients and roles, which credentials
each model platform needs, TLS, and the SPA's build-time configuration — is in
**[`INTEGRATIONS.md`](INTEGRATIONS.md)**.

### Building the images

```bash
make build-images      # aira-gateway:dev, aira-management:dev, aira-frontend:dev
```

The SPA is configured **at deployment time**: `public/runtime-config.js` ships beside the bundle
and names the OIDC issuer and client id, so one image serves any realm — replace that one file and
set `AIRA_CSP_CONNECT_SRC` to the same issuer's origin.
[`INTEGRATIONS.md` §7](INTEGRATIONS.md#7-the-spa-is-configured-at-deployment-time).

---

## 6. Upgrading

```mermaid
graph LR
    a["1 · migrate<br/><i>both databases</i>"] --> b["2 · topics<br/><i>idempotent</i>"] --> c["3 · consumer"] --> d["4 · gateway"] --> e["5 · management + relay"] --> f["6 · frontend"]
```

Order matters in one place: **migrate before the new code starts**. Two hazards this project has
actually hit:

- An old container's `create_all` **resurrected a dropped table** and then failed every event
  against it. `create_all` alongside Alembic means a partially-deployed stack can undo a migration —
  production deployments run migrations as a one-shot job and nothing else creates tables.
- A new topic that nothing creates fails **silently**: Management accepts the change, the relay
  publishes, the broker drops it, and no error reaches anybody. Run the topic step on every upgrade;
  it is idempotent, and a test keeps the list in step with the code.

---

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| Configuration changes never reach the gateway | the relay is not running (`make relay`), or a topic is missing (`make kafka-topics`) |
| `/readyz` says `degraded: true` | Redis is unreachable — limits are per-instance, budgets use the Postgres path |
| The SPA shows "invalid credentials" everywhere | it should not any more; if it does, the session ended and the redirect failed — check the browser console for the OIDC error |
| A model answers `404 not found` | it is not in the catalog, or the name is wrong; `GET /v1beta/models` lists what this deployment serves |
| Keycloak has none of the AIRA roles | the realm is imported **only if it does not already exist** — recreate it, see [`deploy/compose/README.md`](../deploy/compose/README.md) |
| Nothing is ever deleted from `request_logs` payloads | the retention worker is not scheduled |
| A rule is configured and finds nothing | check `anomaly_rules` in the gateway database — if it is empty, the topic or the relay is the problem |
