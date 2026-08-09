# Integrating with your infrastructure

What each connected system must provide, which credentials AIRA needs, and which settings on **your**
side matter. One section per system; each ends with a short checklist.

Configuration values are named here and defined in [`CONFIGURATION.md`](CONFIGURATION.md). How to
run the processes is [`SETUP.md`](SETUP.md).

```mermaid
graph LR
    subgraph required["Required"]
        pg[("PostgreSQL 16+")]
        kafka[["Kafka"]]
        kc["Keycloak / OIDC"]
        model["≥1 model platform"]
    end
    subgraph recommended["Strongly recommended"]
        redis[("Redis")]
        otel["OTLP collector"]
    end
    subgraph optional["Optional"]
        vault["Vault"]
    end
    aira["AIRA Gateway"] --> required
    aira --> recommended
    aira --> optional
```

---

## 1. PostgreSQL

**Two databases**, one per plane. They are not interchangeable.

| Database | Owner | Holds |
|---|---|---|
| `aira_mgmt` | Management | configuration: use cases, memberships, hashed API keys, pipelines, budgets, limits, rules, model catalog, outbox |
| `aira_gateway` | Gateway | read-model of the above, plus `request_logs`, `anomaly_events`, `access_suspensions`, `budget_usage` |

**What you provide**

- PostgreSQL **16 or newer** (the code uses `JSONB`, partial indexes and `now()` server defaults).
- One role with `CREATE` on both databases — migrations create and alter tables.
- No extensions beyond the defaults.

**Sizing.** `request_logs` is the one table that grows with traffic: one row per request, plus one
per pipeline model call. Payloads (`request_payload`, `response_payload`) dominate its size and are
deleted on the retention clock; the metadata is kept, because that is what the spend history is made
of. If you set `AIRA_LOG_RETENTION_DAYS`, you are also truncating the reporting horizon — the
default of `0` (never) is deliberate.

**Migrations** are Alembic (gateway) and Django (Management), run as one-shot jobs **before** the
new code starts. Nothing else may create tables: an old container's `create_all` once resurrected a
dropped table and then failed every event against it.

> done two databases · a role that may migrate · migrations as a pre-deploy job · a plan for
> `request_logs` growth

---

## 2. Keycloak (or another OIDC provider)

AIRA needs **one realm**, three role groups, whatever groups your organisation already uses, and
two clients.

### Roles come from groups (`ADR-0017`)

**Group membership is the only source of a role.** Three groups carry the organisation-wide roles,
and you tell AIRA which is which:

```
AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung
```

| Role | Means |
|---|---|
| `global-admin` | everything; the only role that may create a use case or price a model |
| `it-steuerung` | sees every use case and every figure; **writes nothing** |
| `it-security` | sees every use case's configuration; may author global anomaly rules and stop traffic |

The paths are yours — `/it/security-team` is as good as `/aira/it-security`, and several groups may
confer one role (`it-security=/a/one,/b/two`). The match is **exact**: a sub-group does not inherit,
and `/aira/global-admins-readonly` confers nothing.

> **A Keycloak realm role grants nothing.** AIRA does not read `realm_access.roles`. Assigning
> `global-admin` directly to a person has no effect — which is the point: there is exactly one place
> a role is granted, so there is exactly one place to audit and one place to revoke.

Outside `local`, Management **refuses to start** when no group is named for `global-admin`: an
installation nobody can administer cannot be repaired through its own console.

**There is no `use-case-admin` or `use-case-user` role.** Administering or belonging to a use case
is a grant on *that* use case — a Global Administrator creates it and names the group that
administers it; administrators name the groups that are its members. See the next section.

### Groups — who reaches which use case

**Your group names are yours.** A use case's access list names whatever paths your realm actually
uses — `/abteilungen/vertrieb/nord`, `/ai/kundenservice` — and AIRA grants them a role
(`user` or `admin`) in the console. Somebody joining or leaving the group takes effect on their
next token, without anybody editing a list here. See
[`FRD-209`](features/FRD-209-access-by-group.md).

A naming convention also still works, and needs no configuration at all:

```
/use-cases/<slug>
```

A user in `/use-cases/kundenservice` reaches that use case by the name alone. It is one route in,
not the only one, and the two are a **union** — where the roles differ, the stronger wins.

**AIRA never writes to your directory.** It does not create groups, add people to them or delete
them. Group membership stays your system's answer.

#### The one setting this depends on

> **The access token must carry a `groups` claim, with full paths.**

