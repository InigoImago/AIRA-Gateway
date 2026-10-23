# API reference

The machine interfaces of AIRA in one place: the gateway's HTTP API, Management's REST API under
`/api/v1/`, and the configuration events Management sends the gateway over Kafka. Every operation is
listed with who may call it and what it answers.

The two model surfaces have guides of their own — [`MIGRATION-GEMINI.md`](MIGRATION-GEMINI.md) and
[`MIGRATION-KIRA.md`](MIGRATION-KIRA.md) say what each accepts, refuses and answers — so they are
only listed here. [`REQUEST-LIFECYCLE.md`](REQUEST-LIFECYCLE.md) follows one request through the
controls, and [`ROLES.md`](ROLES.md) says who holds which permission.

**This page is compared with the code in both directions.** A test fails when a route of either
plane is missing here, or when this page names one that does not exist, and holds the two guides to
the routes of their surface; another does the same for every event type, its topic, its key and its
payload fields (§4.5). What the tables say about each operation was written from the code named in
its section, and is not checked.

| Jump to                                           |                                                            |
| ------------------------------------------------- | ---------------------------------------------------------- |
| [1 Conventions](#1-conventions)                   | addresses, credentials, who may call, errors, schemas      |
| [2 The gateway](#2-the-gateway)                   | the data plane: model surfaces, figures, incident controls |
| [3 Management](#3-management)                     | the control plane's REST API                               |
| [4 Configuration events](#4-configuration-events) | what Management sends the gateway over Kafka               |

---

## 1. Conventions

### 1.1 Where each plane answers

|                                  | Gateway                                                              | Management                                                     |
| -------------------------------- | -------------------------------------------------------------------- | -------------------------------------------------------------- |
| Address in the Compose stack     | `http://localhost:8001` (`AIRA_PUBLISH_GATEWAY_PORT`)                | `http://localhost:8002` (`AIRA_PUBLISH_MANAGEMENT_PORT`)       |
| Through the console's own origin | `/gw/…`, the prefix removed: `/gw/v1beta/traces` is `/v1beta/traces` | `/api/…`, unchanged                                            |
| Credential                       | an API key, or a Keycloak access token                               | a Keycloak access token, nothing else                          |
| Error body                       | depends on the path (§1.4)                                           | `{"error": {"code", "message", "details"}}`                    |

Management's two probes, `/healthz` and `/readyz`, are outside `/api/` and so are not reachable
through the console's origin.

### 1.2 Credentials, and naming a use case

**On the gateway** a credential is read from `Authorization: Bearer …`, then `x-goog-api-key`, then
`?key=` (`auth/credentials.py`) — the last on every path but the KIRA surface's, whose clients send
their credential in a header and which answers a key in its URL with `401 NOT_AUTHENTICATED` naming
the headers it reads. It is a Keycloak access token, or an API key: one Management issued
for a single use case, or the break-glass key an operator mints with the gateway's CLI, which is
bound to none. A token's permissions are those of the roles its groups confer; **an API key holds
no permission at all**, so every route below that asks for one needs a token. The break-glass key
may act on any use case, sees no figures, and is the one key `/readyz` shows its diagnosis to. Too
many refused credentials from one address answer `429` with `Retry-After`
(`AIRA_MAX_AUTH_FAILURES_PER_MINUTE`).

**A request names its use case** with the `X-AIRA-Use-Case` header, or by putting `/uc/<slug>` in
front of any gateway path — `/uc/assistant/v1beta/models/…`; the header wins where both appear. A
key issued for a use case needs neither. The name only chooses among the use cases the caller
already holds a grant on: naming another is `403` (`use_case_refusal`). On the Gemini surface a
`POST` that names no use case is refused `400` unless its credential is a key, however many use
cases the caller holds (`must_name_a_use_case`); a `GET` spends nothing and needs none. The KIRA
surface, whose contract has no field for a use case, attributes a request that names none to the
caller's use case when they hold exactly one, and refuses it `403`, listing the candidates, when
they hold several (`api/kira/attribution.py`).

**Management** accepts a Keycloak bearer token and nothing else (`apps/api/authentication.py`).
Refused authentications are bounded per address (`AIRA_THROTTLE_AUTH_FAILURES`) and every signed-in
caller per minute (`AIRA_THROTTLE_USER`); both answer `429`.

### 1.3 Who may call

The **Who may call** columns use these words.

| Word           | Means                                                                                                                                                                                                                                      |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| public         | no credential: anybody who can reach the port                                                                                                                                                                                              |
| any credential | any valid credential (gateway)                                                                                                                                                                                                             |
| signed in      | any valid Keycloak token (Management)                                                                                                                                                                                                      |
| `report.read_all`, … | a token whose roles hold that permission — the catalogue is `libs/src/aira_common/permissions.py`, and [`ROLES.md`](ROLES.md) §2 says which role holds which                                                                         |
| a grant        | an `admin` or `user` grant on the use case in question: a membership by name, or a Keycloak group the use case was granted to; on the gateway also the `/use-cases/<slug>` group convention, and a key issued for that use case             |
| admin          | an `admin` grant on the use case; in Management also `usecase.manage_all`                                                                                                                                                                  |
| sees it        | Management: `usecase.read_all`, `usecase.manage_all`, or a grant on the use case. Anybody else is answered `404`, as if it did not exist                                                                                                  |

**`usecase.manage_all` is a permission of Management.** There it counts as an administrator's grant
on every use case. The gateway grants nothing on it: calling a use case, seeing its requests and
reading its stored content still need a grant or the matching gateway permission, because the
right to see or administer a use case's configuration never implies the right to act inside it. Its
one reading on the gateway is in the pipeline dry run, where a caller who already holds a grant may
try an unsaved pipeline as an administrator could.

### 1.4 Errors

| Where                                       | Body                                                                                       |
| ------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Gateway, paths under `/v1beta/`             | Google's envelope: `{"error": {"code": 403, "message": "…", "status": "PERMISSION_DENIED", "details": [ErrorInfo]}}` |
| Gateway, paths under `/kira/api/external/`  | the predecessor's: `{"code": "…", "message": "…"}`                                         |
| Gateway, every other path                   | `{"error": {"code": "not_found", "message": "…"}}`                                         |
| Management                                  | `{"error": {"code": "permission_denied", "message": "…", "details": …}}` — a `400` carries the field errors in `details` |

The gateway answers a parameter it cannot accept with `400` naming it, never `422`, and a body
larger than `AIRA_MAX_REQUEST_BYTES` with `413` before reading it, naming the limit. Both planes
also refuse a body unread when it nests deeper than 64 levels or holds more than 250 000 JSON values
— `400` naming the bound, on the KIRA surface `422 VALIDATION_ERROR` — and both model surfaces
refuse a request of more than 10 000 turns or 20 000 parts before parsing them, and one offering
more than 128 functions. A traced response carries its trace id in `x-trace-id`, failures
included; the health probes are not traced.

**Which control refused.** A Gemini refusal's `details` holds one Google `ErrorInfo` —
`{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "BLOCKED_BY_PIPELINE", "domain":
"aira.gateway", "metadata": {"request_id": "…", "step": "injection_filter"}}` — so a Gemini SDK,
which keeps `details` and prints it with its exception, carries it to the developer. `reason` is the
request's outcome in upper case (`BLOCKED_BY_PIPELINE`, `BUDGET_EXCEEDED`, `RATE_LIMITED`,
`NO_CAPABLE_MODEL`, `UPSTREAM_ERROR`, `MODEL_NOT_FOUND`, `INVALID_REQUEST`, `REQUEST_TOO_LARGE`,
`SUSPENDED`; `UNAUTHENTICATED` and `PERMISSION_DENIED` before a request is attributed). `metadata`
adds `step` for a pipeline block and `resets_at` for a budget. The status and the message are what
they were without it. The KIRA envelope is the predecessor's and gains nothing: there the same word
is the `X-AIRA-Reason` header.

**Headers that say what happened**, on every gateway response, both surfaces, exposed to browsers:

| Header | When | Value |
| --- | --- | --- |
| `X-AIRA-Request-Id` | always | the request's id — its audit row's, which the console's request search and `GET /v1beta/traces/{id}` find |
| `X-AIRA-Reason` | a refusal | the outcome in lower case, as in `ErrorInfo` |
| `X-AIRA-Model` | an answer | the model that answered — after routing and fallback |
| `X-AIRA-Pipeline` | a pipeline ran | one `step=action` per step, comma-separated: `injection_filter=passed`, `pii_filter=redacted`, `model_route=a>b` |

Headers, because a client validates the body it parses and ignores a header it does not know: the
`google-genai` SDK and the AI SDK's Google provider (OpenCode's) read every answer and every
refusal as before.

Other refusals' messages say what they need to be acted on: an exhausted fallback chain names every
model it tried and why each did not answer, keeping the last provider's status; a budget refusal
names when its period resets — never as `Retry-After`, which would put a client to sleep for the
rest of the month — and, when this request alone is estimated above the whole limit, says that
rather than "exhausted".

Management reads JSON bodies only (anything else is `415`) and its paths are exact: `APPEND_SLASH`
is off, so `/api/v1/use-cases` without its slash is `404`, and so is `/api/v1/me/` with one. Its
lists are plain arrays, except two that are paged — `GET /api/v1/use-cases/` and
`GET /api/v1/roles/changes/` — which answer `{count, page, page_size, pages, results}` and take
`?page=` and `?page_size=` (at most 200).

### 1.5 OpenAPI

**The gateway** generates an OpenAPI 3.1 document of every route, its path parameters and its query
parameters. `GET /openapi.json` serves it, `GET /docs` renders it with Swagger UI (whose sign-in
helper is `GET /docs/oauth2-redirect`) and `GET /redoc` with ReDoc — **only when
`AIRA_ENVIRONMENT=local` or `AIRA_DEMO_MODE` is on** (`create_app` in
`gateway/src/aira_gateway/app.py`). Anywhere else the four paths answer `404`: a map of every
incident and content-read route is for whoever develops against the gateway, not for anybody who
can reach its port. The document describes no request body and no response, because the routes read
and write their JSON by hand; this page and the two guides describe them. No copy of it is committed
or published with a release, and no OpenAPI 3.0 version is generated for an API portal that reads
only 3.0.

**Management has no schema endpoint.** DRF's schema generation is not routed, and JSON is its only
renderer (`REST_FRAMEWORK` in `management/backend/src/aira_management/config/settings.py`). This
page is its reference.

---

## 2. The gateway

FastAPI, `gateway/src/aira_gateway/`. The routers are mounted in `_include_routers` in `app.py`;
each section names the module its routes live in.

### 2.1 The model surfaces

`api/gemini/routes.py`, `api/ranking/routes.py`, `api/kira/routes.py`, `api/kira/info_routes.py`.
Every request a surface
attributes to a use case is recorded in the audit trail, served or refused. One refused before it is
attributed — no valid credential, a use case the caller does not hold, none named — writes no row:
the request's span is its only record, and an `AIRA_OTEL_SAMPLE_RATIO` below 1 thins those as it
thins every span. The guides say the rest.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /v1beta/models` | any credential | The models this gateway has an upstream for, as Google's `Model` resource with the verbs each answers and what the catalogue declares about it (`airaCapabilities`, `airaDeclared`, …). One the catalogue holds and has not approved is left out. |
| `GET /v1beta/models/{model}` | any credential | One model of that list; `404` for any other. |
| `POST /v1beta/models/{model}:generateContent` | a grant on the use case it is attributed to | The whole answer in one response. Google's request body: a function's parameters as `parameters` (the OpenAPI subset) or `parametersJsonSchema` (JSON Schema, carried as written), not both; an answer's schema as `responseSchema` or `responseJsonSchema`, with `responseMimeType` `application/json`; thinking as `thinkingBudget` or `thinkingLevel`; `tools` as a list or one tool object. |
| `POST /v1beta/models/{model}:streamGenerateContent` | the same | The answer in chunks: a JSON array, or server-sent events with `?alt=sse`. |
| `POST /v1beta/models/{model}:embedContent` | the same | `{embedding: {values}}`. |
| `POST /v1beta/models/{model}:batchEmbedContents` | the same | `{embeddings: [{values}]}`, one per entry of the body's `requests`. |
| `POST /v1/projects/{project}/locations/{location}/rankingConfigs/{config}:rank` | a grant on the use case it is attributed to | Google's ranking API: `{model, query, records: [{id, title?, content?}], topN?, ignoreRecordDetailsInResponse?}` answered with `{records: [{id, score, title?, content?}]}` in the reranker's order. `model` is required and names a catalogue entry declared `rerank`; the path's project, location and configuration select nothing — the gateway ranks in its own project, at the locations the catalogue gives the model. At most `AIRA_RERANK_MAX_RECORDS` records. Booked as one ranking unit per hundred records: that many requests against a rate limit, and that many units as the input figure the entry's input price applies to. |
| `POST /kira/api/external/chat` | a grant on the use case it is attributed to | The whole answer in one response, in the predecessor's shape. `tools` declares functions (`{type?: "function", name, description, parameters}`, parameters a JSON Schema object under the predecessor's rule) where the use case enables tool calling; a call comes back as a part `{type: "function_call", id, name, arguments, opaque_metadata}` (`opaque_metadata` `null` or `{thoughtSignature}`), and its result goes back as `{type?: "function_result", call_id, name, result, error?}` in the next request. `usage_data.token_output` leaves out thinking. An answer with no text, no call and no output token is `502 EXTERNAL_KI_API_ERROR`; one that cost output and says nothing is `parts: []`. |
| `POST /kira/api/external/streaming-chat` | the same | Server-sent events: an `update` per text chunk — none for a `responseSchema` answer, which comes whole in `completed` — a `function_call` event per call, then `completed` with the whole answer and its usage. A `: ping` comment every 15 seconds while nothing else was sent; `X-Accel-Buffering: no`. |
| `POST /kira/api/external/embed` | the same | One vector. For a model whose upstream name begins with `gemini-embedding-2`, each text goes as its own part inside its task's prompt (`task: search result \| query: …`), and a list is one embedding of all parts; for any other, a list of texts is joined into one text. |
| `POST /kira/api/external/rerank` | the same | `{model_id, query, records: [{id, title, content}]}`, every field filled, answered with `{records: [{id, score, title, content}]}` in the reranker's order. `422 NO_RERANKER_CAPABILITIES` for a model not declared `rerank`. Booked as on Google's ranking path. |
| `GET /kira/api/external/models` | any credential | The served models that have a KIRA id, by that id, with their capabilities (`Chat`, `Embedding`, `Reranker`), limits, thinking (modes and level words, a default, `null` for an undeclared bound) and embedding options. |
| `GET /kira/api/external/health` | public; the upstreams are listed only to whom `/readyz` shows its diagnosis | The gateway, and each upstream from the background probe's last verdict; `503` when any of them is unhealthy. |
| `GET /kira/api/external/version-info` | public | Build metadata, `null` where none was set. |
| `GET /kira/api/external/ki-usage` | any credential | **The caller's own** requests and tokens between `startDatum` and `endDatum`, one row per model, `user_id` the caller's name and `model_id` the model's KIRA id (`0` where none) — counted **in the use cases the credential may act in**: an API key's own use case, a signed-in person's memberships. Nobody else's figures, whatever the caller's role, and none from a use case the credential has no part in. CSV with a byte-order mark for `Accept: text/csv`. A date that does not parse is `422`; a window of more than 366 days is `400 INVALID_TIME_RANGE`. A date without an offset is read as UTC. |

An upstream failure on the KIRA surface is `502 EXTERNAL_KI_API_ERROR`, whatever the provider said,
except `429`; an attachment type nobody accepts, a schema it cannot read and a `temperature` outside
0–2 are `422`. Its `422` `details` carry `loc` (beginning with `"body"`), `msg` and `type`.

The four Gemini verbs are one route, `/v1beta/models/{model}:{verb}`, split at the **last** colon so
a model name may contain one; any other verb, `:countTokens` among them, is `400`.

### 2.2 Health and build

`routes/health.py`.

| Operation           | Who may call | Answers |
| ------------------- | ------------ | ------- |
| `GET /healthz`      | public | `200 {"status": "ok"}`. Liveness, with no I/O. |
| `GET /readyz`       | public; the diagnosis to `operations.diagnose`, to the break-glass key, and to anybody locally or in demo mode | `200` or `503` with `{status: ready \| not_ready, degraded}`. **Postgres alone decides readiness.** Kafka (`checks.kafka`, `required: false`, reachable while any broker answers), the shared counter store, a feature running on its fallback and the upstreams only set `degraded`. The diagnosis adds `fallbacks`, `checks`, `upstreams` (the background probe's last verdicts, never a call made for the request) and `secrets` (names, never values). |
| `GET /version-info` | public | `{service, environment, buildNumber, buildTime, git: {commit, commitShort, branch}}`, with `null` where no build metadata was set. |

### 2.3 Trying a pipeline

`api/pipeline.py`.

| Operation                    | Who may call | Answers |
| ---------------------------- | ------------ | ------- |
| `POST /v1beta/pipeline:dryRun` | a grant on the use case named in the body; an unsaved pipeline, or `past_blocks`, only its admins, `usecase.manage_all` and the break-glass key | Body `{use_case, system, user, model, pipeline, past_blocks}`, each sample at most 8,000 characters. `200 {blocked, block_reason, effective_model, fallback_models, trace: [{type, action, detail, after_block}]}`; to a member, `detail` leaves out what a step's model wrote (`output`, `after`). `400` for a model the use case was not released or the installation has not approved; `403` for another use case, one this gateway does not hold or holds retired, or a member trying an unsaved pipeline; before any step runs, the refusals a request meets — a suspension, a rate limit (a run weighs one per step, each of which may call a model), an exhausted budget. Each step's model meets the conditions it meets in production — release, approval, residency, and a price where spend is capped — so a dry run reaches no model a request could not. **It runs the real engine**: a step that asks a model spends tokens, billed and audited. |

### 2.4 Budgets

`api/usage.py`.

| Operation                     | Who may call | Answers |
| ----------------------------- | ------------ | ------- |
| `GET /v1beta/usage/{use_case}` | `report.read_all`, or a grant on the use case | `200 {use_case, usage, enforced}`: one entry per budget with its limits, `used_*`, `remaining_*` and `period_ends`; a budget for each member shows the caller's own counter. `enforced` says whether budgets refuse at all (`AIRA_ENFORCE_BUDGETS`). |
| `GET /v1beta/me/budget`       | any credential; the use case comes from the key, the header or `/uc/<slug>` | `200 {use_case, person, enforced, currency, allowances, today, this_week, this_period}`: `allowances` are the counters enforcement reads, one per budget that binds this caller; `today`, `this_week` (since Monday) and `this_period` (the calendar month) are what the audit trail says the caller spent, in UTC, with or without a budget — each `{from, to}` with the reporting totals. |

### 2.5 Figures, requests and findings

`api/reporting/`. Each answer is bounded to what the caller may see: every use case with the
permission named, otherwise the use cases the caller holds a grant on. A filter outside that answers
empty with `in_scope: false` rather than refusing, so nothing confirms that a use case exists. Lists
are newest first, `limit` at most 200 (50 by default), continued with the `next_cursor` they return.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /v1beta/reporting` | any credential, bounded by `report.read_all` | Spend and usage over `from`–`to` (ISO 8601, read as UTC; this calendar month by default; at most 366 days), narrowed by `use_case` and `person`. `series` (`auto`, `day`, `hour`) adds the window bucket by bucket, in bands by `split` (`model`, `use_case`, `outcome`). JSON: `{from, to, totals, by_use_case, by_model, by_member, by_outcome, by_person, series, scope, use_case, person, in_scope, people_limited, enforced}`, `series` only when asked. `Accept: text/csv` answers one `breakdown` (`use_case`, `model`, `member`) as a CSV file; any other `Accept` is `406`. In a use case whose `payload_visibility` is `own`, a member who is not its admin sees only their own row in the two splits by person; on `oversight` nobody inside the use case sees those splits, its admins included. Totals and the other breakdowns stay whole. |
| `GET /v1beta/register` | any credential, bounded by `report.read_all` | The register of processing activities over `from`–`to`: `{from, to, scope, use_cases, processed_in, catalogue, last_erasure}` — per use case its purpose, its released models and where they run, retention, content visibility, controls and members, and where its traffic actually went. CSV with `Accept: text/csv`. |
| `GET /v1beta/traces` | any credential, bounded by `trace.read_all`; `source_ip` and filtering by it need `incident.investigate` | Request metadata, never content: `{traces, next_cursor, scope, in_scope}`, each row an allow-list of the audit row's columns (`id`, `created_at`, `operation`, `api`, `model`, `status`, `outcome`, the token counts, `latency_ms`, `cost_nanos`, `provider`, `region`, `trace_id`, `subject`, `credential`, `use_case`, `tool_calls`, `flagged`, …). Filters `use_case`, `outcome`, `refusals_only`, `credential`, `subject`, `source_ip`, `mine`, `tools_only`, `flagged_only`, `request_id`. In a use case whose `payload_visibility` is `own`, a member who is not its admin sees only their own requests; `incident.investigate` is never narrowed. |
| `GET /v1beta/traces/{id}` | as the list: `trace.read_all`, or a grant on the request's use case | `200 {trace, recorded: true}` — one row as the list shows it to this caller plus what explains it: `pipeline_decisions` (each step's verdict and each model the chain skipped or tried, with why), `degraded` and `error` (the message the caller was given). **The opening is recorded** (`kind: metadata` in the access log) before it is returned. `404` for a request that does not exist or lies outside the caller's scope, and then nothing is recorded. |
| `GET /v1beta/traces/{id}/payload` | `payload.read_any`; otherwise a grant on the request's use case, as far as its `payload_visibility` allows | `200 {id, request, response, available: true, ground}` — the read is recorded before the content is returned, and does not happen if the record cannot be written. `200 {id, available: false, reason, message}` when there is nothing to show (`not_stored`, `expired`, `never_had_one`, `never_kept`); `403` with the reason when the caller may not read it; `404` for a request that does not exist or lies outside the caller's scope — one answer for both. |
| `GET /v1beta/anomalies` | any credential, bounded by `anomaly.read_all` | What the detector found: `{events, next_cursor, scope, in_scope}`, each `{id, created_at, rule_id, rule, kind, use_case, target, target_value, observed, threshold, sample, window_minutes, action_taken, detail}`; addresses in `detail` only with `incident.investigate`. A finding a global rule made about a person or a credential belongs to no use case and is seen with `anomaly.read_all` alone; in a use case on `own`, a member who is not its admin sees the findings about the use case and about themselves. |

### 2.6 Incidents and operations

`api/incidents/`. Bounded by permission rather than by use case.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /v1beta/suspensions` | `incident.suspend` or `anomaly.read_all`: every stop. A caller with a grant, with `?use_case=`: the stops on that use case and on themselves in it. Anybody else `403` | `{suspensions, scope: all \| own, in_scope}`: every suspension in force, then up to 200 lifted or expired. Each `{id, created_at, use_case, target, target_value, action, throttle_rpm, expires_at, author, reason, lifted_at, lifted_by}`. |
| `POST /v1beta/suspensions` | `incident.suspend` | Body `{target, target_value, use_case, action, throttle_rpm, minutes, reason}`: `target` is `use_case`, `subject` or `credential`; `action` is `block` (the default) or `throttle` with `throttle_rpm`; no `minutes` means until lifted. `201` the suspension. Stored in the gateway's own database and **never sent through Management or Kafka**, so it works while either is down; in force at once on the instance that stored it and within 5 seconds on every other. |
| `DELETE /v1beta/suspensions/{id}` | `incident.suspend` | `200` the suspension, stamped `lifted_at` and `lifted_by` — kept, never deleted. `404` for an unknown id. |
| `GET /v1beta/content-reads` | `content_read.read` | Who read which stored content, on what ground: `{reads: [{id, created_at, request_log_id, use_case, subject, username, ground, roles, auth_method, credential, kind}], next_cursor, count}` — `kind` is `content` (prompt and response read) or `metadata` (a request's details opened); filters `use_case`, `reader` and `kind`. |
| `GET /v1beta/models/{model}:check` | `operations.diagnose` | `{model, declared, served, reachable, detail, reason, explanation, regions: [{region, reachable, detail, reason, explanation}]}`: whether the model is catalogued, whether an adapter serves it, and whether its provider answers a cheap question in each region (5 seconds each). Where it does not, `reason` is the cause the provider or the connection stated — one word, such as `SERVICE_DISABLED` or `CERTIFICATE_NOT_TRUSTED` — and `explanation` what to change; both `null` where none was stated. **Never a generation.** `provider`, `publisher` and `region` describe an editor's unsaved values. Each probe writes a `diagnostic` audit row attributed to no use case. |
| `POST /v1beta/models/{model}:checkThinking` | `operations.diagnose` | Body `{levels, modes}`, the first 12 of each asked. Whether the model accepts each thinking level word — one generation capped at one output token per word and region, billed and recorded as `diagnostic` — and whether its dialect can express each mode, answered without a call: `{model, results: [{region, level, accepted, detail}], modes: [{mode, accepted, detail}]}`. `400` when no upstream serves the model. |

### 2.7 What a provider offers

`api/providers.py`.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /v1beta/providers` | `catalog.write` | The upstreams this gateway is configured with: `{providers: [{name, label, publisher, region, canEnumerate, cataloguedIsEnough, servedModels, adapters}], allowedRegions, modelRegions}` — the last two are the residency policy (`AIRA_ALLOWED_REGIONS`, `AIRA_ALLOWED_MODEL_REGIONS`) the console checks a catalogue entry against. |
| `GET /v1beta/providers/{name}/offerings` | `catalog.write` | What one provider says this installation's credential reaches, unfiltered against the catalogue: `{provider, models: [{name, displayName, description, maxOutputTokens, canGenerate, canEmbed, canCachePrompts, thinking}]}`. `404` for an unknown provider, `501` for one that cannot be asked, `502` when it did not answer. |
| `GET /v1beta/providers/{name}/prices` | `catalog.write` | `?model=&region=`. What the model costs this installation, from the provider's billing account (`AIRA_VERTEX_BILLING_ACCOUNT`): `{model, region, billingAccount, currency, prices: {input, output, cached_input, cache_write}, unpriced}`, each price `{perMillion, currency, skuId, skuName, region, contract}` — the SKU it was read from, and whether a contract price applied; `unpriced` says why a field has none. `501` without a billing account or with an API key; `502` naming the cause when the account refuses. Read-only. |

### 2.8 The schema routes

FastAPI's own, present only locally and in demo mode (§1.5).

| Operation                    | Who may call | Answers                                  |
| ---------------------------- | ------------ | ---------------------------------------- |
| `GET /openapi.json`          | public       | the OpenAPI 3.1 document                 |
| `GET /docs`                  | public       | Swagger UI over it                       |
| `GET /docs/oauth2-redirect`  | public       | Swagger UI's sign-in helper page         |
| `GET /redoc`                 | public       | ReDoc over it                            |

---

## 3. Management

Django REST Framework, `management/backend/src/aira_management/`. Every route is under `/api/v1/`
(`config/urls.py`) except the two probes, and every route except the probes needs a signed-in
caller. **Emits** names the events a change sends the gateway (§4); a change and its events are
written in one transaction, so neither happens without the other.

### 3.1 Health

`apps/health/views.py`.

| Operation      | Who may call | Answers |
| -------------- | ------------ | ------- |
| `GET /healthz` | public | `200 {"status": "ok"}`. |
| `GET /readyz`  | public | `200` or `503` with `{status, degraded, checks: {postgres, kafka}}`. Postgres decides readiness; Kafka (`required: false`) only sets `degraded`, because a change waits in the outbox while it is down. The same body for everybody: it names no host. |

### 3.2 The caller

`apps/api/views.py`.

| Operation         | Who may call | Answers |
| ----------------- | ------------ | ------- |
| `GET /api/v1/me`  | signed in | `{subject, username, email, roles, role_labels, permissions, use_cases, api_key_default_days, api_key_max_days, currency, may_test, strict_data}` — what the server decides, so the console asks rather than guesses: `permissions` from the caller's roles, `use_cases` the slugs the token's `/use-cases/<slug>` groups name, `may_test` whether the pipeline tests are offered, `strict_data` whether the installation runs strict data mode (`AIRA_STRICT_DATA_MODE`). |
| `GET /api/v1/`    | signed in | DRF's router index, `{"use-cases": "<url>"}`. The console does not call it. |

### 3.3 Use cases

`apps/usecases/views/` — `viewset.py`, `retirement.py`. A use case is answered with `slug`, `name`,
`description`, `processing_notes`, `store_payloads`, `payload_visibility`, `tools_enabled`,
`include_reasoning`, `prompt_caching_enabled`, `prompt_cache_ttl`, `retention_days`,
`allowed_models`, `created_at`, `updated_at` and `permissions` — what this caller may do here:
`can_admin`, `can_manage`, `is_member`, `may_call`. While strict data mode is on, `payload_visibility`
is answered as `oversight` whatever is stored, and saving any other value is refused `400`; the
stored value is kept and is what the event carries, so turning the mode off restores it.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /api/v1/use-cases/` | signed in | Paged: the live use cases the caller sees, `?q=` filtering on name and slug. `?may_call=true` lists instead those the gateway would accept this caller's token for, by the gateway's own rule (`aira_common.access.resolve`) — an oversight role sees every use case and may call none. |
| `POST /api/v1/use-cases/` | `usecase.create` | `201` the use case; its creator becomes its admin. Emits `usecase.upserted`, `membership.upserted`. |
| `GET /api/v1/use-cases/{slug}/` | sees it | `200` the use case. |
| `PUT /api/v1/use-cases/{slug}/` | admin | `200` the use case. The slug is its identity and cannot change; `allowed_models` takes approved models only. Emits `usecase.upserted`. |
| `PATCH /api/v1/use-cases/{slug}/` | admin | As `PUT`, with only the fields sent. |
| `DELETE /api/v1/use-cases/{slug}/` | admin | `204`. **Retires** it: every access it granted ends and its record stays. Emits `usecase.deleted`. |
| `GET /api/v1/use-cases/retired/` | `usecase.read_retired` | `[{slug, name, description, processing_notes, store_payloads, retention_days, created_at, deleted_at, deleted_by, purgeable_on}]`. |
| `DELETE /api/v1/use-cases/{slug}/purge/` | `usecase.purge` | `204` for a use case retired at least 30 days ago, `400` before then, `404` for one that is live or unknown — alike. Emits `usecase.purged`. |

### 3.4 Who reaches a use case

`apps/usecases/views/` — `members.py`, `api_keys.py`, `grants.py`. Removing a member or a group
grant revokes every key whose owner no longer holds any grant on the use case.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /api/v1/use-cases/{slug}/members/` | sees it | `[{username, role, created_at}]`. |
| `POST /api/v1/use-cases/{slug}/members/` | admin | Body `{username, role}`, `role` `user` (the default) or `admin`; somebody who has not signed in yet is invited when the directory knows them. `201` the membership. Emits `membership.upserted`. |
| `DELETE /api/v1/use-cases/{slug}/members/{username}/` | admin | `200 {revoked_keys}`. Emits `membership.removed`, and `api_key.revoked` per key revoked. |
| `GET /api/v1/use-cases/{slug}/groups/` | sees it | `[{group_path, role, granted_by, reaches, created_at}]`. |
| `POST /api/v1/use-cases/{slug}/groups/` | admin | Body `{group_path, role}`. `201` the grant. The path is checked for its shape, not for existence: a group Keycloak does not have yet can be granted, so a mistyped path makes a grant that reaches nobody. `reaches` counts the people Management has seen sign in with that group — `0` says nobody known holds it yet. Emits `use_case_group.granted`. |
| `DELETE /api/v1/use-cases/{slug}/groups/revoke/` | admin | The path in `?group_path=`, because a group path has slashes. `200 {revoked_keys}`. Emits `use_case_group.revoked`, and `api_key.revoked` per key revoked. |
| `GET /api/v1/use-cases/{slug}/api-keys/` | sees it | `[{prefix, label, owner, issued_by, owner_withheld, is_active, created_at, revoked_at, expires_at}]` — never a key. Under strict data mode `owner` and `issued_by` are `null` (`owner_withheld: true`) except to them and to `incident.investigate`. |
| `POST /api/v1/use-cases/{slug}/api-keys/` | a grant, or `usecase.manage_all` — whose holder is then made a member, because a key must rest on a grant that can end; naming another `owner` needs admin | Body `{label, owner, expires_in_days}`, all optional: the owner defaults to the caller, the lifetime to `AIRA_API_KEY_DEFAULT_DAYS` and at most `AIRA_API_KEY_MAX_DAYS`. `201 {api_key, prefix, label, owner, issued_by, use_case, expires_at}` — **the key is shown this once**; only its hash is kept. Emits `api_key.created`, and `membership.upserted` when the owner held no grant. |
| `DELETE /api/v1/use-cases/{slug}/api-keys/{prefix}/` | admin | `204`; `400` when no active key has that prefix. Emits `api_key.revoked`. |

### 3.5 Inside a use case

`apps/usecases/views/` — `pipeline.py`, `budgets.py`, `rate_limits.py`, `anomaly_rules.py`. A child
record is deleted by its id; an id the use case does not have is `400`.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /api/v1/use-cases/{slug}/pipeline/` | sees it | `{steps, fallback_models, updated_at}`; a use case with none answers `{steps: [], fallback_models: []}`. |
| `PUT /api/v1/use-cases/{slug}/pipeline/` | admin | Body `{steps, fallback_models}`; every model it names must be released to the use case. `200`. A pipeline is cleared by saving it with no steps, never deleted. Emits `pipeline.upserted`. |
| `GET /api/v1/use-cases/{slug}/budgets/` | sees it | `[{id, scope, subject, period, limit_cost, limit_tokens, limit_requests, enabled, created_at, updated_at}]`. |
| `POST /api/v1/use-cases/{slug}/budgets/` | admin | Upserts on `scope` (`use_case`, a shared pot, or `each_member`, one per person) and `period` (`day`, `month`); at least one limit. `201`. Emits `budget.upserted`. |
| `DELETE /api/v1/use-cases/{slug}/budgets/{id}/` | admin | `204`. Emits `budget.deleted`. |
| `GET /api/v1/use-cases/{slug}/rate-limits/` | sees it | `[{id, scope, subject, limit_rpm, burst, enabled, created_at, updated_at}]`. |
| `POST /api/v1/use-cases/{slug}/rate-limits/` | admin | Upserts on `scope` (`use_case` or `each_member`). `201`. Emits `ratelimit.upserted`. |
| `DELETE /api/v1/use-cases/{slug}/rate-limits/{id}/` | admin | `204`. Emits `ratelimit.deleted`. |
| `GET /api/v1/use-cases/{slug}/anomaly-rules/` | sees it | This use case's own rules; the global ones are at `/api/v1/anomaly-rules/`. Each `{id, use_case, is_global, name, kind, window_minutes, threshold, parameter, min_sample, action, target, action_minutes, throttle_rpm, enabled, created_at, updated_at}`. |
| `POST /api/v1/use-cases/{slug}/anomaly-rules/` | admin | One rule, upserted on its `name`. `201`. Emits `anomaly_rule.upserted`. |
| `DELETE /api/v1/use-cases/{slug}/anomaly-rules/{id}/` | admin | `204`. Emits `anomaly_rule.deleted`. |

### 3.6 The model catalogue

`apps/catalog/views.py`. A model is answered with `name`, `display_name`, `approved`, `provider`,
the four prices (`input_price_per_million`, `output_price_per_million`,
`cached_input_price_per_million`, `cache_write_price_per_million`, decimal strings), `is_priced`,
its declaration (`capabilities`, `publisher`, `platform`, `addressing`, `underlying_model`,
`context_window`, `max_output_tokens`, `default_max_output_tokens`, `thinking`, `embedding`,
`attachments`, `fixed_sampling`, `default_sampling`, `hosting`, `deprecated`, `numeric_id`),
`is_declared` and `updated_at`. The list is not paged. `fixed_sampling` is `true` for a model that
samples at its own settings: the gateway never sends it a request that sets `temperature` other than
1, `top_p` other than 1 or any `top_k`, and tries the next model in the chain or refuses naming the
control. It defaults to `false`, no restriction, and accepts only a JSON `true` or `false`.
`default_sampling` is `{top_p, top_k}`, either or both, or `null`: sent where a caller sets that
control not at all, and only to a dialect that can say it; `top_p` above 0 and at most 1, `top_k` a
whole number of at least 1, and none for a model with fixed sampling. `capabilities` may include
`rerank`. An `addressing.model` names the model on the wire where the entry is an alias of another:
release, approval, residency, price and the KIRA id stay the entry's own.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /api/v1/models/` | signed in | Every catalogued model. |
| `POST /api/v1/models/` | `catalog.write` | Upserts on `name`: `201` for a new model, `200` for one corrected. `409` when a concurrent write took the name or the KIRA id. Emits `model.upserted`. |
| `GET /api/v1/models/{name}/` | signed in | One model; `{name}` is the rest of the path, slashes included. |
| `PUT /api/v1/models/{name}/` | `catalog.write` | `200`. A model's name cannot change. Emits `model.upserted`. |
| `PATCH /api/v1/models/{name}/` | `catalog.write` | As `PUT`, with only the fields sent. |
| `DELETE /api/v1/models/{name}/` | `catalog.write` | `204`. Emits `model.deleted`, and `usecase.upserted` for every live use case it was released to. |
| `GET /api/v1/model-reference/` | `catalog.write` | `{source, as_of, currency, conversion, live_error, entries}`: drafts of catalog entries from a public model list, for the console's *Add a model*. Each entry is `{platform, name, display_name, publisher, family, status, release_date, knowledge, description, declaration: {capabilities, context_window, max_output_tokens, thinking, attachments, fixed_sampling}, prices: {input, output, cached_input, cache_write}, notes}`, prices per million tokens in `currency` (US dollars). From the copy the release ships, or from `AIRA_MODEL_REFERENCE_URL` where set; `live_error` says why that list was not used; `conversion` is `{from, to, rate}` where `AIRA_MODEL_REFERENCE_EXCHANGE_RATE` is set and the installation does not count in the list's currency, otherwise `null`. Writes nothing. |

### 3.7 The installation's own budget, and global rules

`apps/budgets/views.py`, `apps/anomalies/views.py`.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /api/v1/installation-budgets/` | signed in | The installation's budgets to `budget.installation.write` and `report.read_all`; `[]` to everybody else. |
| `POST /api/v1/installation-budgets/` | `budget.installation.write` | Upserts on `period`. `201`. Emits `budget.upserted`, with `use_case` empty. |
| `DELETE /api/v1/installation-budgets/{id}/` | `budget.installation.write` | `204`; `404`, with no body, for an unknown id. Emits `budget.deleted`. |
| `GET /api/v1/anomaly-rules/` | signed in | Every global rule, and the rules of every live use case the caller sees. |
| `POST /api/v1/anomaly-rules/` | `anomaly.rule.global.write` | Creates a **global** rule. `201`. Emits `anomaly_rule.upserted`. |
| `GET /api/v1/anomaly-rules/{id}/` | signed in | One rule, within the same reach. |
| `PUT /api/v1/anomaly-rules/{id}/` | a global rule: `anomaly.rule.global.write`; a use case's: admin of it | `200`. Emits `anomaly_rule.upserted`. |
| `PATCH /api/v1/anomaly-rules/{id}/` | the same | As `PUT`, with only the fields sent. |
| `DELETE /api/v1/anomaly-rules/{id}/` | the same | `204`. Emits `anomaly_rule.deleted`. |

### 3.8 The question catalogue

`apps/smoketests/views/`. **May run tests** is `smoketest.run_any`, or an admin grant on a live use
case the caller's token may call — a run spends a use case's budget a hundred prompts at a time,
which is an administrator's decision. Runs and answers are visible only within the use cases the
caller may run. The console sends each question through the gateway with the person's own
credential, so a run is ordinary traffic: priced, budgeted and audited. Runs and results are never
deleted.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /api/v1/test-attribution/` | may run tests | `[{use_case, name, models, may_run, why_not}]` per use case the caller may run. |
| `GET /api/v1/test-cases/` | may run tests | The live questions: `[{id, topic, prompt, expectation, position, retired}]`. |
| `POST /api/v1/test-cases/` | `smoketest.author` | `201`; appended when no `position` is sent. |
| `GET /api/v1/test-cases/{id}/` | may run tests | One question. |
| `PUT /api/v1/test-cases/{id}/` | `smoketest.author` | `200`; `retired: true` takes a question out of the catalogue. |
| `PATCH /api/v1/test-cases/{id}/` | `smoketest.author` | As `PUT`, with only the fields sent. |
| `DELETE /api/v1/test-cases/{id}/` | `smoketest.author` | `204`; `400` once it has been answered — retire it instead. |
| `GET /api/v1/test-runs/` | may run tests | `[{id, model, use_case, started_at, finished_at, requested_by_name, counts}]`; `?use_case=`, `?model=` (the model a run entered at). |
| `POST /api/v1/test-runs/` | may run tests, in that use case | Body `{use_case, model}`, `model` one of those released to it (the first by default). `201` the run, with one unrated result per question. |
| `GET /api/v1/test-runs/{id}/` | may run tests | One run. |
| `GET /api/v1/test-runs/{id}/results/` | may run tests | Its results. |
| `POST /api/v1/test-runs/{id}/finish/` | the person who started it | `200` the run, `finished_at` set once. |
| `GET /api/v1/test-runs/{id}/export/` | may run tests | The run as a CSV file. |
| `GET /api/v1/test-results/` | may run tests | `[{id, run, case, topic, prompt, expectation, response, error, latency_ms, verdict, note, rated_by_name, rated_at}]`. |
| `GET /api/v1/test-results/{id}/` | may run tests | One result. |
| `PATCH /api/v1/test-results/{id}/` | may run tests; the answer only the run's starter, and not once rated | The answer (`response`, `error`, `latency_ms`) or the judgement (`verdict`, `note`). `200`; a judgement names whoever made it. |
| `GET /api/v1/test-stats/` | may run tests | The latest **finished** run per use case the caller may run, with its verdict counts. |

### 3.9 Directory, roles and the privacy notice

`apps/directory/views.py`, `apps/roles/views.py`, `apps/privacy/views.py`. Every change to a stored
role is written to the role change log with its author and the role before and after.

| Operation | Who may call | Answers |
| --------- | ------------ | ------- |
| `GET /api/v1/directory/` | `directory.search`, or an `admin` grant on any live use case | `?q=`, at least two characters. `{results: [{kind, id, label, detail}], source}` — `kind` `user` or `group`; `source` `keycloak`, or `local` (what Management already knows) when no directory client is configured or it cannot be reached, or `none` for a shorter query. Every search is logged; nothing is written to Keycloak. |
| `GET /api/v1/roles/` | `role.read` | Every role: `[{slug, label, group_paths, permissions, builtin, fixed}]`. |
| `POST /api/v1/roles/` | `role.manage` | Body `{label, group_path, permissions}`. The group must exist in Keycloak and confer no other role — `503` when Keycloak cannot be asked. `201`. Emits `role.upserted`. |
| `GET /api/v1/roles/catalogue/` | `role.read` | Every permission: `[{name, area, label, sensitive, reserved, grantable, strict_data_reserved}]` — `grantable` whether a stored role may hold it now, `strict_data_reserved` whether strict data mode is what withholds it. |
| `GET /api/v1/roles/changes/` | `role.read` | The change log, paged; `?role=`. Each `{id, role, action, actor, at, before, after}`. |
| `POST /api/v1/roles/check-group/` | `role.manage` | Body `{group_path}`: `200 {group_path, exists: true}`, or the refusal a save would give. |
| `GET /api/v1/roles/{slug}/` | `role.read` | One role. |
| `PATCH /api/v1/roles/{slug}/` | `role.manage` | `200`. The fixed roles (`global-admin`, `it-security`) are refused `403`; a built-in role changes only its permissions. While strict data mode is on, `payload.read_any` and `incident.investigate` are refused `400` for every role, here and on `POST`. Emits `role.upserted`. |
| `DELETE /api/v1/roles/{slug}/` | `role.manage` | `204`; a built-in role is refused `403`. Emits `role.removed`. |
| `GET /api/v1/privacy-notice` | signed in | `?language=`, else the browser's `Accept-Language`. The notice, its `version`, `edition` and `languages`, and for the caller `mode`, `due` and `acknowledged_at`. |
| `POST /api/v1/privacy-notice/acknowledgements` | signed in | Body `{version, language}`. `201` the first time, `200` again; `409` when the notice changed while it was open. |

`role.manage` is held by the Global Administrator alone and cannot be given to a role.

### 3.10 What the framework adds

Every route a DRF router registers also answers with `.json` in place of its trailing slash
(`/api/v1/use-cases.json`); JSON being the only renderer, that changes nothing. Django and DRF
answer `HEAD` and `OPTIONS` beside the methods listed. A method a route does not list is `405` —
runs and results, for instance, have no `DELETE`.

---

## 4. Configuration events

Configuration travels one way. Management writes it; the gateway applies it into its read-model and
enforces it from there, without calling Management on the request path. **Management consumes no
event and the gateway publishes none**: the console reads the gateway's figures, requests and
findings over its HTTP API (§2.5). Nor is the audit trail published on the bus: a system that wants
a record per request, a SIEM for instance, takes it from the OpenTelemetry collector's delivery
channel, which carries one span per gateway request and per model call inside one
([`INTEGRATIONS.md`](INTEGRATIONS.md) §6). Two controls do not travel as events: a suspension is
written to the gateway directly (§2.6), and the break-glass key is minted on the gateway with its
CLI.

### 4.1 How an event travels

1. **Written with the change.** A view calls `emit` inside the transaction that makes the change,
   and `record_to_outbox` (`apps/outbox/subscriber.py`) writes the event to the outbox table. A
   change that rolls back sends nothing. An event whose encoded value would exceed 1,000,000 bytes
   (`MAX_EVENT_BYTES` in `libs/src/aira_common/kafka.py`) refuses the change itself with `400`,
   while it can still be shortened. A few data migrations write events directly, re-sending every
   live use case or model once so the gateway learns a new field.
2. **Published in order.** `manage.py relay` (`apps/outbox/management/commands/relay.py`) publishes
   pending events oldest first, at most 500 a run, one run at a time; the Compose stack runs it
   every 10 seconds (`AIRA_RELAY_INTERVAL`). A send that fails stops the run there: that event and
   every later one, on every topic, stay pending for the next run, because order is meaning. An
   event the broker refuses for its size is set aside — *parked* — never sent, and named on every
   run; the next change to the same thing sends a new one.
3. **At least once.** A relay that stops after sending and before marking the event sent sends it
   again. Every handler on the gateway is an idempotent upsert or delete keyed by the entity, so a
   repeated event changes nothing.
4. **Applied by one consumer.** `python -m aira_gateway.consumer.worker` is one process beside the
   gateway's instances, never scaled with them: the read-model it writes is a set of tables in the
   gateway's database, which every instance reads, so one consumer keeps all of them current. It is
   in the consumer group `aira-gateway`, subscribed to all nine topics, from the earliest offset
   when the group is new. Each event is applied and committed on its own (`consumer/apply.py`).
   **It dispatches on the `event_type` header alone**; the topic decides only ordering and
   compaction.
5. **Kept for 30 days.** A delivered or parked outbox row is deleted 30 days later
   (`KEPT_AFTER_DAYS`). An event says what changed, not who changed it; the one exception is
   `api_key.created`'s `issued_by`, set when a key was issued on somebody else's behalf.

The topics are `aira.` plus a name; with `AIRA_KAFKA_STAGE=t` they are `aira.t.…` and the group
`aira-gateway.t`, so several installations can share a cluster with one prefixed ACL each. All nine
are compacted (`cleanup.policy=compact`): the last event per key survives, and a fresh consumer
group rebuilds the read-model by replaying them. Kafka orders events within a partition; events on
**different** topics have no order between them, so a key may arrive before its use case. The
gateway therefore reads a use case it has not heard of as unknown, never as retired — and an upsert
of something inside a **retired** use case is ignored (a key is written deactivated). And because
the retirement lives on the use case's own key, which a purge later replaces, **retiring also ends
each child on the child's own key**: every active key is revoked, and every member, group grant,
budget, rate limit, rule and the pipeline get their removal event; a purge sends them once more, for
a use case retired before retirement did. So the last record of every key of a retired use case
ends it, and no replay in any order — a rebuild, or a consumer group whose offsets were reset —
brings a retired or purged use case's access back, even onto a use case created later under the
same slug. A use case purged by an earlier version is the exception: its children's last records
still grant, and Management no longer holds the rows that would end them. What a broker must
provide is in [`INTEGRATIONS.md`](INTEGRATIONS.md).

### 4.2 One record

| Part                 | What it holds                                                                                                                                                                                                                 |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Topic                | one of the nine in §4.3                                                                                                                                                                                                        |
| Key                  | the entity's natural key, UTF-8: the payload's `id`, else `prefix`, else `slug`, else `name`, else `use_case` — and for a membership `slug\|username`, for a group grant `slug\|group`, because on a compacted topic two members of one use case must not share a key |
| Value                | the payload, a JSON object, UTF-8, at most 1,000,000 bytes                                                                                                                                                                    |
| Header `event_type`  | the event type, e.g. `usecase.upserted`                                                                                                                                                                                       |
| Header `traceparent` | the W3C trace context of the request that caused the change, when it was traced; the consumer continues that trace, so a change is one trace across both planes                                                                |

There is **no version field, no schema identifier and no schema registry**. The event type and the
payload's field names are the contract, and §4.5 says how the two planes are kept to it. The Compose
stack runs a `schema-registry` container that neither plane uses.

### 4.3 The events

**Management sends** and **Gateway applies** are read from the code: `_TOPIC_FOR` in
`apps/outbox/subscriber.py`, and `HANDLERS` in `gateway/src/aira_gateway/consumer/apply.py`. **Key**
names the payload field or fields the record key is made of (§4.2).

| Event type | Topic | Key | Management sends | Gateway applies | Meaning |
| ---------- | ----- | --- | :--------------: | :-------------: | ------- |
| `usecase.upserted` | `aira.usecases` | `slug` | ✓ | ✓ | A use case was created or changed. Also sent, without the model, for every live use case a deleted model was released to. The gateway upserts its row. |
| `usecase.deleted` | `aira.usecases` | `slug` | ✓ | ✓ | A use case was retired. The gateway deactivates its keys; deletes its members, group grants, budgets and their counters, rate limits, rules and pipeline; and keeps the row as a tombstone that grants nothing. Management follows it with each child's own removal event (§4.1). |
| `usecase.purged` | `aira.usecases` | `slug` | ✓ | ✓ | A retired use case was purged, after each child's removal event once more (§4.1). The gateway drops the tombstone; its `request_logs` stay. |
| `membership.upserted` | `aira.memberships` | `slug\|username` | ✓ | ✓ | A person was granted the use case by name, or given another role in it. |
| `membership.removed` | `aira.memberships` | `slug\|username` | ✓ | ✓ | A person's membership ended. |
| `use_case_group.granted` | `aira.memberships` | `slug\|group` | ✓ | ✓ | A Keycloak group was granted the use case, or given another role in it. |
| `use_case_group.revoked` | `aira.memberships` | `slug\|group` | ✓ | ✓ | A group grant was revoked. |
| `api_key.created` | `aira.api-keys` | `prefix` | ✓ | ✓ | A key was issued. A key once revoked is never reactivated by a late redelivery, and one for a retired use case is written deactivated. |
| `api_key.revoked` | `aira.api-keys` | `prefix` | ✓ | ✓ | A key was revoked — by hand, or because its owner's last grant on the use case ended. The gateway stamps `revoked_at` when it learns of it. |
| `pipeline.upserted` | `aira.pipelines` | `use_case` | ✓ | ✓ | A use case's pipeline was saved; one with no steps is cleared. |
| `pipeline.deleted` | `aira.pipelines` | `use_case` | ✓ | ✓ | A use case was retired or purged, and its pipeline ended with it. No route deletes a pipeline: clearing one is a save with no steps. |
| `budget.upserted` | `aira.budgets` | `id` | ✓ | ✓ | A budget was set on a use case, or the installation's own (`use_case` empty). |
| `budget.deleted` | `aira.budgets` | `id` | ✓ | ✓ | A budget was removed. Its counters stay, so a budget set again within the same period starts from what was already spent. |
| `ratelimit.upserted` | `aira.rate-limits` | `id` | ✓ | ✓ | A rate limit was set. |
| `ratelimit.deleted` | `aira.rate-limits` | `id` | ✓ | ✓ | A rate limit was removed. |
| `anomaly_rule.upserted` | `aira.anomaly-rules` | `id` | ✓ | ✓ | A rule was set: a use case's, or a global one (`use_case` null). |
| `anomaly_rule.deleted` | `aira.anomaly-rules` | `id` | ✓ | ✓ | A rule was removed. |
| `model.upserted` | `aira.models` | `name` | ✓ | ✓ | A model was catalogued or changed, also by the local-model seed. |
| `model.deleted` | `aira.models` | `name` | ✓ | ✓ | A model was removed from the catalogue. |
| `role.upserted` | `aira.roles` | `slug` | ✓ | ✓ | A stored role was created or changed — IT Steuerung's permissions included. |
| `role.removed` | `aira.roles` | `slug` | ✓ | ✓ | A stored role was deleted. |

### 4.4 Payloads

The fields each event carries, wherever it is sent from — the views and the seeds build it with the
same function (`apps/usecases/views/payloads.py`, `catalog/views.py` `_payload`,
`anomalies/views.py` `rule_payload`, `roles/views.py` `_event`) or as a literal beside the `emit`.

| Events | Fields | Notes |
| ------ | ------ | ----- |
| `usecase.upserted`, `usecase.deleted` | `slug`, `name`, `description`, `processing_notes`, `store_payloads`, `payload_visibility`, `restrict_members_to_own_requests`, `tools_enabled`, `include_reasoning`, `prompt_caching_enabled`, `prompt_cache_ttl`, `retention_days`, `allowed_models` | The use case as it stands; `usecase.deleted` carries it as it stood when retired, so a read-model rebuilt from the compacted topic still knows its retention. `restrict_members_to_own_requests` is the retired form of `payload_visibility`, derived from it, for a gateway that predates it. `allowed_models` is sorted. |
| `usecase.purged` | `slug` | |
| `membership.upserted` | `slug`, `username`, `role` | `username` is Management's account name, which the gateway matches against a token's `preferred_username`. |
| `membership.removed` | `slug`, `username` | |
| `use_case_group.granted` | `slug`, `group`, `role` | `group` is the Keycloak group path. |
| `use_case_group.revoked` | `slug`, `group` | |
| `api_key.created` | `prefix`, `key_hash`, `subject`, `issued_by`, `use_case`, `label`, `status`, `expires_at` | The hash, never the key. `subject` is the owner's username; `expires_at` is ISO 8601. |
| `api_key.revoked` | `prefix`, `use_case`, `status` | |
| `pipeline.upserted` | `use_case`, `steps`, `fallback_models` | |
| `pipeline.deleted` | `use_case` | Sent when its use case is retired or purged. |
| `budget.upserted` | `id`, `use_case`, `scope`, `subject`, `period`, `limit_cost`, `limit_tokens`, `limit_requests`, `enabled` | `limit_cost` is a decimal string. |
| `budget.deleted` | `id`, `use_case`, `period` | `period` is sent for the installation's budget only. |
| `ratelimit.upserted` | `id`, `use_case`, `scope`, `subject`, `limit_rpm`, `burst`, `enabled` | |
| `ratelimit.deleted` | `id`, `use_case` | |
| `anomaly_rule.upserted` | `id`, `use_case`, `name`, `kind`, `window_minutes`, `threshold`, `parameter`, `min_sample`, `action`, `target`, `action_minutes`, `throttle_rpm`, `enabled` | `use_case` null is a global rule. |
| `anomaly_rule.deleted` | `id`, `use_case` | `use_case` is left out when a rule is deleted through `/api/v1/anomaly-rules/`. |
| `model.upserted` | `name`, `display_name`, `approved`, `provider`, `input_price_per_million`, `output_price_per_million`, `cached_input_price_per_million`, `cache_write_price_per_million`, `capabilities`, `publisher`, `platform`, `addressing`, `underlying_model`, `context_window`, `max_output_tokens`, `default_max_output_tokens`, `thinking`, `embedding`, `attachments`, `hosting`, `fixed_sampling`, `default_sampling`, `deprecated`, `numeric_id` | Prices are decimal strings, or null for none on file. `fixed_sampling` is a boolean, `false` for no restriction; `default_sampling` an object or null. |
| `model.deleted` | `name` | |
| `role.upserted` | `slug`, `label`, `group_path`, `permissions`, `builtin` | |
| `role.removed` | `slug` | |

### 4.5 Keeping the two planes to one contract

**Within one release** the planes are compared by tests that read both, and CI runs them on every
change:

- `tools/tests/test_the_two_planes_agree_on_the_event_vocabulary.py` — every event type Management
  emits has a handler on the gateway, and every handler an emitter or a stated reason;
- `tools/tests/test_a_purged_use_case_stays_purged_under_every_replay.py` — retiring, purging and
  creating the slug again, then the topics replayed in every order: nothing of the old use case
  grants, whether it was retired now or before retirement ended its children;
- `management/backend/tests/test_outbox_routing.py` — every event emitted has a topic, each kind of
  entity its own compaction key, and each event its one builder;
- `tools/tests/test_a_model_event_is_applied_whole.py` — every field of `model.upserted` is one the
  gateway applies;
- `tools/tests/test_what_management_accepts_fits_the_gateway.py` — every text Management accepts
  fits the gateway column it is written to, since PostgreSQL would refuse the whole event;
- `tools/tests/test_kafka_topics_are_created.py` — every topic the code publishes to is created;
- `tools/tests/test_both_planes_read_one_pipeline_vocabulary.py` — a pipeline's words and bounds
  are one set on both planes;
- `tools/tests/test_the_event_catalogue_names_every_event.py` — this section, against both planes.

**Across releases** nothing checks a payload against a schema and nothing negotiates a version: a
renamed field reads as absent on the other plane, and a removed event type as unknown. So an event
changes additively — a new field beside the old one, which goes once no running gateway reads it —
and the code is written for Management being upgraded before the gateway: Management still sends
`restrict_members_to_own_requests`, derived, for gateways older than `payload_visibility`. What a
gateway does with what it does not expect is in `consumer/apply.py` and `consumer/worker.py`:

| What arrives | What the consumer does | Logged as |
| ------------ | ---------------------- | --------- |
| an event type it has no handler for — what an older gateway gets from a newer Management | skips it; the change does not take effect on this gateway | `config_event_not_applied`, with the payload's field names, never their values |
| a record without a readable `event_type` header | skips it | `config_event_without_type`, with topic, partition and offset |
| a value that is not a JSON object (a compacted topic's tombstone included), a missing required field, or a value the database refuses | skips it, and marks its span failed | `config_event_failed`, with event type, topic, partition, offset and error type |
| a field it does not know | ignores it: each handler reads only the fields it names | — |
| a field it knows, absent | reads it as its handler states, below | — |

The consumer never stops on one event: one it could not get past would be read again after every
restart and freeze the read-model, and a revoked key would go on working. **A skipped event is not
retried.** The group's offset moves past it, so the change takes effect only when the same thing is
changed again in Management or the topics are replayed.

What an absent field is read as differs by handler, on purpose (`consumer/apply.py`):

- **a use case** — `tools_enabled` and `prompt_caching_enabled` off; `store_payloads` and
  `include_reasoning` on; `prompt_cache_ttl` `5m`; `retention_days` the installation's default. An
  absent `allowed_models` leaves the release unrecorded, which the gateway serves as unrestricted,
  while an empty list releases nothing. An absent `payload_visibility` is read from
  `restrict_members_to_own_requests`, and a value the gateway does not know as `oversight`, the
  narrowest;
- **a model** — `approved` absent is approved; an absent declaration field (capabilities, limits,
  thinking, …) leaves what the gateway holds, and a null one resets it; an absent or null price is
  no price on file;
- **a membership or a group grant** — `role` absent is `user`, and a role the gateway does not know
  counts as the weakest;
- **a key** — `expires_at` absent or unreadable is no expiry, safe only because the key can still be
  revoked;
- **an anomaly rule** — without a `use_case` key at all the event is skipped, since reading it as
  global would widen a rule that can block traffic;
- **a role** — `permissions` absent is none.
