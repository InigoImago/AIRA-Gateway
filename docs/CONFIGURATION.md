# Configuration reference

Every setting, what it is for, and what happens if you get it wrong.

**Every setting is prefixed `AIRA_`** and read by [pydantic-settings]; the few variables that are
not settings — Vault's own connection variables, and what Compose, the console's container and the
collector read — say so where they appear. The defaults below are the ones in the code, so they are
the values a process uses when nothing sets them. Both services also read the same variable names
from **Vault** when one is configured, and Vault **wins** over the environment
([§7](#7-secrets-from-vault)).

[pydantic-settings]: https://docs.pydantic.dev/latest/concepts/pydantic_settings/

| Jump to                                                       |                                                |
| ------------------------------------------------------------- | ---------------------------------------------- |
| [1 Common](#1-common-to-both-services)                        | logging, environment, currency, tracing        |
| [1a Integration debugging](#1a-watching-a-call-to-another-system) | one line per call to another system        |
| [2 Gateway](#2-gateway)                                       | the data plane                                 |
| [3 Model platforms](#3-model-platforms)                       | Vertex, Foundry, OpenAI-compatible, Gemini API |
| [4 Management](#4-management)                                 | the control plane                              |
| [5 Frontend](#5-frontend)                                     | the console's container, configured at start   |
| [5a Collector](#5a-the-collector-and-a-second-destination)    | what arrives, and a second destination         |
| [5b Images](#5b-which-images-a-deployment-runs-compose-only)  | which version a deployment runs                |
| [6 Missing pieces](#6-what-happens-when-something-is-missing) | degradation, decided                           |
| [7 Vault](#7-secrets-from-vault)                              |                                                |
| [8 Safe defaults](#8-what-refuses-to-boot)                    | what refuses to boot                           |

---

## 1. Common to both services

| Variable                                                       | Default                                                    | What it does                                                                                                                                                                      |
| -------------------------------------------------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_ENVIRONMENT`                                             | `local`                                                    | `local`, or anything else — no other value means anything to either plane, so `demo`, `staging` and `production` are all simply *not local*. Not cosmetic: outside `local`, dev secrets and `DEBUG` **refuse to boot** ([§8](#8-what-refuses-to-boot)), `seed_demo` refuses unless `AIRA_DEMO_MODE` is on or `--force` is given, and the gateway stops showing itself — `/docs`, `/redoc`, `/openapi.json`, the mock upstream and the full `/readyz` diagnosis exist only under `local` or `AIRA_DEMO_MODE`. |
| `AIRA_LOG_LEVEL`                                               | `INFO`                                                     |                                                                                                                                                                                   |
| `AIRA_LOG_JSON`                                                | `true`                                                     | Structured logs. `false` gives human-readable lines for a terminal.                                                                                                               |
| `AIRA_DEMO_MODE`                                               | `false`                                                    | Enables the demo API key (its plaintext is in this repository) and lets `seed_demo` run. **Never in production.** The key is a credential only while this is on, and a start outside `local` without it revokes one a demo run left in the database. On the gateway it also does what `local` does — registers the mock upstream, serves `/docs`, `/redoc` and `/openapi.json`, and shows the full `/readyz` diagnosis (hosts, upstreams, Vault keys) to anyone — and waives six of its start-up checks ([§8](#8-what-refuses-to-boot)). Management waives nothing for it. |
| `AIRA_CURRENCY`                                                | `EUR`                                                      | One installation, one currency. Appears in every spend figure and in the CSV column header — a number without a unit in a file that gets forwarded is a number nobody can act on. |
| `AIRA_OTEL_ENABLED`                                            | `false`                                                    | OTLP export of traces, metrics and logs. On with an empty `AIRA_OTEL_ENDPOINT` exports nothing, and says nothing.                                                                 |
| `AIRA_OTEL_ENDPOINT`                                           | `http://localhost:4318`                                    | Collector endpoint (OTLP/HTTP, protobuf); `/v1/traces`, `/v1/metrics` and `/v1/logs` are appended. Headers, a CA or client certificate, the timeout and compression on this leg are the OpenTelemetry SDK's own `OTEL_EXPORTER_OTLP_*` variables; the address is this one. |
| `AIRA_OTEL_SAMPLE_RATIO`                                       | `1.0`                                                      | 1.0 = every trace. Decided by trace id in each process, never by a caller's `traceparent` flag. **Below 1 it thins the delivery channel too**: an access record is a span, and a span not kept is a record a SIEM never receives. Outside 0–1 the process stops at start while export is on. |
| _(no setting)_                                                 | `application/x-protobuf`                                   | **The OTLP encoding is not a setting.** The applications always send protobuf; JSON is a choice on the collector's own exporter (`encoding: json`) — see [`INTEGRATIONS.md` §6](INTEGRATIONS.md#6-observability). |
| `AIRA_DEBUG_INTEGRATIONS`                                      | _(empty)_                                                  | One log line per call to another system, while you are wiring one up. See [§1a](#1a-watching-a-call-to-another-system) — empty is off, and off is what a working installation runs. |
| `AIRA_DEBUG_OTEL_PAYLOAD`                                      | `0`                                                        | Print this many items of each OTLP batch as OTLP/JSON on stdout. 0 is off. Expensive and loud; see [`INTEGRATIONS.md` §6](INTEGRATIONS.md#6-observability).                       |
| `AIRA_POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | `localhost` / `5432` / _(differs)_ / `aira` / `aira-local` | **Two different databases** — see the note below.                                                                                                                                 |
| `AIRA_KAFKA_BOOTSTRAP_SERVERS`                                 | `localhost:29092`                                          | Comma-separated `host:port` brokers (no port means `9092`); an IPv6 broker as `[address]:port`. Empty means no broker, and readiness reports Kafka as not configured. An entry nothing could dial refuses to start. Readiness counts the cluster reachable while any broker answers, and Kafka never makes an instance not ready ([§6](#6-what-happens-when-something-is-missing)). |
| `AIRA_KAFKA_STAGE`                                             | _(empty)_                                                  | The stage in every topic name and in the gateway's consumer group, on a cluster several stages share: `t` gives `aira.t.usecases` and `aira-gateway.t`. **Both planes need the same one.** Empty keeps `aira.usecases`. One to sixteen lower-case letters or digits; anything else refuses to start. |

### 1a. Watching a call to another system

`AIRA_DEBUG_INTEGRATIONS` turns on one structured log line per call this service makes to a system
that is not ours. It exists for the days when something is being integrated and *"did we send it,
and did it arrive"* has no answer anywhere.

```bash
AIRA_DEBUG_INTEGRATIONS=            # off — the default
AIRA_DEBUG_INTEGRATIONS=otel        # one system
AIRA_DEBUG_INTEGRATIONS=kafka,auth  # several
AIRA_DEBUG_INTEGRATIONS=all         # every one of them
```

| Name | What gets a line |
| --- | --- |
| `otel` | Every OTLP export: the signal, how many items, the endpoint, the duration, and whether the collector took it. Plus whatever the OpenTelemetry SDK itself says about a failure. |
| `kafka` | Connecting to the broker (which is where a SASL mechanism, a trust store and an address are first proven), each record with the partition and offset it landed on, and each event that arrives at the consumer. |
| `auth` | The JWKS fetch against the identity provider — its duration, and the reason when it fails. |
| `vault` | The AppRole login and the KV read: the status Vault gave, the path, and where the secret-id came from. Never a value. |
| `redis` | Each counter script: the duration, and the reason when the store is not usable. |
| `postgres` | A new physical connection, and a failure to establish one. **Not** a line per statement — those are in the trace. |

There is no second switch. The lines are `INFO` (`WARNING` when the call failed), so
`AIRA_DEBUG_INTEGRATIONS` alone is enough; you do not also need `AIRA_LOG_LEVEL=DEBUG`.

A name this build does not know **refuses the process at start-up**, with the valid names in the
message — rather than starting up and watching nothing, which reads like a broken feature.

What a line looks like, and what it never carries:

```json
{"system": "otel", "operation": "export", "outcome": "failed", "signal": "traces",
 "target": "http://otel-collector:4318/v1/traces", "items": 12, "duration_ms": 5003.7,
 "error_type": "ConnectTimeout", "error": "timed out", "event": "integration_call",
 "level": "warning"}
```

No payload, no token, no secret value, no message body, no bound SQL parameter. Addresses are
redacted in both places a credential hides in one — a `?key=` parameter and a `user:password@`
authority — so `redis://aira:REDACTED@redis:6379/0` is what reaches the log.

**Lines about `otel` go to stdout only** and are never exported over OTLP. A report that a log
export failed would otherwise become a log record queued for export.


### How this service authenticates to Kafka

**`PLAINTEXT` is refused outside `local`**, on both planes, whenever a broker is configured
(`AIRA_DEMO_MODE` waives it on the gateway only). Both planes *apply* what arrives on these topics —
the gateway builds the read-model its authorization comes from out of them — so an unauthenticated
broker is a way to grant yourself administrator rights on any use case with no credential and no
audit row. That is why the check is a startup refusal rather than a warning.

| Variable                       | Default     | What it does                                                                                              |
| ------------------------------ | ----------- | --------------------------------------------------------------------------------------------------------- |
| `AIRA_KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | `PLAINTEXT` \| `SSL` \| `SASL_PLAINTEXT` \| `SASL_SSL`. Anything but `PLAINTEXT` outside `local`.        |
| `AIRA_KAFKA_SASL_MECHANISM`    | —           | e.g. `SCRAM-SHA-512`, `PLAIN`. Read by the two `SASL_*` protocols; empty means `SCRAM-SHA-512`.           |
| `AIRA_KAFKA_SASL_USERNAME`     | —           | SASL user. Identifies rather than authorises, so it is configuration — a config file may carry it (`config/integrated.example.yaml` does), and Vault may hold it too. |
| `AIRA_KAFKA_SASL_PASSWORD`     | —           | SASL password. **A secret** — Vault (§7).                                                                 |
| `AIRA_KAFKA_SSL_CAFILE`        | —           | Path to the CA bundle that signs the broker's certificate, for `SSL` and `SASL_SSL`.                       |

> **The two services use two databases.** The gateway defaults to `aira_gateway`, Management to
> `aira_mgmt`. They are not interchangeable: one holds configuration and the other holds the
> read-model plus everything that happened. Pointing both at one database will appear to work until
> a migration from one plane meets a table from the other.

---

## 2. Gateway

### Authentication and attribution

| Variable                            | Default     | What it does                                                                                                                                                                                                                                                                |
| ----------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_AUTH_REQUIRED`                | `true`      | `false` opens every route — a `local`-only switch: refused anywhere else, `AIRA_DEMO_MODE` included. With it off there is no identity, so role checks are skipped rather than guessed.                                                                                     |
| `AIRA_OIDC_ENABLED`                 | `false`     | Accept OIDC bearer tokens in addition to API keys. **Required** for every console screen that asks the gateway — dry runs, budget consumption, reports, incidents, the catalogue's discovery and model checks, smoke tests. Gateway only: Management always requires OIDC. |
| `AIRA_OIDC_ISSUER`                  | —           | e.g. `https://sso.example.com/realms/aira`. Compared literally with each token's `iss`, so it is the address the **browser** got the token from. Read by both planes, and by the console's container ([§5](#5-frontend)).                                                    |
| `AIRA_STACK`                        | `aira`      | Namespaces a whole stack: the Compose project, the network, and every container name. Docker refuses a duplicate container name and a *fixed* network name is shared by every project on the machine — so without all three, a second stack either cannot start or silently joins the first one's bridge, where `postgres` answers for two containers. Read by Compose only. |
| `AIRA_PUBLISH_*_PORT`               | _(today's)_ | Which **host** port each service is published on — `AIRA_PUBLISH_POSTGRES_PORT`, `…_KEYCLOAK_PORT`, `…_KEYCLOAK_HEALTH_PORT`, `…_KAFKA_PORT`, `…_REDIS_PORT`, `…_GRAFANA_PORT`, `…_VAULT_PORT`, `…_SCHEMA_REGISTRY_PORT`, `…_OTLP_GRPC_PORT`, `…_OTLP_HTTP_PORT`, `…_OLLAMA_PORT`, `…_GATEWAY_PORT`, `…_MANAGEMENT_PORT`, `…_FRONTEND_PORT`, and `…_KDC_PORT` — the KDC of the showcase's stand-in Windows domain (profile `windows-sso`); the collector's two are in [§5a](#5a-the-collector-and-a-second-destination). Read by Compose only. **Its own prefix on purpose**: `AIRA_POSTGRES_PORT` is a *setting* — the port the gateway connects to — and one name for both meanings would make moving the published port silently redirect the in-network connection. |
| `AIRA_OIDC_ISSUERS`                 | —           | **Several Keycloak realms at once**, for one organisation whose people live in more than one — a migration, a second instance, a merger. `issuer\|audience\|jwks_uri` per entry, `;`-separated, the JWKS URI optional. Empty means the single pair below, which is what most deployments keep. A token is routed by its own `iss` and then verified against that realm properly; **every** entry needs an audience outside `local` or the gateway refuses to start, and an entry with no issuer refuses to start (with OIDC on, and anywhere outside `local`). The realms are trusted equally — the same group path from either grants the same access, which holds because they describe one population. **Gateway only**: Management and the console read the single `AIRA_OIDC_ISSUER`, so a person from a second realm can call the gateway and cannot sign in to the console. |
| `AIRA_OIDC_AUDIENCE`                | —           | The audience this service answers to. Enforced when set — and **required outside `AIRA_ENVIRONMENT=local`**: without it, any token the issuer minted is accepted, including one issued to a different client. Both planes refuse to start without it — the gateway while `AIRA_OIDC_ENABLED` is on (`AIRA_DEMO_MODE` waives it there), Management whenever an issuer is set. |
| `AIRA_OIDC_CLOCK_SKEW_SECONDS`      | `60`        | How far the **issuer's** clock may run ahead of this host's, for `iat` and `nbf`. Costs nothing — a token that is "too new" was still genuinely minted — and at `0` a service one second behind its issuer refuses **every** freshly minted token as `401`, which reads exactly like a wrong secret. Read by both planes; outside 0–300 s refuses to start. |
| `AIRA_OIDC_EXPIRY_LEEWAY_SECONDS`   | `0`         | How long past `exp` a token is still accepted. **This is the half with a cost**: it extends a credential's life beyond what the issuer granted, so it is off. The predecessor grants 60 s here; a client that needs it has a broken refresh strategy, and an installation may still choose to absorb it. Read by both planes; outside 0–300 s refuses to start. |
| `AIRA_OIDC_JWKS_URI`                | _(derived)_ | Only when it is not `<issuer>/protocol/openid-connect/certs` — or when this service cannot reach the issuer's address: the issuer is what the browser saw, the key set is fetched by the service. Both planes. The Compose stack defaults it to the in-network `http://keycloak:8080/…`, and `make up-integrated` requires it. |
| `AIRA_REQUIRE_USE_CASE`             | `true`      | Refuse a model call from an authenticated caller that names no use case — any method but `GET`, `HEAD` and `OPTIONS`, on both surfaces. A key bound to a use case names its own. The break-glass key is exempt: the gateway's own CLI mints it, bound to no use case, for when Management is what is broken. Turning it off anywhere but `local` refuses to start, `AIRA_DEMO_MODE` included: without a use case a call is charged to no budget, bounded by no use-case limit, outside the model release, and its audit row names nobody. |
| `AIRA_API_KEY_DEFAULT_DAYS`         | `30`        | Lifetime of a newly issued API key. **A key is always bounded** — omitting the lifetime at issuance takes this value, and there is no way to ask either plane for one that never expires. Read by both planes from one definition.                             |
| `AIRA_API_KEY_MAX_DAYS`             | `180`       | The longest lifetime anybody may request. More is **refused by name**, with the maximum in the message — a silently shortened lifetime would leave the requester believing a date that is not in the database. Management refuses to start on a maximum below 1, or on a default outside 1 to the maximum. |
| `AIRA_MAX_AUTH_FAILURES_PER_MINUTE` | `60`        | Failed authentications one source address may make before it is answered 429 with `Retry-After`. Counts **refusals only**, so a working credential never touches it however busy the caller is. `0` disables it (an installation whose WAF already does this).              |
| `AIRA_REDACT_PATTERNS`              | —           | Extra regexes (`;`- or newline-separated) redacted from stored payloads, **added to** the built-in credential shapes, never replacing them. An invalid or exponentially-backtracking pattern stops the gateway rather than silently matching nothing.           |
| `AIRA_TRUST_FORWARDED_FOR`          | `false`     | Read the client IP from `X-Forwarded-For`, `AIRA_TRUSTED_PROXY_HOPS` entries from the right. Only enable behind a proxy you control — otherwise any caller can write any address into your audit trail. Gateway only: Management has no such switch.                     |
| `AIRA_DIRECTORY_CLIENT_ID`          | —           | Management only. A **read-only** Keycloak service account (`view-users`, `query-groups`) so the console can search your groups and people when granting access. Without it the console offers what it already knows and says so, and **no role can be bound to a group**: a group nobody could check is refused.                                |
| `AIRA_DIRECTORY_CLIENT_SECRET`      | —           | Its secret. From Vault in any real deployment.                                                                                                                                                                                                                              |
| `AIRA_DIRECTORY_URL`                | —           | Management only. Where it reaches Keycloak's admin API, when that is not the server in `AIRA_OIDC_ISSUER` — the issuer is the address a browser uses, and inside a container it rarely is Keycloak. Defaults to the issuer's server; the compose stack sets `http://keycloak:8080`. |
| `AIRA_MODEL_REFERENCE_URL`          | —           | Management only. Where the **model reference** is read live — `https://models.dev/api.json`, or a mirror. Empty answers from the copy this release ships, so an upgrade makes no new outbound call; set, Management asks at most once a day, waits five minutes after a failure before asking again, and answers from the shipped copy, saying why, whenever the list cannot be had. |
| `AIRA_MODEL_REFERENCE_EXCHANGE_RATE` | — | Management only. How many units of `AIRA_CURRENCY` one US dollar is, e.g. `0.92`, for the model reference's list prices: where the billing account has no price for a model, the list price is filled at this rate and says so. Empty: a dollar price is shown beside the field, not filled. Must be a positive number. |

| Variable                   | Default | What it does                                                                                                                                                                                                                                                                                                        |
| -------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_TRUSTED_PROXY_HOPS`  | `1`     | How many reverse proxies **append** to `X-Forwarded-For` in front of the service. The address is read that many entries **from the right**, never from the left: a proxy appends, so the left end is whatever the caller sent, and reading it would let a caller choose the address in the audit trail — and rotate it to defeat the failed-authentication bound. Raise by one per additional proxy. **The two planes read it differently.** The gateway consults it only with `AIRA_TRUST_FORWARDED_FOR` on; a chain shorter than this did not come through the proxies and is ignored in favour of the socket peer, as is every header at `0`. **Management reads it always** (DRF's `NUM_PROXIES`): its failed-authentication bound counts against that entry, and at 1 or more it believes a proxy's `X-Forwarded-Proto` (for HSTS). There a chain shorter than the count yields its **leftmost** entry, which the caller wrote — so set it to exactly the number of proxies that append; `0` reads the socket peer. A negative value refuses to start on both planes. `1` is the console's nginx, which Management always sits behind; `0` when Management is reached directly. |
| `AIRA_ROLE_GROUPS`         | —       | Which Keycloak group confers which AIRA role, as `role=/path[,/path];role=/path`. **Group membership is the only source of a role** — a realm role on the same token is not read. Only the three built-in roles may be named (`global-admin`, `it-security`, `it-steuerung`); any other name, a retired one such as `use-case-admin`, or a path that does not start with `/` refuses to start on both planes, in every environment. A custom role is bound to its group in the console, not here. Empty grants no oversight to anybody, which is the safe direction for a data plane; Management is the plane that refuses to boot without a global-admin group outside `local`, because it is the one an installation is repaired from. |

### Storage and retention

Management reads the first four as well: the privacy notice states them
([§4](#the-privacy-notice)).

| Variable                      | Default | What it does                                                                                                                                                     |
| ----------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_STORE_PAYLOADS`         | `true`  | Operator **kill switch** above the per-use-case setting: a use-case admin may decline storage but cannot re-enable it where the operator forbade it.             |
| `AIRA_STRICT_DATA_MODE`       | `true`  | **Strict data mode**, read by both planes. On, nobody but Global Administrator and IT Security sees a person: stored content is readable by those two roles only, whatever a use case chose; figures per person, the name and API key on a request, and the person or key a finding or block names reach only them and the person concerned; latency per person is computed for nobody; and no other role can be given `payload.read_any` or `incident.investigate`. Applied when read, so turning it off restores every use case's own content setting and every stored grant. The privacy notice states which mode runs. |
| `AIRA_PSEUDONYM_SECRET` | — | The key a person's name is **pseudonymised** with in spans, the SIEM feed and log lines while strict data mode is on — the same person always the same pseudonym, so one caller can still be followed, and nobody reads the name off it. A keyed hash, because usernames are guessable and an unkeyed one is reversed by hashing the directory. Shared by both planes, and a credential: from Vault. Unset, each process draws its own key and says so once at start-up; nothing leaks, but pseudonyms differ between processes and restarts. Re-identifying a caller is the audit trail's job, not this key's: a span carries its trace id, and the request's row names the caller to Global Administrator and IT Security. |
| `AIRA_DEFAULT_RETENTION_DAYS` | `7`     | For traffic that carries no use case, and for rows whose use case the read-model does not know. Use-case traffic follows its own period. **1 to 3650**, the bounds a use case's own period has; anything else refuses to start on both planes, because the retention sweep and the privacy notice — which prints this value — would read it differently. |
| `AIRA_LOG_RETENTION_DAYS`     | `0`     | Whole-row deletion. **0 = never**, deliberately: payload retention and record retention are separate clocks, and deleting rows would truncate the spend history. |
| `AIRA_LOG_QUEUE_SIZE`         | `512`   | Audit-write queue. `0` writes on the request path — supported, and the cost is the latency the queue exists to remove.                                           |

### Controls

| Variable                             | Default                    | What it does                                                                                                                                                                             |
| ------------------------------------ | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_ENFORCE_BUDGETS`               | `true`                     | `false` reserves nothing and refuses nobody. Both budget screens say so, and so does a caller's own `/v1beta/me/budget`: every budget keeps its figures, its bars and its warnings while stopping nothing. Spend is still counted and still reported, so the figures are true. |
| `AIRA_ENFORCE_PRICED_MODELS` | `true`                 | A model with no price on file cannot be counted against a **cost** budget, so it would spend without bound. On, it is refused where a cost limit binds the request — a dispatch condition, so a chain tries the next candidate and a priced fallback still serves. `false` keeps such traffic flowing while prices are filled in, knowing the limit is not a limit. Token and request limits are unaffected, and with `AIRA_ENFORCE_BUDGETS` off no limit binds, so this refuses nothing. |
| `AIRA_COUNT_CANCELLED_STREAMS` | `true` | A stream whose caller left before the vendor said what it used is otherwise booked as nothing. On, the gateway asks the vendor's own token count — free — for the request and for the answer received, books that, and marks the row `counted`: a floor, since what the vendor produced after the caller left is in no count, and reports say so. Vertex's Gemini models only; elsewhere the usage stays unknown. `false` sends nothing a second time — the request and the received answer go to the same vendor and region again, which the privacy notice states. |
| `AIRA_ENFORCE_RATE_LIMITS`           | `true`                     | `false` takes no token from a configured limit's bucket. Same shape as the line above: the limits stay configured and visible, and nothing is refused for exceeding one. A suspension's throttle still applies — `AIRA_ENFORCE_SUSPENSIONS` is its switch. |
| `AIRA_ENFORCE_SUSPENSIONS`           | `true`                     | `false` records findings and refuses nobody — the setting to use while learning what your rules do. A rule that would block or throttle records `detected_not_enforced` and writes no suspension, so switching it back on starts from nothing a rule decided meanwhile. |
| `AIRA_DETECT_ANOMALIES`              | `true`                     | `false` stops evaluation entirely; rules stay authored. "Switched off" and "deleted" are different states.                                                                               |
| `AIRA_ANOMALY_INTERVAL_SECONDS`      | `60`                       | How often the detector wakes. It evaluates only scopes that saw traffic, so a longer interval costs findings _latency_, not accuracy.                                                    |
| `AIRA_BUDGET_ESTIMATE_OUTPUT_TOKENS` | `1024`                     | What a reservation assumes an answer will cost when the caller sets no cap. Too low under-reserves and lets concurrent requests overshoot; too high refuses traffic that would have fit. |
| `AIRA_REDIS_URL`                     | `redis://localhost:6379/0` | The shared counter store. See [§6](#6-what-happens-when-something-is-missing).                                                                                                           |
| `AIRA_REDIS_SENTINELS`               | _(empty)_                  | Redis Sentinel: the sentinels as `host:port,…`. Set, they **replace** `AIRA_REDIS_URL`: the gateway asks them for the leader and follows it when it moves to another server. An entry that is not `host:port` refuses to start. |
| `AIRA_REDIS_SENTINEL_SERVICE`        | _(empty)_                  | The name the sentinels know the leader by. Required with `AIRA_REDIS_SENTINELS`; without it the gateway refuses to start. |
| `AIRA_REDIS_USERNAME`                | _(empty)_                  | The data nodes' ACL user, where they have one. **With Sentinel only** — beside `AIRA_REDIS_URL` it is not read, and the credentials go inside the URL (`redis://user:password@host:6379/0`), which then carries a secret and belongs in Vault. |
| `AIRA_REDIS_PASSWORD`                | _(empty)_                  | The data nodes' password, **with Sentinel only** — the same rule as the line above. **A secret** — Vault. |
| `AIRA_REDIS_SENTINEL_PASSWORD`       | _(empty)_                  | The sentinels' own password, where they require one. **A secret** — Vault. |
| `AIRA_REDIS_DB`                      | `0`                        | The database number on the leader, with Sentinel. |

### Input bounds

Every bound here must be above zero: `0`, or `-1` written to mean "unlimited", refuses to start.

| Variable                              | Default           | What it does                                                                                                                                                       |
| ------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `AIRA_MAX_REQUEST_BYTES`              | `8388608` (8 MiB) | Enforced before any route. A refusal is **413** and still leaves an audit row.                                                                                     |
| `AIRA_MAX_RESPONSE_SCHEMA_BYTES`      | `32768`           |                                                                                                                                                                    |
| `AIRA_MAX_RESPONSE_SCHEMA_DEPTH`      | `8`               |                                                                                                                                                                    |
| `AIRA_MAX_RESPONSE_SCHEMA_PROPERTIES` | `256`             | A caller-supplied schema is parsed and **forwarded, never executed** — re-validating provider output against it would run caller-supplied regexes on the hot path. |
| `AIRA_MAX_EMBEDDING_BATCH`            | `256`             | A batch of n weighs n against rate limits and budgets.                                                                                                             |
| `AIRA_MAX_EMBEDDING_CHARS`            | `1000000`         |                                                                                                                                                                    |

### Residency and CORS

| Variable                      | Default                                 | What it does                                                                                                                                                                                                                                                                                                                                                             |
| ----------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `AIRA_ALLOWED_REGIONS`        | _(EU regions of every supported cloud)_ | **One policy for every cloud** — Google's `europe-west1` and Azure's `westeurope` in one list. A model configured outside them **refuses to start**: residency is enforced, not intended. Google AI Studio (`AIRA_GOOGLE_API_KEY`) is the region `global`, because that endpoint names none and guarantees none — using it means adding `global` to this list, out loud. |
| `AIRA_ALLOWED_MODEL_REGIONS`  | —                                       | A region permitted for **named models alone**, beyond the list: `region=model,model;region=model`, e.g. `global=glm-4.7-maas,deepseek-v3.2-maas`. For a model served only where the list does not reach — Agent Platform's open models answer at `global` and nowhere else — without permitting that region for every model. A whole endpoint (an OpenAI server, Foundry, AI Studio) is never opened by a model's name. A malformed entry stops the gateway starting. |
| `AIRA_CORS_ORIGINS`           | —                                       | Comma-separated allow-list.                                                                                                                                                                                                                                                                                                                                              |
| `AIRA_CORS_ALLOW_CREDENTIALS` | `false`                                 | `*` **plus** credentials is refused at startup rather than silently ignored.                                                                                                                                                                                                                                                                                             |

### Compatibility surface and build identity

| Variable                                                      | Default | What it does                                                           |
| ------------------------------------------------------------- | ------- | ---------------------------------------------------------------------- |
| `AIRA_KIRA_SUNSET`                                            | —       | Sent as written, unchecked, in the `Sunset` header of every KIRA response — RFC 8594 asks for an HTTP-date, e.g. `Sat, 31 Oct 2026 23:59:59 GMT`. Empty sends no `Sunset`; `Deprecation: true` is sent either way. |
| `AIRA_BUILD_NUMBER` / `_TIME` / `AIRA_GIT_COMMIT` / `_BRANCH` | —       | Served by the gateway's unauthenticated `/version-info`, so a running instance can say which build it is. Nothing stamps them yet — the Dockerfiles and CI pass no build argument — so a published image reports nulls. |
| `AIRA_APP_NAME`                                               | `aira-gateway` / `aira-management` | The OpenTelemetry `service.name` of each plane, so it names the service in every exported trace, metric and log; the gateway also reports it on `/version-info` and as its API's title. Changing it renames the service in every trace and dashboard, so it is left alone unless two installations share one collector. One value given to both planes names both the same. |
| `AIRA_TEST_DATABASE`                                          | `false` | **Test harness only**, on both planes. Points the engine at an in-memory SQLite instead of Postgres. Never set in a deployment: the schema differs (SQLite enforces no column lengths), so it would hide exactly the failures Postgres reports. Both planes refuse it outside `local`. |

---

## 3. Model platforms

Each platform is registered **only when configured**. An unconfigured one is simply absent — no
placeholder, no half-working adapter. An ambiguous routing table (two adapters claiming one model)
**refuses to boot**: with four platform families, last-registration-wins is a silent choice of region
and credential. So does anything an adapter cannot honour — a malformed model list, a credential
that cannot be used, a region outside `AIRA_ALLOWED_REGIONS` ([§8](#8-what-refuses-to-boot)).

### Google Vertex AI — Gemini, Anthropic and managed open models

| Variable                         | Default | What it does                                                                      |
| -------------------------------- | ------- | --------------------------------------------------------------------------------- |
| `AIRA_VERTEX_PROJECT`            | —       | GCP project id. The adapter is registered when this and a credential (any of the next three) are set; without a credential the model list below is not read. |
| `AIRA_VERTEX_CREDENTIALS`        | —       | The service account's key **as JSON** — the file's content, one value, not a path to it: a path is refused at start as not valid JSON (a path goes in `AIRA_VERTEX_CREDENTIALS_FILE`), and so is a key that cannot sign. From Vault in any real deployment. |
| `AIRA_VERTEX_CREDENTIALS_FILE`   | —       | The path of a file holding that same JSON — a Docker or Kubernetes secret, a file a Vault agent renders. Read at start, and **read again** whenever it changes, so a key written over it signs the next token without a restart. A missing or unusable file stops the start; one changed into something unusable keeps the last key that worked. A path, not a credential. Exclusive with `AIRA_VERTEX_CREDENTIALS`: both set stops the start. |
| `AIRA_VERTEX_FEDERATION_TOKEN_URL` | — | **No Google key: workload identity federation.** The token endpoint of your OIDC provider — Keycloak's `…/realms/<realm>/protocol/openid-connect/token`. The gateway fetches its identity there with a client-credentials grant and exchanges it at Google's Security Token Service; `AIRA_VERTEX_CREDENTIALS` or `_FILE` then holds Google's `external_account` configuration (no secret in it), which may also name its own source — a token file or a local URL — instead. |
| `AIRA_VERTEX_FEDERATION_CLIENT_ID` | — | The gateway's client at that provider: in Keycloak, a confidential client with service accounts enabled and an audience mapper for the workload identity provider. |
| `AIRA_VERTEX_FEDERATION_CLIENT_SECRET` | — | That client's secret. A credential: from Vault. |
| `AIRA_VERTEX_METADATA_SERVER` | `false` | On Google Cloud (a VM, GKE with workload identity, Cloud Run): the machine's own service account, from the metadata server — no key, no configuration. Exclusive with the credentials above. **Not needed on Google Cloud when nothing else is set**: with `AIRA_VERTEX_PROJECT` and no credential at all, the gateway asks the metadata server at start (one second at most) and uses the machine's account if it answers. A credential that *is* set and cannot be read — a missing key file — stops the start instead of falling back to the machine's account. |
| `AIRA_VERTEX_BILLING_ACCOUNT` | — | The Google **billing account** whose prices the console fills when a model is catalogued — e.g. `012345-567890-ABCDEF`. The gateway reads each model's SKUs and their price there, the contract price where one applies, in `AIRA_CURRENCY`. Needs a Google identity (not an API key) holding Billing Account Viewer on that account. An identifier, not a secret. Empty: no billing prices are looked up. |
| `AIRA_VERTEX_API_KEY`               | —           | The **other** credential the same adapter takes: an Agent Platform API key, sent as `x-goog-api-key`, for accounts that never create a service account. **Same regional hosts, same residency check**: this does not use Google's global express endpoint, which processes data anywhere. Claude on Vertex refuses it (`401`, passed on as `502`), so serving Anthropic's models needs the service account. Set both and the service account wins. From the environment or Vault. |
| `AIRA_VERTEX_MODELS`             | —       | `region/publisher/model`, comma-separated — e.g. `europe-west1/google/gemini-2.5-flash`. The publisher decides the wire: `google` (Gemini), `anthropic` (Claude), and Agent Platform's managed open models `zai-org` (GLM) and `deepseek-ai` (DeepSeek), which speak the OpenAI dialect at one endpoint. Any other publisher stops the start. Optional: a model the console's catalogue lists under `vertex` and its publisher, with a region, is served without being named here. The open models answer only at `global`, so each needs `AIRA_ALLOWED_MODEL_REGIONS`. |
| `AIRA_VERTEX_TIMEOUT_SECONDS`    | `120`   |                                                                                   |
| `AIRA_VERTEX_DEFAULT_MAX_TOKENS` | `4096`  | Anthropic requires `max_tokens`; this is what is sent when the caller names none. |
| `AIRA_VERTEX_PARTNER_STRICT_TOOLS` | `false` | Send Claude's tools **strict** where the declaration allows it without changing it (every object closed and saying what it requires), so the provider guarantees the arguments match. Off by default: on Vertex a strict tool is the partner-model feature `structured_outputs`, which the organisation policy `constraints/vertexai.allowedPartnerModelFeatures` forbids unless allowed — and then every request carrying a tool is refused. A tool declared in JSON Schema is never sent strict. |

### Microsoft Foundry — Azure OpenAI and Microsoft's own models

| Variable                       | Default      | What it does                                                                                                                                         |
| ------------------------------ | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_FOUNDRY_ENDPOINT`        | —            | `https://<resource>.openai.azure.com`. The adapter is registered when this and `AIRA_FOUNDRY_DEPLOYMENTS` are set.                                  |
| `AIRA_FOUNDRY_API_KEY`         | —            | The resource's key, sent as `api-key`. Required once an endpoint and deployments are set — without it the gateway refuses to start rather than answer `401` to everything. The only credential configurable today: Entra ID has a transport and no setting yet. |
| `AIRA_FOUNDRY_DEPLOYMENTS`     | —            | `model=deployment\|region[\|embed]`, entries separated by `;` — e.g. `gpt-4o=prod-gpt4o\|westeurope`. **The region is required** and checked against `AIRA_ALLOWED_REGIONS` at startup; an entry without one refuses to start. An Azure **deployment** is not a model: pricing attaches to the underlying model, or spend figures go quietly incomplete. |
| `AIRA_FOUNDRY_API_VERSION`     | `2024-10-21` |                                                                                                                                                      |
| `AIRA_FOUNDRY_TIMEOUT_SECONDS` | `120`        |                                                                                                                                                      |

### OpenAI-compatible servers (self-hosted, e.g. Ollama or vLLM)

| Variable                                                        | Default | What it does                                                                                                                                                    |
| --------------------------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_OPENAI_SERVERS`                                           | —       | `name=url\|models\|embeddings\|region`, semicolon-separated. **Named**, because a self-hosted fleet is several machines and each is audited under its own name. An entry with no URL or no models refuses to start; a region, where one is named, must be in `AIRA_ALLOWED_REGIONS`. |
| `AIRA_OLLAMA_URL` / `_MODELS` / `_EMBEDDING_MODELS` / `_REGION` | —       | Single-server shorthand for the same thing: a server named `ollama`, registered when a URL and at least one model are set.                                    |
| `AIRA_OLLAMA_TIMEOUT_SECONDS`                                   | `300`   | The answer allowance for **every** OpenAI-compatible server, `AIRA_OPENAI_SERVERS` entries included — there is no per-server one. High on purpose: a self-deployed model cold-starts. |

> A model name **may contain a colon** (`qwen3:0.6b`). The Gemini surface splits
> `models/<model>:<method>` at the **last** colon, because a method never contains one — so
> `qwen3:0.6b:generateContent` asks for `qwen3:0.6b`, not for a model called `qwen3`.

### Google Gemini public API

| Variable               | Default                                            | What it does                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ---------------------- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_GOOGLE_API_KEY`  | —                                                  | **Google AI Studio** (`generativelanguage.googleapis.com`). Registers the provider when set. The key is never logged, and `?key=` is redacted from spans. Its region is `global` and it is checked like every other adapter's, so under the EU default the gateway **refuses to start** until `global` is named in `AIRA_ALLOWED_REGIONS` — this endpoint gives no regional guarantee, which is the whole difference from Vertex. |
| `AIRA_GEMINI_MODELS`   | empty — the catalogue asks the key instead | Optional. The console's model catalogue asks the key which models it reaches, and a model it lists under `generative-language` is served without being named here. |
| `AIRA_GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta` | Keep the `/v1beta`: the adapter appends `/models/<model>:<method>` to this, so a base without it sends every call to a path that does not exist. Empty means the default.                                                                                                                                                                                                                                       |

The AI Studio key travels in the `x-goog-api-key` header, never in a URL. That endpoint allows an
answer 120 s and has no setting of its own.

> **Every upstream timeout in this section is the allowance for an answer.** Connecting is bounded
> apart, at 10 s: a model may think for minutes before its first byte, while a host that accepts no
> connection is down — and the regional failover and the fallback chain move on in seconds.

---

## 4. Management

| Variable                                       | Default       | What it does                                                                                                |
| ---------------------------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------- |
| `AIRA_SECRET_KEY`                              | _(dev value)_ | Django signing key. The dev value, or none, **refuses to boot** outside `local`.                            |
| `AIRA_DEBUG`                                   | `true`        | Django's `DEBUG`. Honoured only in `local`; anywhere else `true` — the default — **refuses to boot**, so a deployment sets `false`. |
| `AIRA_ALLOWED_HOSTS`                           | `*`           | Comma-separated. `*` **refuses to boot** outside `local`.                                                   |
| `AIRA_OIDC_ISSUER` / `_AUDIENCE` / `_JWKS_URI` | —             | The same meaning as the gateway's, and Management reads the two clock tolerances too. **One issuer**: `AIRA_OIDC_ISSUERS` is the gateway's alone. Management **requires** OIDC — there is no API-key path into the control plane — and outside `local` an unset issuer refuses to boot. |
| `AIRA_THROTTLE_AUTH_FAILURES`                  | `60/minute`   | Failed authentications one source address may collect. A presented token is verified against the issuer's JWKS **before** anything decides it is invalid, so probing is not free. Counts **refusals only**, so a working credential never touches it. `0/minute` switches it off; a bare `0` is not a rate and refuses to boot, in every environment. Not a DRF throttle — an `AnonRateThrottle` never fires here, because permissions are checked before throttles and every view requires authentication. |
| `AIRA_THROTTLE_USER`                           | `600/minute`  | The same for a signed-in caller — DRF's per-user throttle — sized to stop a script rather than to shape ordinary use: a console screen loads five panels at once. A rate DRF cannot read, or one that admits nothing (`0/minute`), refuses to boot. **Per process**, like the bound above (Django's `LocMemCache`): N workers admit N × the rate. No setting points the cache at a shared store, so an exact bound would take a code change. |

### The privacy notice

Every console user is shown the privacy notice as a window they acknowledge before working, and can
open it at any time from the console's footer. What it states about retention and content storage
is read from `AIRA_STORE_PAYLOADS`, `AIRA_DEFAULT_RETENTION_DAYS` and `AIRA_LOG_RETENTION_DAYS` —
**pass Management the same values as the gateway**, or the notice describes a different
installation. Changing any value the notice prints, or a role's access to other people's data, is a
new version of the notice, and everybody is asked to read it again.

| Variable                        | Default   | What it does |
| ------------------------------- | --------- | ------------ |
| `AIRA_PRIVACY_CONTROLLER`       | —         | The controller as printed: name, address, a contact (Art. 13(1)(a) DSGVO). **Refuses to boot** outside `local` when empty; locally the notice prints the gap instead. |
| `AIRA_PRIVACY_DPO_CONTACT`      | —         | How to reach the data protection officer. Empty prints that no contact is recorded. |
| `AIRA_PRIVACY_WORKS_AGREEMENT`  | —         | The works agreement (Betriebsvereinbarung) governing the system, by name or reference. Empty prints that none is recorded. |
| `AIRA_PRIVACY_NOTICE_MODE`      | `monthly` | When the window opens: `once` per edition; `monthly` — per edition and at the first start of each calendar month (UTC); `always` — every start of the console, for whoever reviews the notice. Anything else refuses to boot. |
| `AIRA_PRIVACY_DEFAULT_LANGUAGE` | `de`      | The language for a browser whose `Accept-Language` names none the notice exists in. Must be a language that has a text (`de`, `en`), or the process refuses to boot. |

Management has no `AUTH_REQUIRED` switch on purpose — a control plane that can be opened is a
control plane somebody opens.

---

## 5. Frontend

The console is one image for any realm: a static bundle behind nginx, configured **when its
container starts**. On every start the entrypoint writes `runtime-config.js` — the OIDC issuer and
client id the bundle reads — from the first two variables below, and derives the content policy's
`connect-src` from the issuer's origin, so moving Keycloak is one variable
([`INTEGRATIONS.md` §7](INTEGRATIONS.md#7-the-spa-is-configured-at-deployment-time)). Under
`ng serve` the bundle reads `public/runtime-config.js` as it is. None of these is a setting of
either plane, except the issuer, which both planes read as well.

| Variable                    | Default                        | What it does                                                                    |
| --------------------------- | ------------------------------ | ------------------------------------------------------------------------------- |
| `AIRA_OIDC_ISSUER`          | `http://localhost:8080/realms/aira` | The realm the console signs people in at, as a **browser** reaches it — the value both planes compare `iss` with. |
| `AIRA_OIDC_CLIENT_ID`       | `aira-gateway`                 | The console's public OIDC client.                                               |
| `AIRA_CSP_CONNECT_SRC`      | _(derived)_                    | What the console's content policy lets it call. Unset, it is `'self'` and the issuer's origin — leave it so: the token request goes to Keycloak cross-origin, and the derivation names exactly that origin. Set it only for an origin the derivation cannot guess — a second one, a websocket host — and name the issuer's origin in it too: a value replaces the derivation and does not follow a moved issuer, and a policy that misses it produces a login that fails in the browser and nowhere else. |
| `AIRA_MANAGEMENT_UPSTREAM`  | `http://management:8002`       | Where nginx sends `/api` — an address the **console's container** can reach; `127.0.0.1` there is that container itself. |
| `AIRA_GATEWAY_UPSTREAM`     | `http://gateway:8001`          | Where it sends `/gw`, the same way.                                             |

nginx resolves the upstreams on every request, through the resolver named in `AIRA_DNS_RESOLVER`
(default `127.0.0.11`, Docker's embedded DNS — name the platform's own anywhere else): a name
resolved once would leave a container restart behind a changed IP as a 502 that outlives the
restart. And `/gw` must not buffer: two verbs are SSE, and nginx buffers by default — the shipped
configuration sets `proxy_buffering off` and a read timeout above the gateway's own.

The local Compose stack publishes every port on `AIRA_BIND_HOST` (default `127.0.0.1`) and on its
IPv6 twin `AIRA_BIND_HOST6` (default `::1`) — Docker opens one socket per family, so reaching the
stack from another machine takes both. It runs Postgres, Redis, Kafka and a dev-mode Vault with the
credentials printed in `.env.example`, and Compose's plain `"5432:5432"` would put all of them on
every interface of the machine.

Compose reads a few more of its own, none of them a setting:

- `AIRA_RELAY_INTERVAL` (default `10`) and `AIRA_RETENTION_INTERVAL` (default `3600`) — the seconds
  between two runs of the outbox relay and of the retention sweep, which the stack runs in a loop.
  Anywhere else, a scheduler of the platform's runs both.
- `AIRA_CONSOLE_PORT` — the console's port as a **browser** sees it, handed to the Keycloak realm
  import for the client's redirect URIs. It defaults to `AIRA_PUBLISH_FRONTEND_PORT`, and matters
  when a forwarder or a tunnel remaps that port.

---

## 5a. The collector, and a second destination

**These are not settings.** Nothing in `GatewaySettings` or `ManagementSettings` declares them —
they are read by the **OpenTelemetry Collector's** own configuration through `${env:…}`, and by
Compose. They are here because an operator looking for a knob does not care which process reads it,
and this is the document that promises to list them.

The full worked explanation, with what each is measured to do, is
[`INTEGRATIONS.md` §6](INTEGRATIONS.md#6-observability).

### What arrives at the collector

| Variable | Default | What it does |
| --- | --- | --- |
| `AIRA_OTEL_DEBUG_VERBOSITY` | `normal` | How much the collector says about each batch it receives: `basic` (counts), `normal` (a line per span), `detailed` (every attribute, event and link). `make otel-arrivals` reads it. |
| `AIRA_OTEL_ARRIVED_FILE` | `/dev/null` | Write those arrivals as OTLP/JSON, one document per line, all three signals. `/payload/arrived.json` lands in `deploy/compose/payload/`. `/dev/stdout` works for a look and **mangles anything large** — Docker's log driver cuts at 16 KiB. |
| `AIRA_PUBLISH_OTLP_METRICS_PORT` | `8889` | Where the collector's own counters are published — accepted/refused against sent/failed. `make otel-status` reads them. |

### The two channels take the same settings

Everything below is stated once and applies to both tables: the two families differ only in their
prefix. A trace backend behind a credential, one wanting OTLP/JSON and one reachable only over HTTP
take the same variables a second destination does, and none of them means editing a file this
repository ships.

Each channel has **three `--config` slots**, and they answer three questions that do not depend on
each other, because a `.yaml` has no conditional:

| slot | question | choices |
| --- | --- | --- |
| `…_CONFIG` | **whether**, and where | the channel's on/off switch |
| `…_PROTOCOL_CONFIG` | **how it is reached** | OTLP/HTTP · OTLP/gRPC · a recipe |
| `…_AUTH_CONFIG` | **who we say we are** | nothing · a header · a recipe |

**`…_ENDPOINT` is the channel's default transport, and the other transport has its own name.** The
rule reads backwards between the two, so it is worth stating: the observability channel defaults to
gRPC, so its `_ENDPOINT` is `host:port` and `_HTTP_ENDPOINT` is the extra; the delivery channel
defaults to HTTP, so its `_ENDPOINT` is a URL and `_GRPC_ENDPOINT` is the extra. `_PLAINTEXT`
follows its `_ENDPOINT` — it is the gRPC leg's switch, and over HTTP the scheme already says it.
Those two names are the *only* difference between the families.

**The encoding is reachable only over HTTP, on either channel.** OTLP/gRPC is protobuf by
definition — the specification defines no JSON over gRPC — so a channel that speaks gRPC has one
encoding and `…_ENCODING` does not reach it. Over HTTP, `json` sends OTLP/JSON: the protobuf-JSON
mapping, one nested document per batch, which is what to send a receiver that parses documents
rather than an OTLP product. `proto` is what a conformant receiver may insist on, since the
specification makes protobuf required and JSON optional, and several managed endpoints do.

**A credential fragment selected without its values does not fail silently or loudly.** It sends the
**name of the variable you forgot** — `AIRA_OTEL_FORWARD_AUTHORIZATION-is-not-set` — which
`make otlp-inspector` shows and the destination's own rejection quotes.

### Channel 1 — observability, to your trace backend

Every span, metric and log. On by default, and the bundled Grafana is only the default destination.

| Variable | Default | What it does |
| --- | --- | --- |
| `AIRA_OTEL_BACKEND_CONFIG` | _(the empty fragment)_ | **Off is a switch of its own.** `/etc/otelcol-contrib/nobackend.yaml` leaves the base pipelines with only the two inspection exporters — for an installation that wants the access records and has no trace backend. It is not `AIRA_OTEL_ENABLED=false`, which stops the applications exporting at all, and so **both** channels. It **outranks** `_PROTOCOL_CONFIG`, so a channel that is off stays off however its transport is set. |
| `AIRA_OTEL_BACKEND_ENDPOINT` | `otel-lgtm:4317` | Where this channel sends over its default transport. `host:port`, **no scheme** — OTLP/gRPC. |
| `AIRA_OTEL_BACKEND_PLAINTEXT` | `true` | No TLS at all (h2c) on the gRPC leg. Right for the bundled backend on this network, wrong for anything across a boundary. |
| `AIRA_OTEL_BACKEND_PROTOCOL_CONFIG` | _(the empty fragment)_ | **Which transport.** Unset is OTLP/gRPC; `/etc/otelcol-contrib/backend-http.yaml` is OTLP/HTTP — and is what makes the encoding below a choice at all. Same batching, same credential, same TLS; only the exporter the pipelines use changes. A destination that is not OTLP is a recipe (below). |
| `AIRA_OTEL_BACKEND_HTTP_ENDPOINT` | `http://otel-lgtm:4318` | The HTTP leg's address, a URL; the collector appends `/v1/traces`, `/v1/logs`, `/v1/metrics`. Read only when the transport fragment is selected. |
| `AIRA_OTEL_BACKEND_ENCODING` | `json` | `json` or `proto`, on the HTTP leg. Any other value stops the collector at validation — deliberately: an endpoint can be *forgotten*, an encoding is one of two words typed on purpose. |
| `AIRA_OTEL_BACKEND_COMPRESSION` | `gzip` | `gzip` · `none` · `zstd` · `snappy`, on either transport. `none` is for a receiver that does not unwrap gzip; otherwise the symptom is a `400` about a malformed payload, which reads as *our telemetry is wrong*. |
| `AIRA_OTEL_BACKEND_AUTH_CONFIG` | _(the empty fragment)_ | **Who we say we are.** Unset sends no credential of any kind. `…/backend-auth-header.yaml` sends one header, under any name, on both transports. A credential that expires is a recipe (below). |
| `AIRA_OTEL_BACKEND_AUTH_HEADER` | `authorization` | **The header name.** `Authorization` is what a minority of OTLP receivers ask for: `x-api-key`, `api-key`, `DD-API-KEY`, `X-Honeycomb-Team`, `X-Seq-ApiKey` are all in the field. |
| `AIRA_OTEL_BACKEND_AUTHORIZATION` | _(names itself)_ | Its value, sent verbatim, scheme included. Read only by the header fragment. HTTP basic — Grafana Cloud's OTLP endpoint — is `Basic <base64 of user:password>`. |
| `AIRA_OTEL_BACKEND_CA_FILE` | _(empty)_ | A private CA, from `deploy/compose/otel/ca/`. Empty means the system trust store. **This** is the answer to a backend behind an internal CA. |
| `AIRA_OTEL_BACKEND_CLIENT_CERT_FILE` / `_CLIENT_KEY_FILE` | _(empty)_ | Mutual TLS: the backend authenticates **us** by certificate, instead of or as well as a credential. |
| `AIRA_OTEL_BACKEND_INSECURE` | `false` | Keep TLS, stop checking the certificate. A reachability test, not a deployment — and not a substitute for the CA file above. |
| `AIRA_OTEL_BACKEND_BATCH_SECONDS` | `200ms` | How long the collector holds telemetry for this channel. The default is the collector's own, so leaving it alone changes nothing. |
| `AIRA_OTEL_BACKEND_BATCH_SIZE` | `8192` | And how much fits in one request. |
| `AIRA_OTEL_BACKEND_CONSUMERS` | `10` | Concurrent senders. |
| `AIRA_OTEL_BACKEND_QUEUE` | `1000` | How much may wait while the backend says no. Held rather than dropped. |
| `AIRA_OTEL_BACKEND_RETRY_INITIAL` | `5s` | How long before asking again. |

### Channel 2 — delivery, a second destination beside it

Off unless **both** of the first two are set. `AIRA_OTEL_FORWARD_CONFIG` is the switch; the rest are
read only once it is.

The same three slots and the same knobs as channel 1 above, one prefix along — the only two names
that differ are the ones that name the transport this channel does *not* default to.

| Variable | Default | What it does |
| --- | --- | --- |
| `AIRA_OTEL_FORWARD_CONFIG` | _(the empty fragment)_ | `/etc/otelcol-contrib/forward.yaml` turns forwarding on. Anything else, including unset, merges `empty.yaml` — an empty `{}` — and nothing exists: no exporter, no filter pipeline, no second container. |
| `AIRA_OTEL_FORWARD_ENDPOINT` | `…not-set.invalid:4318` | Where to send, for a receiver that speaks plain OTLP — the collector appends `/v1/traces`, `/v1/logs`, `/v1/metrics`; a receiver with another route is a recipe. The default never resolves (RFC 2606) **on purpose**: switched on without an endpoint, this one exporter fails by name in `make otel-status` instead of stopping the collector, and everything else keeps working. |
| `AIRA_OTEL_FORWARD_ENCODING` | `json` | `json` or `proto`. The OTLP specification makes protobuf **required** and JSON optional, so a conformant receiver may refuse JSON — several managed endpoints do. Any other value stops the collector at validation. Has no effect on the gRPC leg, which is protobuf by definition. |
| `AIRA_OTEL_FORWARD_COMPRESSION` | `gzip` | `gzip` · `none` · `zstd` · `snappy`. `none` is for a receiver that does not unwrap gzip; otherwise the symptom is a `400` about a malformed payload, which reads as *our telemetry is wrong*. **Plain, uncompressed JSON** is `_ENCODING=json` with this set to `none` — there is no encoding called `none`. |
| `AIRA_OTEL_FORWARD_PROTOCOL_CONFIG` | _(the empty fragment)_ | **Which transport.** Unset is OTLP/HTTP; `/etc/otelcol-contrib/forward-grpc.yaml` is OTLP/gRPC. Same filter, same batching, same credential — only the exporter the pipelines use changes. Splunk HEC and Azure Monitor are recipes (below). |
| `AIRA_OTEL_FORWARD_GRPC_ENDPOINT` | `…not-set.invalid:4317` | `host:port`, **no scheme**. gRPC has no per-signal paths, so this is one address rather than three. Read only on the gRPC leg. |
| `AIRA_OTEL_FORWARD_GRPC_PLAINTEXT` | `false` | No TLS at all (h2c). **Not** `_INSECURE`, which keeps the encryption and stops checking the certificate. For a receiver on the same private network. |
| `AIRA_OTEL_FORWARD_AUTH_CONFIG` | _(the empty fragment)_ | **Who we say we are, as its own fragment.** Unset sends no credential header of any kind. `…/forward-auth-header.yaml` sends one header, under any name, on both transports. OAuth2 and a platform identity are recipes (below). |
| `AIRA_OTEL_FORWARD_AUTH_HEADER` | `authorization` | **The header name.** `Authorization` is what a minority of OTLP receivers ask for: `x-api-key`, `api-key`, `DD-API-KEY`, `X-Honeycomb-Team`, `X-Seq-ApiKey` are all in the field. |
| `AIRA_OTEL_FORWARD_AUTHORIZATION` | _(names itself)_ | Its value, sent verbatim, scheme included. HTTP basic is `Basic <base64 of user:password>`. Read only by the header fragment — set alone it does nothing, the same shape as the endpoint and the switch. |
| `AIRA_OTEL_FORWARD_CA_FILE` | _(empty)_ | A private CA's certificate, inside the collector — put the file in `deploy/compose/otel/ca/` and name it `/etc/otelcol-contrib/ca/<file>.crt`. Empty means the system trust store. **This** is the answer to a receiver behind an internal CA. |
| `AIRA_OTEL_FORWARD_CLIENT_CERT_FILE` / `_CLIENT_KEY_FILE` | _(empty)_ | Mutual TLS: the receiver authenticates **us** by certificate, instead of or as well as a header. |
| `AIRA_OTEL_FORWARD_INSECURE` | `false` | Skip certificate verification altogether. For a first reachability test, not for a deployment — and not a substitute for the CA file above, which answers a missing root without abandoning the check. |
| `AIRA_OTEL_FORWARD_BATCH_SECONDS` | `10s` | How long the collector holds telemetry to send it in one piece. |
| `AIRA_OTEL_FORWARD_BATCH_SIZE` | `8192` | And how much fits in one request. |
| `AIRA_OTEL_FORWARD_CONSUMERS` | `1` | Concurrent senders. The collector's own default is **ten**, and a `429` is retryable — ten senders against a quota is a loop that tightens under load. |
| `AIRA_OTEL_FORWARD_QUEUE` | `5000` | How much may wait while the receiver says no. Held rather than dropped. |
| `AIRA_OTEL_FORWARD_RETRY_INITIAL` | `5s` | How long before asking again. The default is one second, which against a quota asks the same question before the window has moved. |

**What the second destination receives is not what Grafana receives.** Traces are filtered to a
request and the calls made inside one — across a full demo run, **347 spans to Grafana and 20 to
the second destination** — because a SIEM wants one record per request and Grafana wants the SQL
underneath it. Metrics and logs go over whole.

**And they are two independent streams.** The forwarding fragment adds `traces/siem`,
`metrics/siem` and `logs/siem` with their own batching, and names none of the base pipelines — so
`AIRA_OTEL_FORWARD_*` cannot reach the trace backend's delivery. Their batching is `batch/siem` for
the same reason: `_BATCH_SECONDS` on the shared `batch` would retime both.

**And you can look at it before a SIEM exists.** `make otlp-inspector` starts a receiver in the
`debug` profile that keeps the last few hundred batches and renders them — the spans, their
`aira.*` attributes, the content type, and whether a credential was on the request. Point the
collector at `http://otlp-inspector:4318` and the page is what your SIEM would have been handed.
A debugging tool: in memory, unauthenticated, and holding attribution, so it belongs on a
developer's machine and nowhere else.

| Variable | Default | What it does |
| --- | --- | --- |
| `AIRA_PUBLISH_OTLP_INSPECTOR_PORT` | `4319` | Where the inspector's page is published. Deliberately not `4318`: the collector has that one, and two receivers at the same address is how a measurement gets taken from the wrong end of the wire. |
| `AIRA_OTLP_INSPECTOR_KEEP` | `200` | How many **batches** it keeps. A batch carries up to `AIRA_OTEL_FORWARD_BATCH_SIZE` records, so this bounds arrivals rather than records. |

### Beyond the core: recipes

The variables above are a closed core: what nearly every destination needs, the same on
both channels. A destination with other needs is a **recipe** — a fragment in
`deploy/compose/otel/recipes/`, selected through a slot that already exists. Its own values go in
`deploy/compose/otel/custom/collector.env`, which is optional, git-ignored and passed to the
collector only, under names without the `AIRA_` prefix because they are not settings of this
product. An unset one is absent, and the recipe's own fallback lets the collector start.

| recipe | slot | values it reads |
| --- | --- | --- |
| `recipes/forward-splunk-hec.yaml` | `AIRA_OTEL_FORWARD_PROTOCOL_CONFIG` | `SPLUNK_HEC_ENDPOINT` · `SPLUNK_HEC_TOKEN` · `SPLUNK_HEC_INDEX` · `SPLUNK_HEC_METRICS_INDEX` · `SPLUNK_HEC_SOURCETYPE` |
| `recipes/forward-azure-monitor.yaml` | `AIRA_OTEL_FORWARD_PROTOCOL_CONFIG` | `AZURE_MONITOR_TRACES_ENDPOINT` · `AZURE_MONITOR_LOGS_ENDPOINT` · `AZURE_MONITOR_METRICS_ENDPOINT` · `AZURE_MONITOR_CLIENT_ID` |
| `recipes/forward-oauth2.yaml` | `AIRA_OTEL_FORWARD_AUTH_CONFIG` | `OAUTH_TOKEN_URL` · `OAUTH_CLIENT_ID` · `OAUTH_CLIENT_SECRET` · `OAUTH_SCOPES` · `OAUTH_CA_FILE` |

An installation's own fragment goes in `deploy/compose/otel/custom/` and is selected as
`/etc/otelcol-contrib/custom/<file>.yaml`. `deploy/compose/otel/recipes/README.md` has the rules a
recipe keeps and the command that validates a combination before it is used.

### And one that is neither

| Variable | Default | What it does |
| --- | --- | --- |
| `AIRA_DEMO_CHAT_MODEL` / `_EMBED_MODEL` | `qwen3:0.6b` / `all-minilm` | Which models the demo pulls and catalogues. Read by the Ollama pull loop and the demo's traffic and agent tools; the seed reads them as `AIRA_SEED_LOCAL_CHAT_MODEL` / `_EMBED_MODEL`, which `docker-compose.showcase.yml` sets from these. Never read by a settings class — **demo only**; a real deployment names its models through `AIRA_OPENAI_SERVERS` or a platform's own list. |
| `AIRA_DEMO_HISTORY_DAYS` / `_PER_DAY` | `28` / `3` | How far back the showcase's usage history reaches and how busy a working day in it is. Read by `tools/demo_history.py` alone — **demo only**, and the one thing in this repository that moves a timestamp: it drives real traffic and then backdates the rows that run created, so the usage chart has a month of columns instead of one. |
| `AIRA_CONSOLE_HOST` | `localhost` | The host a **browser** types to reach the console. Read by the Keycloak realm import, and only when the realm does not yet exist, so it is not retroactive on a stack that already has one. `localhost` and `127.0.0.1` stay valid whatever it says. |

---

## 5b. Which images a deployment runs (Compose only)

**Not settings.** No settings class reads these, no `config/*.example.yaml` names them, and Vault has
no opinion about them. (`config_render.py` would not stop a config file that did: it refuses
credentials and nothing else.) They are read by Compose, when `docker-compose.release.yml` is layered
on — `RELEASE=` on a `make up*` target does that and sets `AIRA_VERSION` — and they decide **which
code runs**, which makes them worth a place in this reference anyway.

| Variable | Default | What it does |
| --- | --- | --- |
| `AIRA_VERSION` | **none — required** | The published version to run: `v0.1.1`. Unset is a refusal naming the variable, before a container starts. There is deliberately no default: `latest` moves under a restart, so a deployment that was pinned yesterday would not be pinned today and nothing would say so. `make current-version` prints the newest version this checkout's tags name (`make next-version` is the other question: what a release cut now would be called). |
| `AIRA_IMAGE_REGISTRY` | `ghcr.io/inigoimago` | Everything before the image name — registry and owner. A fork publishes to its own namespace. Lower case: **GHCR rejects an upper-case path**, which fails at the push, after a build. |
| `AIRA_PULL_POLICY` | `missing` | What Compose does when the image is not already local. `missing` is right for a version, which is immutable — asking the registry again on every start buys nothing and fails without a network. Set `always` when running a tag that **moves** (`latest`, `sha-…`), where the local copy and the registry's are not the same bytes. `build` is also a legal value for Compose and would be this overlay undoing itself. |
| `AIRA_IMAGE_TAG` | `dev` | The *locally built* tag, for the ordinary build path. Nothing to do with a release: it names an image on this machine only. |

The addresses an integrated deployment must supply — `AIRA_POSTGRES_HOST`,
`AIRA_KAFKA_BOOTSTRAP_SERVERS`, `AIRA_OIDC_ISSUER`, `AIRA_OIDC_JWKS_URI`, and `AIRA_REDIS_URL` /
`VAULT_ADDR` as deliberate blanks — are ordinary settings, documented in their own sections above.
What `docker-compose.external.yml` changes is only that they become **required**: their defaults one
file down name the containers of the stack this repository ships beside, which resolve to nothing in
a deployment. See [`deployment/integrated.md` §5a](deployment/integrated.md#5a-running-the-published-images-against-your-infrastructure).

---

## 6. What happens when something is missing

Degradation is **decided**, not accidental. `/readyz` returns **200 with `degraded: true`** — the
service still works, with a named control on a fallback — and the degradation is frozen onto each
audit row, so a request can be read in the light of the conditions it actually met.

| Missing                                | Effect                                                                                            | Why this way                                                                                                                                    |
| -------------------------------------- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Redis**                              | rate limits become **per-instance**; budgets fall back to the Postgres path (enforcing, but racy) | The moment a control stops working is the worst moment to stop applying it. Bounding per instance is wrong-but-bounded; fail-open is unbounded. |
| **Kafka**                              | configuration stops arriving; what has arrived keeps working. Both planes stay ready, reporting `degraded: true`; the cluster counts as reachable while any broker answers | The gateway serves from its read-model and Management queues changes in its outbox. A bus outage must not be an API outage, nor take every instance out of rotation at once. |
| **Postgres**                           | the instance is **not ready**: `/readyz` answers `503` on both planes — Postgres alone decides readiness — so it is taken out of rotation. Under Compose the services wait behind their migrations; with `AIRA_DEMO_MODE` the gateway stops at start, writing the demo key | It is the audit trail and the read-model. Serving without recording is the one thing this product must not do.                                   |
| **Vault** (configured — an address and a role id or token — but unreachable) | the process **stops**                                           | Falling back turns a broken secret store into a service that starts, looks healthy and runs on a stale value.                                   |
| **An upstream**                        | `/readyz` reports `degraded`, requests to it fail with its status passed through                  | Unreachable is degraded, not down.                                                                                                              |
| **The retention worker**               | nothing is ever deleted                                                                           | Silent, which is why it is called out here and in [`deployment/integrated.md` §7](deployment/integrated.md#7-migrate-then-start).                |
| **A Kafka topic**                      | the relay stops at the first event for it: the run fails naming the event and the topic, and that event and every later one — on every topic — stay pending until the topic exists | Order is meaning, so nothing behind a failed event is sent first. Configuration distribution halts, and says where. `make kafka-topics` and the `kafka-topics` one-shot create every topic the code publishes to. |

---

## 7. Secrets from Vault

`aira_common.secrets` does AppRole + KV-v2 and is wired in as a **pydantic settings source** that
ranks **above** the environment — not as an injection into `os.environ`, because values placed there
are readable from `/proc`, inherited by every subprocess, and dumped by any library that panics.

> **Unlike every setting in this document, these carry no `AIRA_` prefix, and that is not a typo.**
> They are HashiCorp's own names, read by `aira_common.secrets` before any settings class exists —
> the secret store has to be reachable *before* the thing it configures. A prefixed spelling such as
> `AIRA_VAULT_ADDRESS` is read by nothing: Vault stays **off**, and every credential comes quietly
> from the environment. Confirm where a process's secrets came from with the gateway's `/readyz`
> (`secrets.source`, `vault` or `environment`; `make vault-status` prints it) — shown to anyone under
> `local` or `AIRA_DEMO_MODE`, and otherwise only to an operator credential. Management's `/readyz`
> does not report it.

**Vault is used only when `VAULT_ADDR` is set together with `VAULT_ROLE_ID` (AppRole) or
`VAULT_TOKEN`.** An address alone reads nothing and says nothing: every secret then comes from the
environment, exactly as with no address at all. Whether that should refuse to start is an open
decision; today it does not.

| Variable                 | Default  | What it does                                                                   |
| ------------------------ | -------- | ------------------------------------------------------------------------------ |
| `VAULT_ADDR`             | —        | Where Vault is. Not enough on its own — see above. Outside `local` a plain `http://` address that is not loopback refuses to boot on both planes, even while Vault is not in use ([§8](#8-what-refuses-to-boot)). |
| `VAULT_ROLE_ID`          | —        | AppRole role id. With an address, this turns Vault on, and a secret id must then be found — or the process stops. |
| `VAULT_SECRET_ID`        | —        | The secret id itself. **Wins** when the file below is set as well. A file is better: a variable is readable from `/proc` and inherited by every subprocess. |
| `VAULT_SECRET_ID_FILE`   | —        | Path to a file holding the secret id — the better place for it. Read only while `VAULT_SECRET_ID` is empty; a path that cannot be read stops the process. |
| `VAULT_TOKEN`            | —        | A plain token instead of AppRole, used only while `VAULT_ROLE_ID` is empty; every use logs a warning. Dev only: the Compose stack's dev Vault has the root token `root`, which its `vault-init` uses to provision; the applications receive a token only if `.env` sets one. |
| `VAULT_MOUNT`            | `secret` | KV-v2 mount.                                                                    |
| `VAULT_PATH`             | `aira`   | Path within the mount.                                                          |
| `VAULT_NAMESPACE`        | —        | Vault Enterprise namespace.                                                     |
| `VAULT_TIMEOUT`          | `10`     | Seconds. An empty value falls back to the default rather than raising; anything but a positive number refuses to start, naming this variable. |

Keys **inside** Vault use the settings' own names, with or without the `AIRA_` prefix and in either
case (`google_api_key` is `AIRA_GOOGLE_API_KEY`) — those are the values being fetched, and they are
unrelated to the connection variables above. Two spellings of one setting with different values
refuse to start.
**Rotation is a restart**: secrets are read once, at start-up — a decision, not an omission.
"Vault is down" and "nobody wrote that key" are different exceptions and are reported differently.

---

## 8. What refuses to boot

Safe defaults are enforced, not documented.
Each of the following is a state where starting successfully would be worse than not starting: the
operator would have a service that looks healthy and is not doing what they believe it is doing.

**Outside `AIRA_ENVIRONMENT=local`** — hardening that a development machine legitimately
waives, so that `make up`, the demo and a laptop's zero-configuration start keep working.

| Refuses to boot when | Plane | Waived by `AIRA_DEMO_MODE`? |
| --- | --- | :--: |
| the Postgres password is still the published development default | gateway | yes |
| an OIDC issuer has no audience — on the gateway only while `AIRA_OIDC_ENABLED` is on | both | gateway only |
| Kafka speaks `PLAINTEXT` while a broker is configured | both | gateway only |
| an OIDC issuer, a JWKS URI or `VAULT_ADDR` is plain `http://` to anything but loopback — `VAULT_ADDR` even while Vault is not in use | both | gateway only |
| `AIRA_AUTH_REQUIRED` is off | gateway | **no** |
| `AIRA_REQUIRE_USE_CASE` is off | gateway | **no** |
| `AIRA_OIDC_ENABLED` is on and no issuer is configured | gateway | **no** |
| `AIRA_OIDC_ISSUER` is unset | management | **no** |
| `AIRA_TEST_DATABASE` is on | both | **no** |
| `AIRA_SECRET_KEY` is unset or still the development value | management | **no** |
| `AIRA_DEBUG` is on — its default | management | **no** |
| `AIRA_ALLOWED_HOSTS` is `*` — its default | management | **no** |
| `AIRA_ROLE_GROUPS` names no global-admin group | management | **no** |
| `AIRA_PRIVACY_CONTROLLER` is empty | management | **no** |

That is eight kinds of refusal on the gateway and ten on Management. `AIRA_TEST_DATABASE` would run
a plane on an empty in-memory database that loses every row on restart; only the test harness sets
it.

**A demo is a gateway concession, not an installation-wide one**, and the difference is not
cosmetic. `WAIVED_BY_A_DEMO` (`gateway/src/aira_gateway/security.py`) names six settings — the
Postgres password, the audience, the issuer and JWKS URI as plain `http://`, `PLAINTEXT` Kafka, and
`VAULT_ADDR` as plain `http://` — and waives the checks above for those and nothing else. Management
has no equivalent and waives *nothing*: its `is_local` is `environment == "local"` alone. No value of
`AIRA_ENVIRONMENT` is special either — `demo` is as much *not local* as `production`.

So **`AIRA_DEMO_MODE` outside `local` gives you a stack whose gateway starts and whose control plane
refuses.** The demo shape works on `local`, where both planes waive everything in the table.

Whether Management *should* have a demo waiver is an open question and deliberately not answered
here: it holds the `SECRET_KEY` that signs every administrator's session, and a published one on a
reachable network is session forgery rather than an inconvenience. It is a decision still to take,
not a gap to close quietly.

**In every environment, `local` included** — these are not hardening but *correctness*, and a
local process that started anyway would be doing something different from the one it stands in for.

Both planes:

- a value the settings cannot read — a type it cannot hold (a port that is not a number), a Kafka
  stage or broker entry nothing could dial, an OIDC clock tolerance outside 0–300 s, an unknown
  name in `AIRA_DEBUG_INTEGRATIONS`
- `AIRA_ROLE_GROUPS` that is malformed: an unknown or retired role name, a path not starting with
  `/`, a role with no group
- `VAULT_TIMEOUT` that is not a number of seconds above zero — read even while Vault is not in use
- Vault is configured and cannot be used: unreachable, the login refused, nothing at its path, no
  secret id beside a role id or a secret-id file that cannot be read, or one setting held under two
  spellings with different values
- `AIRA_OTEL_SAMPLE_RATIO` outside 0–1 while export is on
- `AIRA_DEFAULT_RETENTION_DAYS` outside 1–3650

The gateway:

- CORS allows `*` **and** credentials: a browser rejects the combination, and a server that
  reflects the origin instead lets any site call the API with a user's credentials
- a configured model sits in a region outside `AIRA_ALLOWED_REGIONS`, and is not permitted it by
  name in `AIRA_ALLOWED_MODEL_REGIONS` — Vertex, Foundry, AI Studio and an OpenAI-compatible server
  that names a region alike
- a configured Vertex model is not `region/publisher/model`, or names a publisher no adapter speaks
- Vertex credentials that are not a service account's JSON, or whose key cannot sign
- a Foundry endpoint with deployments and no key, or a deployment that names no region
- an OpenAI-compatible server with no URL or no models
- two upstream adapters claim the same model name
- Redis sentinels with no service name, or an entry that is not `host:port`
- an `AIRA_OIDC_ISSUERS` entry with no issuer, while OIDC is on
- a redaction pattern that is invalid or backtracks catastrophically
- an `AIRA_ALLOWED_MODEL_REGIONS` entry that is not `region=model,model`
- a size bound, interval or timeout of `0` or below; a negative failure limit, reservation estimate,
  audit queue size or `AIRA_TRUSTED_PROXY_HOPS`

Management:

- an `AIRA_THROTTLE_USER` rate DRF cannot read or that admits nothing, and an
  `AIRA_THROTTLE_AUTH_FAILURES` that is not `<count>/<period>` — a bare `0` included
- `AIRA_API_KEY_MAX_DAYS` below 1, or `AIRA_API_KEY_DEFAULT_DAYS` outside 1 to the maximum
- a negative `AIRA_TRUSTED_PROXY_HOPS` (the gateway refuses it too, among its bounds above)
- an `AIRA_PRIVACY_NOTICE_MODE` other than `once`, `monthly` or `always`, or a default language the
  notice has no text in

That distinction is worth stating because the second group surprises people locally. Setting
`AIRA_GOOGLE_API_KEY` on a default configuration stops the gateway starting: AI Studio answers on
the **global** endpoint, `global` is not in the EU-only default of `AIRA_ALLOWED_REGIONS`, and
residency is not a thing this product enforces only in production. Widen `AIRA_ALLOWED_REGIONS`
deliberately, or use a regional platform.

`make config-check` builds both planes' settings from the rendered file alone and asks their
`unsafe_settings` — so it reports the table above, Management's whole list, and every value a
settings class refuses to read. It builds no upstream, CORS policy, redactor or telemetry, so the
rest of the gateway's list, and the sample ratio, are first met at start.
