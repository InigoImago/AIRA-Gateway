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

## 0. When it does not work: watching the wire

Read this before section 1, because it is what turns the rest of this document into a loop you can
close. Every system below is reached over a network, with credentials, and every one of them fails
silently in at least one of its modes. `AIRA_DEBUG_INTEGRATIONS` makes each call say what it did:

```bash
AIRA_DEBUG_INTEGRATIONS=all      # while you are wiring something up
AIRA_DEBUG_INTEGRATIONS=kafka    # or just the one you are working on
AIRA_DEBUG_INTEGRATIONS=         # off — and off is what a working installation runs
```

One line per call, on stdout with the rest of the service log — what went where, how long it took,
and the far end's own words when it failed:

```json
{"system": "kafka", "operation": "producer.start", "outcome": "failed", "duration_ms": 64.5,
 "target": "broker-a:9093", "protocol": "SASL_SSL", "error_type": "KafkaConnectionError",
 "error": "Unable to bootstrap from [('broker-a', 9093, AddressFamily.AF_INET)]",
 "event": "integration_call", "level": "warning"}
```

`outcome` separates **`timeout`** from **`failed`** deliberately: a refusal is a wrong address, port
or credential and is fixed in configuration; a timeout is a firewall, a hung process or a route that
goes nowhere and is fixed by looking at the network. No line carries a payload, a token or a secret
value, and addresses are redacted in both places a credential hides in one.