Without it a group grant reaches nobody — silently, because a token with no groups is
indistinguishable from a token whose owner is in none. Add a **group-membership** protocol mapper
to every client whose tokens AIRA sees, including service accounts:

| Field | Value |
|---|---|
| Mapper type | Group Membership |
| Token Claim Name | `groups` |
| Full group path | **on** |
| Add to access token | on |

The dev realm has it on the SPA client and on the test service accounts. A realm that has it on
only some of them produces a feature that works for some callers and not others, which is the
hardest shape of all to diagnose.

#### Searching your directory (optional)

To let the console **search** your groups and users when granting access, give AIRA a read-only
service account and set `AIRA_DIRECTORY_CLIENT_ID` / `AIRA_DIRECTORY_CLIENT_SECRET`. It needs
`view-users` and `query-groups` on the realm — nothing else, and it is never used for anything but
a search.

Without it the console still works: it offers the people who have signed in and the groups already
granted somewhere, and **says that is what it is showing**. It cannot invent a group nobody has
used, so on a fresh installation the first grant of a new group has to be typed.

### Clients

| Client | Type | Used by | Needs |
|---|---|---|---|
| `aira-gateway` | public, **PKCE S256** | the SPA and the gateway's JWKS validation | exact redirect URIs and web origins — never `*` |
| *(service accounts)* | confidential | machine-to-machine callers | `client_credentials`, realm roles as needed |

**Settings on your side that matter**

- **Direct access grants (password grant): off.** AIRA does not use it, and enabling it weakens the
  realm for a convenience a machine-to-machine grant already provides.
- **Redirect URIs and web origins: pinned** to your SPA origin. A wildcard lets an attacker redirect
  the authorization code to a site they control.
- **Do not request `offline_access`.** The code flow already returns a refresh token;
  `offline_access` asks for one that outlives the SSO session, which a governance console must not
  hold — and if the realm forbids offline tokens, the code-to-token exchange fails with an error
  Keycloak returns *without* CORS headers, so the browser reports something that names neither the
  scope nor the setting.
- **Client description length**: Keycloak stores it in `varchar(255)`. A longer one breaks the
  realm import, and the failure looks like Keycloak not starting.

> done three role groups named in `AIRA_ROLE_GROUPS` · a **`groups` claim with full paths on
> every client, including service accounts** — without it nobody holds any role at all · a public
> PKCE client
> with pinned URIs · password grant off · issuer reachable from both services · *(optional)* a
> read-only service account for directory search

---

## 3. Apache Kafka

Configuration flows Management → Kafka → gateway. **Eight compacted topics**, one per entity kind:

| Topic | Carries |
|---|---|
| `aira.usecases` | use cases |
| `aira.memberships` | control-plane memberships |
| `aira.api-keys` | key hashes and revocations |
| `aira.pipelines` | pipeline configuration |
| `aira.budgets` | budgets |
| `aira.rate-limits` | rate limits |
| `aira.models` | the model catalog, prices and capabilities |
| `aira.anomaly-rules` | anomaly rules |

**What you provide**

- `cleanup.policy=compact` on each. Compaction is what makes the read-model rebuildable: the latest
  value per key is the state.
- **Create them explicitly.** If auto-creation is off — as it should be — a missing topic fails
  **silently**: Management accepts the change, the relay publishes, the broker drops it, and no
  error reaches anybody. The only trace is a line in a consumer log. This has happened twice here,
  which is why `make kafka-topics` is idempotent and a test keeps the list in step with the code.
- Retention: compaction handles it; no time-based retention is needed.
- One partition per topic is sufficient. Ordering per key is what matters, and volume is
  configuration changes rather than traffic.

**Single-writer processes.** Run exactly **one** relay and **one** consumer. They are not
horizontally scalable and do not need to be.

### The bus is a trust boundary

> **Anything that can publish to these topics can grant itself access.** The gateway applies what
> arrives straight into the read-model its authorization is derived from — that is what makes
> `FRD-204`'s idempotent consumer simple, and it is only safe if the broker is authenticated. A
> publisher who is not AIRA can send `api_key.created` with a hash of their choosing, or
> `use_case_group.granted` naming a group they belong to, and hold administrator access to any use
> case. No credential is presented and **no audit row is written**, because from the gateway's side
> nothing unusual happened: configuration arrived, exactly as configuration does.

So both planes take a broker identity, and both **refuse to start on `PLAINTEXT` outside `local`**:

