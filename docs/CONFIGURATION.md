# Configuration reference

Every setting, what it is for, and what happens if you get it wrong.

**All variables are prefixed `AIRA_`** and read by [pydantic-settings]. The defaults below are the
ones in the code, so they are the values a process uses when nothing sets them. Both services also
read the same variable names from **Vault** when one is configured, and Vault **wins** over the
environment ([§7](#7-secrets-from-vault)).

[pydantic-settings]: https://docs.pydantic.dev/latest/concepts/pydantic_settings/

| Jump to                                                       |                                                |
| ------------------------------------------------------------- | ---------------------------------------------- |
| [1 Common](#1-common-to-both-services)                        | logging, environment, currency, tracing        |
| [2 Gateway](#2-gateway)                                       | the data plane                                 |
| [3 Model platforms](#3-model-platforms)                       | Vertex, Foundry, OpenAI-compatible, Gemini API |
| [4 Management](#4-management)                                 | the control plane                              |
| [5 Frontend](#5-frontend)                                     | build-time, not run-time                       |
| [6 Missing pieces](#6-what-happens-when-something-is-missing) | degradation, decided                           |
| [7 Vault](#7-secrets-from-vault)                              |                                                |
| [8 Safe defaults](#8-what-refuses-to-boot)                    | what refuses to boot                           |

---

## 1. Common to both services

| Variable                                                       | Default                                                    | What it does                                                                                                                                                                      |
| -------------------------------------------------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_ENVIRONMENT`                                             | `local`                                                    | `local` \| `demo` \| anything else. Not cosmetic: outside `local`, dev secrets and `DEBUG` **refuse to boot** ([§8](#8-what-refuses-to-boot)), and `seed_demo` refuses to run.    |
| `AIRA_LOG_LEVEL`                                               | `INFO`                                                     |                                                                                                                                                                                   |
| `AIRA_LOG_JSON`                                                | `true`                                                     | Structured logs. `false` gives human-readable lines for a terminal.                                                                                                               |
| `AIRA_DEMO_MODE`                                               | `false`                                                    | Enables the demo API key and lets `seed_demo` run. **Never in production.**                                                                                                       |
| `AIRA_CURRENCY`                                                | `EUR`                                                      | One installation, one currency. Appears in every spend figure and in the CSV column header — a number without a unit in a file that gets forwarded is a number nobody can act on. |
| `AIRA_OTEL_ENABLED`                                            | `false`                                                    | OTLP export of traces, metrics and logs.                                                                                                                                          |
| `AIRA_OTEL_ENDPOINT`                                           | `http://localhost:4318`                                    | Collector endpoint (HTTP/protobuf).                                                                                                                                               |
| `AIRA_OTEL_SAMPLE_RATIO`                                       | `1.0`                                                      | 1.0 = every trace.                                                                                                                                                                |
| `AIRA_POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | `localhost` / `5432` / _(differs)_ / `aira` / `aira-local` | **Two different databases** — see the note below.                                                                                                                                 |
| `AIRA_KAFKA_BOOTSTRAP_SERVERS`                                 | `localhost:29092`                                          |                                                                                                                                                                                   |

### How this service authenticates to Kafka

**`PLAINTEXT` is refused outside `local`.** Both planes *apply* what arrives on these topics — the
gateway builds the read-model its authorization comes from out of them — so an unauthenticated
broker is a way to grant yourself administrator rights on any use case with no credential and no
audit row. That is why the check is a startup refusal rather than a warning.

| Variable                       | Default     | What it does                                                                                              |
| ------------------------------ | ----------- | --------------------------------------------------------------------------------------------------------- |
| `AIRA_KAFKA_SECURITY_PROTOCOL` | `PLAINTEXT` | `PLAINTEXT` \| `SSL` \| `SASL_PLAINTEXT` \| `SASL_SSL`. Anything but `PLAINTEXT` outside `local`.        |
| `AIRA_KAFKA_SASL_MECHANISM`    | —           | e.g. `SCRAM-SHA-512`, `PLAIN`. Required by the two `SASL_*` protocols.                                     |
| `AIRA_KAFKA_SASL_USERNAME`     | —           | SASL user. **A secret** — see §7; put it in Vault rather than in the environment.                          |
| `AIRA_KAFKA_SASL_PASSWORD`     | —           | SASL password. **A secret** — same.                                                                        |
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
| `AIRA_AUTH_REQUIRED`                | `true`      | `false` opens every route — a demo switch, never production. With it off there is no identity, so role checks are skipped rather than guessed.                                                                                                                              |
| `AIRA_OIDC_ENABLED`                 | `false`     | Accept OIDC bearer tokens in addition to API keys. **Required** for the SPA's dry-run, consumption, reporting and incident views.                                                                                                                                           |
| `AIRA_OIDC_ISSUER`                  | —           | e.g. `http://localhost:8080/realms/aira`                                                                                                                                                                                                                                    |
| `AIRA_STACK`                        | `aira`      | Prefixes every container name. Docker refuses a duplicate name, so without this a second stack cannot start beside the first whatever its ports are. Read by Compose only. |
| `AIRA_PUBLISH_*_PORT`               | _(today's)_ | Which **host** port each service is published on — `AIRA_PUBLISH_POSTGRES_PORT`, `…_KEYCLOAK_PORT`, `…_KAFKA_PORT`, `…_REDIS_PORT`, `…_GRAFANA_PORT`, `…_VAULT_PORT`, `…_SCHEMA_REGISTRY_PORT`, `…_OTLP_GRPC_PORT`, `…_OTLP_HTTP_PORT`, `…_OLLAMA_PORT`, `…_GATEWAY_PORT`, `…_MANAGEMENT_PORT`, `…_FRONTEND_PORT`. Eleven of these were literals until 2026-08-18, so a second system on the same machine collided and there was no way out but editing the compose file. **Its own prefix on purpose**: `AIRA_POSTGRES_PORT` is a *setting* — the port the gateway connects to — and one name for both meanings would make moving the published port silently redirect the in-network connection. |
| `AIRA_OIDC_CLIENT_ID`               | `aira-gateway` | The console's OIDC client. Written into `runtime-config.js` by the console's entrypoint at container start, together with `AIRA_OIDC_ISSUER`; the CSP's `connect-src` is derived from the issuer's origin. One variable moves all three — the file used to be static in the image and the header a second variable that "had to agree". |
| `AIRA_OIDC_ISSUERS`                 | —           | **Several Keycloak realms at once** (`FRD-118`), for one organisation whose people live in more than one — a migration, a second instance, a merger. `issuer\|audience\|jwks_uri` per entry, `;`-separated, the JWKS URI optional. Empty means the single pair below, which is what most deployments keep. A token is routed by its own `iss` and then verified against that realm properly; **every** entry needs an audience outside `local` or the gateway refuses to start. The realms are trusted equally — the same group path from either grants the same access, which holds because they describe one population. |
| `AIRA_OIDC_AUDIENCE`                | —           | The audience this service answers to. Enforced when set — and **required outside `AIRA_ENVIRONMENT=local`**: without it, any token the issuer minted is accepted, including one issued to a different client (`ADR-0015`). The gateway and Management both refuse to start. |
| `AIRA_OIDC_CLOCK_SKEW_SECONDS`      | `60`        | How far the **issuer's** clock may run ahead of this host's, for `iat` and `nbf` (`FRD-134`). Costs nothing — a token that is "too new" was still genuinely minted — and at `0` a service one second behind its issuer refuses **every** freshly minted token as `401`, which reads exactly like a wrong secret. Refused above 300 s at startup. |
| `AIRA_OIDC_EXPIRY_LEEWAY_SECONDS`   | `0`         | How long past `exp` a token is still accepted. **This is the half with a cost**: it extends a credential's life beyond what the issuer granted, so it is off. The predecessor grants 60 s here; a client that needs it has a broken refresh strategy, and an installation may still choose to absorb it. Refused above 300 s at startup. |
| `AIRA_OIDC_JWKS_URI`                | _(derived)_ | Only when it is not `<issuer>/protocol/openid-connect/certs`.                                                                                                                                                                                                               |
| `AIRA_REQUIRE_USE_CASE`             | `true`      | Refuse an authenticated request that names no use case. **Default since 2026-08-11**, and turning it off outside `local`/demo refuses to start: without a use case there is nothing to budget, limit or attribute, and a request was served at 200 with `use_case = NULL`. The unbound break-glass key (`ADR-0015`) keeps its exemption.                                                                                                    |
| `AIRA_API_KEY_DEFAULT_DAYS`         | `30`        | Lifetime of a newly issued API key. **A key is always bounded** — omitting the lifetime at issuance takes this value, and there is no way to ask either plane for one that never expires (`ADR-0015`). Read by both planes from one definition.                             |
| `AIRA_API_KEY_MAX_DAYS`             | `180`       | The longest lifetime anybody may request. More is **refused by name**, with the maximum in the message — a silently shortened lifetime would leave the requester believing a date that is not in the database.                                                              |
| `AIRA_MAX_AUTH_FAILURES_PER_MINUTE` | `60`        | Failed authentications one source address may make before it is answered 429 with `Retry-After`. Counts **refusals only**, so a working credential never touches it however busy the caller is. `0` disables it (an installation whose WAF already does this).              |
| `AIRA_REDACT_PATTERNS`              | —           | Extra regexes (`;`- or newline-separated) redacted from stored payloads, **added to** the built-in credential shapes, never replacing them (`FRD-406`). An invalid or exponentially-backtracking pattern stops the gateway rather than silently matching nothing.           |
| `AIRA_TRUST_FORWARDED_FOR`          | `false`     | Read the client IP from `X-Forwarded-For`. Only enable behind a proxy you control — otherwise any caller can write any address into your audit trail.                                                                                                                       |
| `AIRA_DIRECTORY_CLIENT_ID`          | —           | Management only. A **read-only** Keycloak service account (`view-users`, `query-groups`) so the console can search your groups and people when granting access (`FRD-209`). Without it the console offers what it already knows and says so.                                |
| `AIRA_DIRECTORY_CLIENT_SECRET`      | —           | Its secret. From Vault in any real deployment.                                                                                                                                                                                                                              |

| Variable                   | Default | What it does                                                                                                                                                                                                                                                                                                        |
| -------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_TRUSTED_PROXY_HOPS`  | `1`     | How many reverse proxies **append** to `X-Forwarded-For` in front of the gateway. The address is read that many entries **from the right**, never from the left: a proxy appends, so the left end is whatever the caller sent. Reading the left end let a caller choose the address in the audit trail — and rotate it to defeat the failed-authentication bound. Raise by one per additional proxy. A chain shorter than this is a request that did not come through them, and its header is ignored in favour of the socket peer. Only consulted when `AIRA_TRUST_FORWARDED_FOR` is on. |
| `AIRA_ROLE_GROUPS`         | —       | Which Keycloak group confers which AIRA role (`ADR-0017`), as `role=/path[,/path];role=/path`. **Group membership is the only source of a role** — a realm role on the same token is not read. Empty grants no oversight to anybody, which is the safe direction for a data plane; Management is the plane that refuses to boot without a global-admin group, because it is the one an installation is repaired from. |

### Storage and retention

| Variable                      | Default | What it does                                                                                                                                                     |
| ----------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_STORE_PAYLOADS`         | `true`  | Operator **kill switch** above the per-use-case setting: a use-case admin may decline storage but cannot re-enable it where the operator forbade it.             |
| `AIRA_DEFAULT_RETENTION_DAYS` | `7`     | For traffic that carries no use case. Use-case traffic follows its own period.                                                                                   |
| `AIRA_LOG_RETENTION_DAYS`     | `0`     | Whole-row deletion. **0 = never**, deliberately: payload retention and record retention are separate clocks, and deleting rows would truncate the spend history. |
| `AIRA_LOG_QUEUE_SIZE`         | `512`   | Audit-write queue. `0` writes on the request path — supported, and the cost is the latency the queue exists to remove.                                           |

### Controls

| Variable                             | Default                    | What it does                                                                                                                                                                             |
| ------------------------------------ | -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_ENFORCE_BUDGETS`               | `true`                     |                                                                                                                                                                                          |
| `AIRA_ENFORCE_RATE_LIMITS`           | `true`                     |                                                                                                                                                                                          |
| `AIRA_ENFORCE_SUSPENSIONS`           | `true`                     | `false` records findings and refuses nobody — the setting to use while learning what your rules do.                                                                                      |
| `AIRA_DETECT_ANOMALIES`              | `true`                     | `false` stops evaluation entirely; rules stay authored. "Switched off" and "deleted" are different states.                                                                               |
| `AIRA_ANOMALY_INTERVAL_SECONDS`      | `60`                       | How often the detector wakes. It evaluates only scopes that saw traffic, so a longer interval costs findings _latency_, not accuracy.                                                    |
| `AIRA_BUDGET_ESTIMATE_OUTPUT_TOKENS` | `1024`                     | What a reservation assumes an answer will cost when the caller sets no cap. Too low under-reserves and lets concurrent requests overshoot; too high refuses traffic that would have fit. |
| `AIRA_REDIS_URL`                     | `redis://localhost:6379/0` | The shared counter store. See [§6](#6-what-happens-when-something-is-missing).                                                                                                           |

### Input bounds

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
| `AIRA_CORS_ORIGINS`           | —                                       | Comma-separated allow-list.                                                                                                                                                                                                                                                                                                                                              |
| `AIRA_CORS_ALLOW_CREDENTIALS` | `false`                                 | `*` **plus** credentials is refused at startup rather than silently ignored.                                                                                                                                                                                                                                                                                             |

### Compatibility surface and build identity

| Variable                                                      | Default | What it does                                                           |
| ------------------------------------------------------------- | ------- | ---------------------------------------------------------------------- |
| `AIRA_KIRA_SUNSET`                                            | —       | Date advertised in the `Sunset` header on the KIRA surface.            |
| `AIRA_BUILD_NUMBER` / `_TIME` / `AIRA_GIT_COMMIT` / `_BRANCH` | —       | Reported by `/readyz` so a running instance can say which build it is. |
| `AIRA_APP_NAME`                                               | `aira-gateway` / `aira-management` | The service name in logs and OpenTelemetry resource attributes. Changing it renames the service in every trace and dashboard, so it is left alone unless two installations share one collector. |
| `AIRA_TEST_DATABASE`                                          | `false` | **Test harness only.** Points the engine at an in-memory SQLite instead of Postgres. Never set in a deployment: the schema differs (SQLite enforces no column lengths), so it would hide exactly the failures Postgres reports. |

---

## 3. Model platforms

Each platform is registered **only when configured**. An unconfigured one is simply absent — no
placeholder, no half-working adapter. An ambiguous routing table (two adapters claiming one model)
**refuses to boot**: with three platforms, last-registration-wins is a silent choice of region and
credential.

### Google Vertex AI — Gemini _and_ Anthropic, EU-regional

| Variable                         | Default | What it does                                                                      |
| -------------------------------- | ------- | --------------------------------------------------------------------------------- |
| `AIRA_VERTEX_PROJECT`            | —       | GCP project id.                                                                   |
| `AIRA_VERTEX_CREDENTIALS`        | —       | Service-account JSON, or a path to it.                                            |
| `AIRA_VERTEX_API_KEY`               | —           | The **other** credential the same adapter takes (`FRD-115` FR-3a): an Agent Platform API key, sent as `x-goog-api-key`. For accounts that never create a service account — Google issues these, and AIRA's only API-key path used to be AI Studio, on a host that refuses them with `API_KEY_SERVICE_BLOCKED`. **Same regional hosts, same residency check**: this does not use Google's global express endpoint, which processes data anywhere. Set both and the service account wins. From the environment or Vault. |
| `AIRA_VERTEX_MODELS`             | —       | `name=publisher/model@region`, comma-separated.                                   |
| `AIRA_VERTEX_TIMEOUT_SECONDS`    | `120`   |                                                                                   |
| `AIRA_VERTEX_DEFAULT_MAX_TOKENS` | `4096`  | Anthropic requires `max_tokens`; this is what is sent when the caller names none. |

### Microsoft Foundry — Azure OpenAI and Microsoft's own models

| Variable                       | Default      | What it does                                                                                                                                         |
| ------------------------------ | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_FOUNDRY_ENDPOINT`        | —            | `https://<resource>.openai.azure.com`                                                                                                                |
| `AIRA_FOUNDRY_API_KEY`         | —            | Or Entra; the token is minted per request rather than captured once.                                                                                 |
| `AIRA_FOUNDRY_DEPLOYMENTS`     | —            | `model=deployment@region`. An Azure **deployment** is not a model: pricing attaches to the underlying model, or spend figures go quietly incomplete. |
| `AIRA_FOUNDRY_API_VERSION`     | `2024-10-21` |                                                                                                                                                      |
| `AIRA_FOUNDRY_TIMEOUT_SECONDS` | `120`        |                                                                                                                                                      |

### OpenAI-compatible servers (self-hosted, e.g. Ollama or vLLM)

| Variable                                                        | Default | What it does                                                                                                                                                    |
| --------------------------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_OPENAI_SERVERS`                                           | —       | `name=url\|models\|embeddings\|region`, semicolon-separated. **Named**, because a self-hosted fleet is several machines and each is audited under its own name. |
| `AIRA_OLLAMA_URL` / `_MODELS` / `_EMBEDDING_MODELS` / `_REGION` | —       | Single-server shorthand for the same thing.                                                                                                                     |
| `AIRA_OLLAMA_TIMEOUT_SECONDS`                                   | `300`   | High on purpose: a self-deployed model cold-starts.                                                                                                             |

> A model name **may contain a colon** (`qwen3:0.6b`). Splitting on the first one produced
> "Model 'qwen3' not found" — a live-only defect, now a test.

### Google Gemini public API

| Variable               | Default                                            | What it does                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ---------------------- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_GOOGLE_API_KEY`  | —                                                  | **Google AI Studio** (`generativelanguage.googleapis.com`). Registers the provider when set. The key is never logged, and `?key=` is redacted from spans. Its region is `global` and it is checked like every other adapter's, so under the EU default the gateway **refuses to start** until `global` is named in `AIRA_ALLOWED_REGIONS` — this endpoint gives no regional guarantee, which is the whole difference from Vertex. |
| `AIRA_GEMINI_MODELS`   | `gemini-2.0-flash,gemini-1.5-flash`                |                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `AIRA_GEMINI_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta` |                                                                                                                                                                                                                                                                                                                                                                                                                                   |

---

## 4. Management

| Variable                                       | Default       | What it does                                                                                                |
| ---------------------------------------------- | ------------- | ----------------------------------------------------------------------------------------------------------- |
| `AIRA_SECRET_KEY`                              | _(dev value)_ | Django signing key. The dev value **refuses to boot** outside `local`.                                      |
| `AIRA_DEBUG`                                   | `true`        | Forced off outside `local`.                                                                                 |
| `AIRA_ALLOWED_HOSTS`                           | `*`           | Comma-separated. `*` **refuses to boot** outside `local`.                                                   |
| `AIRA_OIDC_ISSUER` / `_AUDIENCE` / `_JWKS_URI` | —             | Same shape as the gateway's. Management **requires** OIDC: there is no API-key path into the control plane. |
| `AIRA_THROTTLE_AUTH_FAILURES`                  | `60/minute`   | Failed authentications one source address may collect. A presented token is verified against the issuer's JWKS **before** anything decides it is invalid, so probing is not free. Counts **refusals only**, so a working credential never touches it. `0` disables it. Not a DRF throttle — an `AnonRateThrottle` never fires here, because permissions are checked before throttles and every view requires authentication. |
| `AIRA_THROTTLE_USER`                           | `600/minute`  | The same for a signed-in caller, sized to stop a script rather than to shape ordinary use — a console screen loads five panels at once. **Per process** (Django's `LocMemCache`), so N workers admit N × the rate; point `CACHES` at Redis to make it exact. |

Management has no `AUTH_REQUIRED` switch on purpose — a control plane that can be opened is a
control plane somebody opens.

---

## 5. Frontend

The SPA is configured **at deployment time**: `public/runtime-config.js` ships beside the bundle and
names the OIDC issuer and client id, so one image serves any realm —
[`INTEGRATIONS.md` §7](INTEGRATIONS.md#7-the-spa-is-configured-at-deployment-time).

nginx takes three values:

| Variable                    | Default                        | What it does                                                                    |
| --------------------------- | ------------------------------ | ------------------------------------------------------------------------------- |
| `AIRA_MANAGEMENT_UPSTREAM`  | `http://management:8002`       | Where `/api` goes.                                                              |
| `AIRA_GATEWAY_UPSTREAM`     | `http://gateway:8001`          | Where `/gw` goes.                                                               |
| `AIRA_CSP_CONNECT_SRC`      | `'self' http://localhost:8080` | What the console's content policy lets it call. **Set it with the issuer**: the token request goes to Keycloak cross-origin, and a policy that does not name it produces a login that fails in the browser and nowhere else. |

Its resolver must **re-resolve** upstreams, or a container restart behind a changed IP is a 502 that
outlives the restart. And `/gw` must not buffer: two verbs are SSE, and nginx buffers by default —
the shipped configuration sets `proxy_buffering off` and a read timeout above the gateway's own.

The local Compose stack publishes every port on `AIRA_BIND_HOST` (default `127.0.0.1`). It runs
Postgres, Redis, Kafka and a dev-mode Vault with the credentials printed in `.env.example`, and
Compose's plain `"5432:5432"` would put all of them on every interface of the machine.

---

## 6. What happens when something is missing

Degradation is **decided**, not accidental. `/readyz` returns **200 with `degraded: true`** — the
service still works, with a named control on a fallback — and the degradation is frozen onto each
audit row, so a request can be read in the light of the conditions it actually met.

| Missing                                | Effect                                                                                            | Why this way                                                                                                                                    |
| -------------------------------------- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Redis**                              | rate limits become **per-instance**; budgets fall back to the Postgres path (enforcing, but racy) | The moment a control stops working is the worst moment to stop applying it. Bounding per instance is wrong-but-bounded; fail-open is unbounded. |
| **Kafka**                              | configuration stops arriving; what has arrived keeps working                                      | The gateway serves from its read-model. A bus outage must not be an API outage.                                                                 |
| **Postgres**                           | the gateway does not start                                                                        | It is the audit trail. Serving without recording is the one thing this product must not do.                                                     |
| **Vault** (configured but unreachable) | the process **stops**                                                                             | Falling back turns a broken secret store into a service that starts, looks healthy and runs on a stale value.                                   |
| **An upstream**                        | `/readyz` reports `degraded`, requests to it fail with its status passed through                  | Unreachable is degraded, not down.                                                                                                              |
| **The retention worker**               | nothing is ever deleted                                                                           | Silent, which is why it is called out here and in [`SETUP.md`](SETUP.md#5-integrated--onto-your-own-infrastructure).                            |
| **A Kafka topic**                      | the change is **silently** dropped                                                                | Nothing errors anywhere. A test now keeps the topic lists in step with the code.                                                                |

---

## 7. Secrets from Vault

`aira_common.secrets` does AppRole + KV-v2 and is wired in as a **pydantic settings source** that
ranks **above** the environment — not as an injection into `os.environ`, because values placed there
are readable from `/proc`, inherited by every subprocess, and dumped by any library that panics.

> **These are the only variables in this document with no `AIRA_` prefix, and that is not a typo.**
> They are HashiCorp's own names, read by `aira_common.secrets` before any settings class exists —
> the secret store has to be reachable *before* the thing it configures. This table said
> `AIRA_VAULT_ADDRESS` until 2026-08-18, which is a name nothing reads: an operator following it
> set the prefixed form, Vault stayed **off**, and every credential came quietly from the
> environment. That is the exact failure `secrets_state()` was written for after it cost three
> days once already — and the document had been sending readers back into it. Confirm with
> `/readyz`, which reports where this process's secrets came from.

| Variable                 | Default  | What it does                                                                   |
| ------------------------ | -------- | ------------------------------------------------------------------------------ |
| `VAULT_ADDR`             | —        | **Enables Vault when set.** Unset means every secret comes from the environment. |
| `VAULT_ROLE_ID`          | —        | AppRole role id.                                                                |
| `VAULT_SECRET_ID_FILE`   | —        | Path to a file holding the secret id. Preferred over the variable below.        |
| `VAULT_SECRET_ID`        | —        | The secret id itself. A file is better: a variable is readable from `/proc` and inherited by every subprocess. |
| `VAULT_TOKEN`            | —        | A plain token instead of AppRole. Dev only — the Compose stack uses `root`.     |
| `VAULT_MOUNT`            | `secret` | KV-v2 mount.                                                                    |
| `VAULT_PATH`             | `aira`   | Path within the mount.                                                          |
| `VAULT_NAMESPACE`        | —        | Vault Enterprise namespace.                                                     |
| `VAULT_TIMEOUT`          | `10`     | Seconds. An empty value falls back to the default rather than raising.          |

Keys **inside** Vault use the settings' own names, with or without the `AIRA_` prefix — those are
the values being fetched, and they are unrelated to the connection variables above.
**Rotation is a restart** — recorded as a decision rather than an omission. "Vault is down" and
"nobody wrote that key" are different exceptions and are reported differently.

---

## 8. What refuses to boot

Safe defaults are enforced, not documented ([`ADR-0007`](adr/ADR-0007-security-hardening-baseline.md)).
Outside `AIRA_ENVIRONMENT=local`, the process **stops** rather than starting insecurely when:

- `AIRA_SECRET_KEY` is still the development value
- `AIRA_DEBUG` is on
- `AIRA_ALLOWED_HOSTS` is `*`
- CORS allows `*` **and** credentials
- a configured model sits in a region outside `AIRA_ALLOWED_REGIONS`
- two upstream adapters claim the same model name
- Vault is configured and cannot be reached

Each of these is a state where starting successfully would be worse than not starting: the operator
would have a service that looks healthy and is not doing what they believe it is doing.