Names: `otel`, `kafka`, `auth`, `vault`, `redis`, `postgres`, or `all`. A name this build does not
know refuses the process at start-up rather than watching nothing. Full reference:
[`CONFIGURATION.md` §1a](CONFIGURATION.md#1a-watching-a-call-to-another-system);
the reasoning: [`FRD-617`](features/FRD-617-watching-the-wire-to-another-system.md),
[`ADR-0022`](adr/ADR-0022-a-call-to-another-system-says-so.md).

For the leg **after** the collector — what your OTLP collector managed to forward onward — the
counters are in `make lab-status` (§6).

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

A user in `/use-cases/kundenservice` reaches that use case by the name alone. It is one route of
**three**, and they are a **union** — where the roles differ, the stronger wins:

1. this convention, resolvable from the token by itself;
2. a grant to **any group path your realm already uses** — `/abteilungen/vertrieb/nord`, whatever
   it is. AIRA imposes no naming convention on somebody else's directory;
3. a grant naming **one person**, for the case where no group fits.

You do not need a group per use case. Route 1 exists because it needs no configuration at all and
is a perfectly good way to run a small installation.

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

#### And an audience claim, which Keycloak does not add by itself

> **The access token's `aud` must carry whatever you set as `AIRA_OIDC_AUDIENCE`.**

Outside `AIRA_ENVIRONMENT=local` the gateway and Management both **refuse to start** without an
audience named ([`ADR-0015`](adr/ADR-0015-a-convenience-default-is-a-production-default.md)): without one, any token
the realm ever minted is accepted, including one issued to a different client. But a Keycloak access
token for a public client carries `aud: ["account"]` and **not** the client id, so the value has to
be put there deliberately:

| Field | Value |
|---|---|
| Mapper type | Audience |
| Included Client Audience | the value you will configure as `AIRA_OIDC_AUDIENCE` |
| Add to access token | on |

A client scope does the same job and is the better place for it when several clients need it.

The dev realm has **no** audience mapper and needs none — it runs as `local`, where the check is
skipped so that a laptop works against a realm nobody configured. That is why this requirement was
missing from this list until an integration meeting asked for it: the gap is invisible from inside
this stack and surfaces on somebody else's Keycloak, as *every token rejected with 401 against a
realm that looks entirely correct*.

#### And one that decides whose allowance is whose

> **`preferred_username` should be in the access token** — Keycloak puts it there by default.

A per-head budget or rate limit is counted against the **person**, so that an API key and a browser
session by the same human share one allowance rather than getting two
([`ADR-0019`](adr/ADR-0019-an-allowance-belongs-to-a-person.md)). The name in the token is what
joins them: an API key's identity already *is* its owner's username, and a token's subject is a
directory id that resembles nothing.

Without the claim nothing breaks and nothing is silently shared: that caller's allowance keys on
their subject, which is stable and unique — they simply get a second one for their key. A service
account, which has no person behind it, is exactly this case and is right to be.

#### The username has to be one Keycloak does not let people change

> **Leave *Edit username* off in the realm** (Realm settings → Login). It is off by default.

This is the one assumption AIRA makes about your directory that it cannot check for you, so it is
written down rather than left implied. The name in the token is not only a label:

| What it decides | Where |
| --- | --- |
| which use cases a caller reaches, where a grant names a **person** rather than a group | `FRD-209` §2.1 |
| whose per-head budget and rate limit the request counts against | [`ADR-0019`](adr/ADR-0019-an-allowance-belongs-to-a-person.md) |
| whether the payload view treats somebody as an administrator of their use case | `FRD-505` |
| whether a **kill switch** aimed at a person stops both of their credentials | `FRD-613` §3 |
| which invited Django account a new `sub` claims on first sign-in | `ADR-0007`, `FRD-613` |

With *Edit username* on, a user can rename themselves to a colleague's name and inherit each of
those. Everything else AIRA reads from a token — the subject, the groups — is Keycloak's to set and
nobody else's, and the username is the exception because a realm can be configured to hand it to
the user.

AIRA does not check the setting: reading it needs the optional admin client below, so a check
would pass silently on every installation that has not configured one — a control that is present
on paper and absent in practice, which this project refuses to ship. If your realm must allow
username editing, grant access by **group** only and expect one person's allowances to follow the
name rather than the human.

#### To stop a person, name them by their username

> **A suspension targeting a `subject` must carry the name, not the directory id.**

An API key never carries a `sub` — Management issues it against a *username*, so that is the only
identity on the wire — while a browser session carries both. A suspension typed as a directory id
therefore stops that person's **tokens** and serves their key, which is a kill switch that appears
to work. Typed as the username it stops both.

The trace list shows the name in its own column beside the subject, which is the value to copy.
This is not something the gateway can close for you: joining a directory id to a username needs a
question put to the directory, and the request path may not ask one ([`FRD-204`](features/FRD-204-config-distribution-kafka.md)).

#### Searching your directory (optional)

To let the console **search** your groups and users when granting access, give AIRA a read-only
service account and set `AIRA_DIRECTORY_CLIENT_ID` / `AIRA_DIRECTORY_CLIENT_SECRET`. It needs
`view-users` and `query-groups` on the realm — nothing else, and it is never used for anything but
a search.

Without it the console still works, with one real limit. It offers the people who have signed in
and the groups already granted somewhere, and **says that is what it is showing**; it cannot invent
a group nobody has used, so on a fresh installation the first grant of a new group has to be typed.

> **And granting a *person* who has never signed in needs this client.** AIRA creates their account
> — with an invitation their first token claims (`FRD-613`) — only for a username the directory
> confirms, because an account created for a name nobody has is an accountability chain ending in
> a string. Without the client the console says so and names both ways out: configure it, or have
> the colleague sign in once, which creates the account. Granting a **group** needs none of this
> and is unaffected.

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
> every client, including service accounts** — without it nobody holds any role at all · an
> **audience mapper**, without which the services do not start · a public PKCE client
> with pinned URIs · password grant off · issuer reachable from both services **at the same URL the
> browser uses** · *(optional)* a read-only service account for directory search

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

### Google Agent Platform (formerly Vertex AI) — Gemini and Anthropic, EU-regional

> **The product was renamed.** Vertex AI is now
> [Gemini Enterprise Agent Platform](https://cloud.google.com/products/gemini-enterprise-agent-platform);
> Agent Engine became Agent Runtime, Memory Bank became Agent Platform Memory Bank
> ([name changes](https://docs.cloud.google.com/gemini-enterprise-agent-platform/vertex-ai-name-changes)).
> The REST hosts, paths and credentials this adapter uses are unchanged, so the settings keep the
> `AIRA_VERTEX_*` names — renaming a configuration key breaks every deployment that has one, and
> buys a word.

| You provide | Why |
|---|---|
| A GCP **project** with the Agent Platform (Vertex AI) API enabled | |
| **Either** a service account with `roles/aiplatform.user` **or** an API key | Two credentials, one adapter — see below |
| The credential, from the environment **or Vault** | `AIRA_VERTEX_CREDENTIALS` (JSON) or `AIRA_VERTEX_API_KEY` |
| The **regions** you are permitted to use | Listed in `AIRA_ALLOWED_REGIONS`; a model outside them refuses to start, and since `FRD-611` the console refuses one **as it is typed**, naming what is allowed. `global` is not in the shipped default: it names no region and guarantees none, so an installation that wants it says so out loud |
| **Model Garden access** for the publishers you want | Anthropic models need to be enabled in your project |

One transport, two dialects. Anthropic on Vertex uses `:rawPredict` with the Messages API:
`max_tokens` is always sent, thinking blocks are **dropped and never persisted**, cache tokens count
as input, and there are no embeddings.

#### Which credential — and why it is not a free choice

|  | Service account (**recommended**) | API key |
|---|---|---|
| Setting | `AIRA_VERTEX_CREDENTIALS` | `AIRA_VERTEX_API_KEY` |
| How it authenticates | signed JWT exchanged for a short-lived token | `x-goog-api-key`, sent as-is |
| Rotation | rotate the key in IAM; nothing else moves | replace the string everywhere it is stored |
| Scope | one IAM role, auditable per principal in Cloud Logging | whatever the key is restricted to |
| Region | locational host, real residency | **the same** — see the note below |

Set both and the **service account wins**: a deployment that has one has made the more deliberate
choice, and silently preferring a key left in the environment would be a downgrade nobody asked for.

> **The residency footnote that matters.** Google's own *express mode* documents the **global**
> endpoint `aiplatform.googleapis.com`, and its
> [data-residency page](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/data-residency)
> is explicit that global endpoints "route and process data anywhere globally… you can't control or
> know which region your ML processing requests are sent to". **AIRA does not use that endpoint.**
> The same API key was measured to answer on the *locational* host
> (`europe-west1-aiplatform.googleapis.com`, both path forms), so this adapter keeps its regional
> hosts and its per-model region check, and an API key gets the same residency a service account
> does. If you point anything else at the global endpoint, that guarantee is gone.

#### B — the service account, step by step

Console, or the equivalent `gcloud`. Replace `PROJECT` with your project id.

1. **Enable the API.** Console → *APIs & Services → Library* → “Vertex AI API” (listed under Agent
   Platform after the rename) → **Enable**.
   `gcloud services enable aiplatform.googleapis.com --project PROJECT`
2. **Create the service account.** *IAM & Admin → Service Accounts → Create*. A name like
   `aira-gateway`. No project role at creation.
   `gcloud iam service-accounts create aira-gateway --project PROJECT`
3. **Grant exactly one role.** `roles/aiplatform.user` — the narrowest that can call
   `:generateContent` and `:rawPredict`. Not `roles/editor`.
   ```
   gcloud projects add-iam-policy-binding PROJECT \
     --member "serviceAccount:aira-gateway@PROJECT.iam.gserviceaccount.com" \
     --role roles/aiplatform.user
   ```
4. **Create a JSON key.** *Keys → Add key → Create new key → JSON*. It downloads once.
   `gcloud iam service-accounts keys create key.json --iam-account aira-gateway@PROJECT.iam.gserviceaccount.com`
5. **Store it** — the whole file, as one line, in Vault (below) or in the environment:
   `AIRA_VERTEX_CREDENTIALS='{"client_email":"…","private_key":"-----BEGIN PRIVATE KEY-----\n…"}'`
6. **Name the project and the models:**
   ```
   AIRA_VERTEX_PROJECT=PROJECT
   AIRA_VERTEX_MODELS=europe-west1/google/gemini-2.5-flash,europe-west1/google/text-embedding-005
   AIRA_ALLOWED_REGIONS=europe-west1,europe-west4,eu
   ```
   The form is `region/publisher/model`: **region per model, not per process** (`FRD-115` FR-4).
7. **Start.** A malformed credential, or a model in a region the list does not permit, is a
   **startup** failure with the reason named — deliberately, because a gateway that starts and then
   fails every request looks like an outage at Google.

Reference: [Authenticate to Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/authentication)
· [Deployments and endpoints](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/locations)

#### C — the API key, step by step

1. **Get the key.** Console → *APIs & Services → Credentials* → the key created for you, or
   *Create credentials → API key*
   ([docs](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start/api-keys)).
2. **Check its API restrictions.** This is the step that bites: a key restricted to the Agent
   Platform API answers `403 API_KEY_SERVICE_BLOCKED` on any other Google API, and a key restricted
   to something else answers the same here. *Credentials → your key → API restrictions.*
3. **Store it** as `AIRA_VERTEX_API_KEY`, from the environment or Vault.
4. **The rest is identical to B** — `AIRA_VERTEX_PROJECT`, `AIRA_VERTEX_MODELS`,
   `AIRA_ALLOWED_REGIONS`. Same hosts, same paths, same residency check; only the header differs.

#### Both credentials from Vault (`FRD-116`)

Any `AIRA_*` setting can come from Vault — the settings source is generic — and **Vault wins over
the environment** where both hold a value. Write them under the configured path:

```bash
vault kv put secret/aira \
  AIRA_VERTEX_API_KEY='AQ.…' \
  AIRA_VERTEX_CREDENTIALS='{"client_email":"…","private_key":"-----BEGIN PRIVATE KEY-----\n…"}'
```

Verified against a running Vault, not assumed: both arrive, and the multi-line PEM survives the
round trip intact. The startup log names **which keys** were loaded and never their values
(`vault_secrets_loaded … keys=[AIRA_VERTEX_API_KEY, AIRA_VERTEX_CREDENTIALS]`).

A configured Vault that cannot be read is a **boot failure**, never a quiet fallback to the
environment — otherwise a deployment whose Vault is down silently runs on whatever stale value the
environment happens to hold.

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

### Google AI Studio (the Gemini public API)

An API key (`AIRA_GOOGLE_API_KEY`). Simplest to start with, and the one to avoid where residency
matters: `generativelanguage.googleapis.com` names no region and guarantees none, so AIRA records it
as `global` — a value the EU default does **not** contain. A deployment that wants it says `global`
in `AIRA_ALLOWED_REGIONS` out loud, which turns "may we send data there" from something somebody
remembers into a line of configuration and a region on every audit row.

`AIRA_GEMINI_MODELS` may be left empty: cataloguing a model under this provider is enough to serve
it (`FRD-507`).

### Which platforms can be asked what they offer

The console can ask a provider for its model list and start a catalog entry from one of them. That
is a property of the platform, not a setting:

| Platform | Can be asked | Cataloguing alone reaches it |
|---|---|---|
| Google AI Studio | yes | yes |
| Self-hosted, OpenAI-compatible | yes | yes |
| Microsoft Foundry / Azure | no | no — each model needs a **deployment** first |
| Google Vertex AI | no | no — two adapters serve one provider name |

A platform that cannot be asked is not misconfigured. Its models are named in the gateway's
configuration or typed into the catalog, and the editor's reachability check is what says whether
anything serves them. Nothing a vendor lists is ever catalogued automatically: a listing is not an
approval (`FRD-307`), a capability flag is a claim rather than evidence (`FRD-131`), and no listing
publishes a price.

> done credentials in Vault or a mounted secret · regions on the allow-list · every model that will be
> served declared in the catalog with a **price** and its **capabilities**

---

## 6. Observability

OTLP over HTTP to any collector. Traces carry `aira.*` attributes — subject, use case, model,
outcome, tokens, cost, provider/publisher/region — so a trace can be filtered by any of them.

`?key=` is redacted from spans; credentials never appear in a log line. `x-trace-id` is on **every**
response including failures, which are the ones most worth correlating.

### The two legs, and which one you can change

```
  gateway ─── application/x-protobuf ──▶ collector ─── your choice ──▶ wherever you send it
  management        (fixed)                                              (protobuf or JSON)
```

**Leg 1, the applications to the collector, is protobuf and is not switchable.** Measured on the
running stack: `Content-Type: application/x-protobuf`. This is not a setting anybody withheld —
the OTLP spec defines an `http/json` protocol and `opentelemetry-exporter-otlp-proto-http`, the
package both planes use, does not implement it. `AIRA_OTEL_ENDPOINT` chooses *where*; nothing
chooses the encoding. If your destination cannot read protobuf, put a collector in front of it;
that is what the second leg is for.

**Leg 2, the collector onward, is yours.** One line on an `otlphttp` exporter:

```yaml
exporters:
  otlphttp/siem:
    endpoint: https://siem.internal:4318
    encoding: json          # ← this is the switch. Default is protobuf.
```

The shipped stack already carries a worked example: `deploy/compose/otel/collector-config.lab.yaml`
fans out to a second destination in JSON, and `make up-lab LAB_SIEM_ENDPOINT=…` starts it without
touching the reference stack ([§0](#0-when-it-does-not-work-watching-the-wire) for the debug
channel, `make lab-status` for whether it arrived).

### What "OTLP/JSON" actually is, before you plan around it

Measured through the overlay above into a throwaway listener:

```json
{"resourceSpans": [{"resource": {"attributes": [{"key": "service.name",
 "value": {"stringValue": "aira-gateway"}}]}, "scopeSpans": [{"spans": [ … ]}]}]}
```

`Content-Type: application/json`, and the body is the **protobuf-JSON mapping**: one nested
document per batch, `resourceSpans[].scopeSpans[].spans[]`, with every attribute as a
`{"key": …, "value": {"stringValue": …}}` pair. It is **not** one flat event per line, and a SIEM
that ingests newline-delimited JSON will not read it as events without a transform. Answer that
question before wiring it up — [`FRD-616`](features/FRD-616-the-audit-trail-as-an-event-stream.md)
says what the content is and, more usefully, what it is not.

If a flat shape is what you need, the collector is where to produce it: this build ships the
`file`, `kafka`, `elasticsearch`, `opensearch`, `splunk_hec` and `syslog` exporters alongside
`otlphttp`, and those speak their destinations' own formats rather than OTLP.

### Did it arrive? Three hops, three answers

A service can only report what its own exporter saw, and that is less than it looks: OTLP defines
a **partial success** — the collector answers `200` with a body saying it dropped part of the batch
— and the Python exporter reads the status and throws the body away. So a green line can be about
telemetry that was discarded.

```
  application ──▶ collector ──▶ Grafana / your SIEM
     │               │                  │
     │               │                  └── make otel-status   forwarded / undelivered
     │               └── make otel-status   accepted / refused
     └── AIRA_DEBUG_INTEGRATIONS=otel      left, took Nms, answered 200, N rejected
```

```console
$ make otel-status
signal           accepted   refused  forwarded  undelivered
log_records            19         0         38            0
spans                  65         0        130            0
```

`forwarded` is summed across exporters, so a stack fanning out to two destinations counts each
batch twice — that is why the reference stack shows double. `undelivered` counts a *give-up*, not
an attempt, so an exporter still retrying a dead endpoint reads `0` for as long as its backoff
lasts; `make lab-status` prints the collector's own words about why.

### When the receiver answers `429`

Measured on this stack, so the numbers are a starting point rather than a rule of thumb:

| | |
|---|---|
| Spans one gateway request produces | **17.5** (server span, httpx client spans, SQLAlchemy spans) |
| Applications → collector | one POST per signal per **5 seconds**, up to **512** spans in it |
| At idle | ~6 POSTs/min per service; the metrics reader adds one per minute |

**The request count is governed by a timer, not by your traffic** — until a batch fills. That is the
part worth taking in before you tune anything, because it decides which lever helps:

- A receiver limiting **requests per second**: lower the request count. Sampling will not do it —
  measured, `AIRA_OTEL_SAMPLE_RATIO=0.25` took spans from 17.5 to 5.9 per request and left the POST
  count unchanged at 6. Batch harder instead (below).
- A receiver limiting **volume or ingested events**: `AIRA_OTEL_SAMPLE_RATIO` is the lever, and it
  scales roughly linearly. It applies to traces only; logs and metrics are unaffected.

If your endpoint sits behind the collector — the `make up-lab` shape, and the one to prefer — the
collector's own defaults are the wrong shape for a rate limit: it forwards every **200 ms** from
**ten concurrent senders**, and a `429` is retryable, so refusals come back and tighten the loop.
Four variables change that:

```bash
# hold telemetry this long, put this much in one request, send one at a time,
# and do not ask again before the window has moved:
make up-lab LAB_SIEM_ENDPOINT=https://siem.internal:4318 \
    LAB_SIEM_BATCH_SECONDS=10s LAB_SIEM_BATCH_SIZE=8192 \
    LAB_SIEM_CONSUMERS=1 LAB_SIEM_RETRY_INITIAL=5s
```

Those are the defaults the overlay now ships. Measured against a counting listener, 40 gateway
requests: **8 HTTP requests carrying 696 spans** (87 per request), against **27 carrying 1399**
(52 per request) with the collector's own defaults — about 40 % fewer requests for the same
telemetry, and sequential rather than in bursts of ten.

`make otel-status` shows whether anything is being dropped while you tune.

### Not the same switch

`AIRA_LOG_JSON` is unrelated: it is whether **the service's own log lines on stdout** are JSON or
human-readable text. It does not touch OTLP. `AIRA_LOG_JSON=false` is for reading a terminal during
development; leave it `true` anywhere something collects container output.

> done an OTLP endpoint · `AIRA_OTEL_ENABLED=true` · a sample ratio you can afford · the encoding
> decided on the collector, not in AIRA

---

## 7. The SPA is configured at deployment time

**One image, any realm.** `public/runtime-config.js` ships beside the bundle and is loaded before
the app, so pointing the console at your Keycloak is replacing one file — a volume mount, a
`ConfigMap`, a `sed` in an entrypoint — and never a rebuild:

```js
window.__AIRA_CONFIG__ = {
  issuer: 'https://sso.example.com/realms/aira',
  clientId: 'aira-gateway',
};
```

The issuer used to be compiled in, which meant one build per environment and, in practice, one
*published* build pointing at whichever Keycloak the person who ran it had in mind. A misdirected
console does not fail — it sends people to a real login page at the wrong realm.

At run time nginx needs the two proxy targets and one policy value:

| Setting | Is |
|---|---|
| `AIRA_MANAGEMENT_UPSTREAM` | where `/api` goes |
| `AIRA_GATEWAY_UPSTREAM` | where `/gw` goes |
| `AIRA_CSP_CONNECT_SRC` | `'self'` plus your Keycloak's origin |

**Set `AIRA_CSP_CONNECT_SRC` together with the issuer, or not at all.** The console's content
policy allows its own origin and the one host named here; the token request goes to Keycloak
cross-origin, so an issuer the policy does not name produces a login that fails in the browser and
nowhere else.

Its resolver must **re-resolve** upstreams. With a name resolved once at start-up, a gateway
container restarting behind a new IP produces a 502 that outlives the restart.

nginx sets the console's security headers — a content policy, `nosniff`, `DENY` framing, a referrer
policy — from `deploy/aira-headers.inc.template`. The bundle itself sets none: it is a static file,
and the headers belong to whatever serves it. `requireHttps: 'remoteOnly'` in the OIDC client keeps
`localhost` working over HTTP and demands HTTPS everywhere else.

> done runtime-config per environment · both proxies · `AIRA_CSP_CONNECT_SRC` · a resolver that
> re-resolves · TLS in front

---

## 8. Reverse proxy and TLS

Terminate TLS in front of both APIs. Two things AIRA needs from the proxy:

- **`X-Forwarded-For`**, if you want real client addresses in the audit trail — and then set
  `AIRA_TRUST_FORWARDED_FOR=true`. Leave it off otherwise: with it on and no trusted proxy, any
  caller can write any address into your audit trail.
- **No buffering on streaming responses.** `:streamGenerateContent` and
  `/kira/api/external/streaming-chat` are SSE; a proxy that buffers them turns a stream into a
  single late response. In nginx that is `proxy_buffering off` — the shipped SPA proxy sets it, and
  it is the default that bites, not an exotic setting.
- **Keep the query string out of your access log**, or turn the log off for the gateway. Every
  Gemini client may authenticate with `?key=<api key>`: that is the wire protocol, not a choice
  AIRA makes. The gateway redacts it from its own logs and from exported spans, and sends
  `Referrer-Policy: no-referrer` so it does not leak onward — but a proxy in front logs the request
  line by default, and that line contains the credential.

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

### Groups for the question catalogue

The catalogue (`FRD-504`) is run **against a use case's own pipeline**, and a run is that use
case's traffic. Two things have to be true of the person running it (`ADR-0020`), and the first is
the one people look for a setting for:

1. **The gateway would accept them for the use case** — decided from the groups their token
   carries, exactly as for an ordinary API request.
2. **They administer that use case**, or they are a Global Administrator or IT Security. A plain
   use-case *user* may call the use case all day and may not put a hundred questions to it: that
   spends the use case's budget and reads a catalogue stating what this installation tests for.

Three consequences worth knowing before you look for a missing setting:

- **Seeing a use case is not calling one** (`ADR-0007`). An oversight role sees every use case and
  is offered a Run button for none of them, by design — create a group whose path is
  `/use-cases/<slug>`, or grant an existing group to that use case in the console, which works the
  same way.
- **Calling one is not administering one.** Administration comes from a group grant or a membership
  with the `admin` role, both set in the console; the token carries no notion of it. Somebody who
  should be able to test their own pipeline and cannot is almost always a `user` where they should
  be an `admin`.
- **A use case with no model released to it cannot be run**, because there is nothing for the
  questions to be put to. The console says exactly that rather than disabling the button silently.
  A run is entered at a model the person starting it picks, from that release list.

The seed creates one use case named `smoke-test` for IT Security's **model** evaluation — a
released model and a pipeline that starts there. It is an ordinary use case in every respect and
the application does not branch on it; to evaluate another model, make another use case the same
way. Its group is `/use-cases/smoke-test`.