```
AIRA_KAFKA_SECURITY_PROTOCOL=SASL_SSL
AIRA_KAFKA_SASL_MECHANISM=SCRAM-SHA-512    # default when unset; PLAIN sends the password in clear
AIRA_KAFKA_SASL_USERNAME=aira-gateway      # a separate identity per service
AIRA_KAFKA_SASL_PASSWORD=…                 # from Vault
AIRA_KAFKA_SSL_CAFILE=/etc/ssl/private-ca.pem   # only for a private CA
```

**What you provide**: one broker principal per service (relay and consumer), each with `Write` on
the eight topics for the relay and `Read` for the consumer — nothing wider. ACLs are what make the
identity worth having; an authenticated principal that may publish to any topic is the same hole
with a login.

> done eight compacted topics, created explicitly · one relay · one consumer · **a broker identity
> per service and ACLs scoped to these topics** · bootstrap servers reachable from both planes

---

### If a reverse proxy sits in front of the gateway

`AIRA_TRUST_FORWARDED_FOR=true` makes the gateway read the caller's address from
`X-Forwarded-For`, and `AIRA_TRUSTED_PROXY_HOPS` says how many proxies append to it (**1** for a
single nginx, which is what this repository ships).

The address is read that many entries **from the right**. The left end is whatever the *caller*
sent: a proxy appends, so `X-Forwarded-For: 10.9.9.9` from a client arrives as
`10.9.9.9, <real address>`. Getting this wrong is not cosmetic — the value lands on every audit
row, it is what `FRD-505`'s incident view filters by, and it is the key the failed-authentication
bound counts against, so a caller who could choose it could also rotate it and never be bounded.

Set the hop count to match your topology. A chain **shorter** than it is treated as not having
come through those proxies at all, and the socket peer is used instead.

## 4. Redis

Optional to start, **required to scale**. It holds rate-limit buckets and budget reservations —
values written on *every* request, which is what earns it a place.

Without it, rate limits become **per-instance**: N replicas behind a load balancer allow N × the
limit. Budgets fall back to the Postgres path, which enforces but is racy.
`/readyz` reports `degraded: true` and keeps serving.

**What you provide**: any Redis 6+ reachable at `AIRA_REDIS_URL`. No persistence is required —
counters seed from Postgres on a miss and expire in five minutes so drift cannot outlive a period.

> done one Redis, or accept per-instance limits and one gateway replica

---

## 5. Model platforms

At least one. Each is registered **only when configured**, and each is audited under its own
provider, publisher and region.

### Google Vertex AI — Gemini and Anthropic, EU-regional

| You provide | Why |
|---|---|
| A GCP **project** with Vertex AI enabled | |
| A **service account** with `roles/aiplatform.user` | The narrowest role that can call `:generateContent` and `:rawPredict` |
| Its **JSON key**, in Vault or a mounted file | `AIRA_VERTEX_CREDENTIALS` |
| The **regions** you are permitted to use | Listed in `AIRA_ALLOWED_REGIONS`; a model outside them refuses to start |
| **Model Garden access** for the publishers you want | Anthropic models need to be enabled in your project |

One transport, two dialects. Anthropic on Vertex uses `:rawPredict` with the Messages API:
`max_tokens` is always sent, thinking blocks are **dropped and never persisted**, cache tokens count
as input, and there are no embeddings.

### Microsoft Foundry / Azure OpenAI

| You provide | Why |
|---|---|
| An **Azure OpenAI resource** endpoint | `AIRA_FOUNDRY_ENDPOINT` |
| An **API key** or Entra credentials | The Entra token is minted per request, not captured once |
| One **deployment per model**, with its region | `AIRA_FOUNDRY_DEPLOYMENTS` as `model=deployment@region` |

An Azure **deployment name is not a model name**. Attributing a response to the deployment would not
fail — the spend figure would quietly stop being complete, because a deployment has no price. AIRA
keeps both and prices the underlying model.

### Self-hosted, OpenAI-compatible (Ollama, vLLM, …)

| You provide | Why |
|---|---|
| One or more **named servers** | `AIRA_OPENAI_SERVERS` as `name=url\|models\|embeddings\|region`; a fleet is several machines and each is audited by name |
| A **region label**, if the deployment claims one | Checked at startup against the residency policy |

Two traps this dialect hides, both handled: usage arrives in a chunk with an **empty `choices`
array**, and the vendor reports **no stream usage** unless `stream_options.include_usage` is sent —
a stream reporting none would be released rather than settled, and every streamed request would be
silently free.

### Google Gemini public API

An API key (`AIRA_GOOGLE_API_KEY`). Simplest to start with, and the one to avoid where residency
matters.

> done credentials in Vault or a mounted secret · regions on the allow-list · every model that will be
> served declared in the catalog with a **price** and its **capabilities**

---

## 6. Observability

OTLP over HTTP to any collector. Traces carry `aira.*` attributes — subject, use case, model,
outcome, tokens, cost, provider/publisher/region — so a trace can be filtered by any of them.

`?key=` is redacted from spans; credentials never appear in a log line. `x-trace-id` is on **every**
response including failures, which are the ones most worth correlating.

> done an OTLP endpoint · `AIRA_OTEL_ENABLED=true` · a sample ratio you can afford

---

## 7. The SPA is configured at build time

The Angular bundle contains its OIDC issuer and client id. There is no run-time configuration file,
so **deploying the SPA means building it** for your realm:

```bash
# management/frontend/src/environments/  (or the equivalent in your pipeline)
issuer:   https://sso.example.com/realms/aira
clientId: aira-gateway
```

At run time nginx needs the two proxy targets:

| Path | Goes to |
|---|---|
| `/api` | Management |
| `/gw` | Gateway |

Its resolver must **re-resolve** upstreams. With a name resolved once at start-up, a gateway
container restarting behind a new IP produces a 502 that outlives the restart.

The bundle sets a CSP and uses `requireHttps: 'remoteOnly'`, so it works on `localhost` over HTTP
and demands HTTPS everywhere else.

> done build per environment · both proxies · a resolver that re-resolves · TLS in front

---

## 8. Reverse proxy and TLS

Terminate TLS in front of both APIs. Two things AIRA needs from the proxy:

- **`X-Forwarded-For`**, if you want real client addresses in the audit trail — and then set
  `AIRA_TRUST_FORWARDED_FOR=true`. Leave it off otherwise: with it on and no trusted proxy, any
  caller can write any address into your audit trail.
- **No buffering on streaming responses.** `:streamGenerateContent` is SSE; a proxy that buffers it
  turns a stream into a single late response.

Timeouts should exceed your slowest model. A self-deployed model cold-starting can take minutes,
which is why its own timeout defaults to 300 seconds.

> done TLS · `X-Forwarded-For` matched by the setting · SSE unbuffered · generous timeouts

---

## 9. What a first integrated deployment looks like

```mermaid
graph TB
    subgraph day1["Day 1 — it runs"]
        a1["Postgres: 2 databases"]
        a2["Keycloak: realm, roles, groups, client"]
        a3["Kafka: 8 compacted topics"]
        a4["1 model platform"]
        a5["Deploy: gateway, consumer, management, relay, frontend"]
    end
    subgraph day2["Day 2 — it is governed"]
        b1["Redis: real rate limits across replicas"]
        b2["Model catalog: prices + capabilities"]
        b3["Retention worker: scheduled"]
        b4["OTLP: traces"]
        b5["Vault: credentials out of the environment"]
    end
    subgraph day3["Day 3 — it is defensible"]
        c1["Budgets and rate limits per use case"]
        c2["Anomaly rules, in <b>alert</b> first"]
        c3["AIRA_REQUIRE_USE_CASE=true"]
        c4["Promote rules to throttle / block"]
    end
    day1 --> day2 --> day3
```

The order is not arbitrary. **Prices before budgets** — an unpriced model makes a cost budget
unenforceable, and unpriced traffic is counted apart rather than as zero. **Alert before block** —
a rule is a hypothesis until somebody has watched it be right, and a detection system that blocks
wrongly once is switched off forever. **`REQUIRE_USE_CASE` last** — turning it on before every
caller is migrated refuses traffic that used to work.

### A group for model testing

Model tests (`FRD-504`) are booked to a dedicated use case, `smoke-test`, which the seed creates.
Because AIRA never writes to your directory, whoever may test a model must be able to *call* that
use case, and the gateway decides that from the groups their token carries:

- Create a group whose path is **`/use-cases/smoke-test`** (or grant an existing group to that use
  case in the console, which works the same way).
- Put the people who may test models in it — by default Global Administrators, IT Security and
  use-case administrators.

Without it the console does not offer a Run button and says why. Seeing every use case is
deliberately not the same as being able to call one (`ADR-0007`), so an oversight role does **not**
get this by virtue of its role.
