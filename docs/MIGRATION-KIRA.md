# Migrating a KIRA client to AIRA

For somebody who has a working client of the predecessor's API and wants it to keep working
against AIRA.

The requests below are the ones a client sends, and the responses are shown as the gateway words
them, shortened with `…` — a model's own text differs from run to run. Where AIRA answers
differently from the predecessor, it says so.

> The short version: a KIRA client changes its **base URL** and its **credential**. What it cannot
> bring with it is the thing AIRA exists for — every request has to belong to a **use case**, and
> somebody has to have released the models it may call. Those are administration steps, done once
> per client, and the rest of this page is them.

Related reading: [`deployment/`](deployment/README.md) to get a stack running,
[`INTEGRATIONS.md`](INTEGRATIONS.md) for what the connected systems must provide,
[`MIGRATION-GEMINI.md`](MIGRATION-GEMINI.md) for clients that speak Google's API instead.

---

## 1. What does not change

The compatibility surface is mounted at **`/kira/api/external`** and keeps the predecessor's paths,
field names, response shapes and error codes:

| | |
|---|---|
| `POST /kira/api/external/chat` | one answer |
| `POST /kira/api/external/streaming-chat` | SSE, `status: update` … then `status: completed` |
| `POST /kira/api/external/embed` | one vector |
| `GET /kira/api/external/models` | the catalog, as integer ids |
| `GET /kira/api/external/health`, `/version-info` | unchanged shapes, no credential needed. Outside a local or demo installation `/health` lists the upstreams only to an operator (`operations.diagnose`, or the break-glass key); anybody else gets the verdict |
| `GET /kira/api/external/ki-usage` | unchanged shape; needs `report.read_all`, so a key bound to a use case never reaches it. Under strict data mode, the default, a row is a **use case** — its slug in `user_id` — rather than a person |

Errors keep the predecessor's envelope — a flat `{"code": …, "message": …}`, never Google's nested
`error` object — so a client that branches on `code` keeps branching on the same strings.

Every response carries `Deprecation: true` and a `Link: </docs/migration>; rel="deprecation"`
header. That is deliberate: a compatibility surface with no announced ending becomes a permanent
one. The gateway itself serves nothing at `/docs/migration`; this page is the guide.

---

## 2. What has to be set up first

Four steps, once per client. They can be done in the console (`http://localhost:4200`) or over the
API; the API is shown because it is scriptable.

All of these need a bearer token: for a **Global Administrator** — or whoever else holds
`usecase.create` — to create the use case, and for an administrator **of that use case** for the
rest. The console uses the signed-in person's. A script takes one from a confidential Keycloak
client with the `client_credentials` grant, whose service account is in the right group or holds the
grant (the password grant is off). On a local stack, the development realm's
`aira-integration-tests-admin` is such a client for a Global Administrator:

```bash
TOKEN=$(curl -s http://localhost:8080/realms/aira/protocol/openid-connect/token \
  -d grant_type=client_credentials \
  -d client_id=aira-integration-tests-admin -d client_secret=integration-tests-admin-secret \
  | jq -r .access_token)
```

Fetch it from `localhost`, as above: the issuer is part of the token, and one minted through
another host name is refused.

### 2.1 Which models exist at all

A model is usable only if it has been catalogued **and approved** — by the Global Administrator, or
whoever else holds `catalog.write`. Ask first, because you can only release what is approved:

```bash
curl -s http://localhost:8002/api/v1/models/ -H "authorization: Bearer $TOKEN"
# [{"name":"qwen3:0.6b","approved":true,"numeric_id":1004, …},
#  {"name":"all-minilm","approved":true,"numeric_id":9002, …}]
```

Two more conditions apply when a request is served: where a cost budget binds, the model needs a
price, and its region must be one the installation permits.

`numeric_id` is the integer a KIRA client sends as `model_id`. It is assigned in the catalog, so if
your clients send the predecessor's ids (`1002`, `1004`, …) an administrator can give the
corresponding AIRA models exactly those numbers — then the client's model ids do not change either.
The demo does exactly that: its chat model carries `1004`, the predecessor's own chat id, because a
migration guide whose one runnable command uses a different number is demonstrating the opposite of
what it claims.

### 2.2 Create the use case

```bash
curl -X POST http://localhost:8002/api/v1/use-cases/ \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"slug": "migration-demo", "name": "Migration demo", "description": "from KIRA"}'
# 201  {"slug":"migration-demo", …, "allowed_models":[], …}
```

Note `allowed_models: []`. **Empty means none** — a new use case can call nothing until somebody
releases a model to it. That is the point, not an oversight.

### 2.3 Release the models it may call

```bash
curl -X PATCH http://localhost:8002/api/v1/use-cases/migration-demo/ \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"allowed_models": ["qwen3:0.6b", "all-minilm"]}'
# 200  {"allowed_models":["all-minilm","qwen3:0.6b"], …}
```

Two gates, two owners: whoever holds `catalog.write` decides what the installation may use at all,
an administrator **of this use case** decides which of those it reaches. The inner gate can never
open what the outer one closed — releasing an unapproved model is refused by name.

A chat request for a model that was not released:

```
400  {"code":"VALIDATION_ERROR",
      "message":"No model could serve this request (gpt-4o: 'gpt-4o' has not been released to use case 'migration-demo'. …)."}
```

### 2.4 Who may use it

People, by username:

```bash
curl -X POST http://localhost:8002/api/v1/use-cases/migration-demo/members/ \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"username": "ucuser", "role": "user"}'
# 201  {"username":"ucuser","role":"user", …}
```

…or a **Keycloak group**, which is usually what you want for a department:

```bash
curl -X POST http://localhost:8002/api/v1/use-cases/migration-demo/groups/ \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"group_path": "/aira/migration-demo", "role": "user"}'
# 201  {"group_path":"/aira/migration-demo","role":"user","granted_by":"…","reaches":0, …}
```

`role` is `user` or `admin`; an admin of a use case may change its configuration, release models
and issue keys. `reaches` counts the people Management has seen sign in with that group, so `0`
means none of them **yet** — the group may be empty, not created yet, or its members may not have
signed in. A group grant is checked for its shape, not against Keycloak, so a mistyped path is
accepted and reaches nobody; the console picks the group from a directory search instead. AIRA never
writes to the directory.

### 2.5 A credential for the client

```bash
curl -X POST http://localhost:8002/api/v1/use-cases/migration-demo/api-keys/ \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"label": "kira-client", "owner": "ucuser"}'
# 201  {"api_key":"aira_a69e58c1_68d4…","prefix":"a69e58c1","label":"kira-client",
#       "owner":"ucuser","issued_by":"…","use_case":"migration-demo","expires_at":"…"}
```

**`api_key` is shown once and never again** — only its hash is stored. `owner` is the person who
answers for the credential, and their name appears beside every request it makes; it must be
somebody with access to this use case. `issued_by` records who created it, when that is a different
person.

**Every key expires**: after 30 days unless `expires_in_days` asks for another lifetime, and after
180 days at most (the installation's `AIRA_API_KEY_DEFAULT_DAYS` and `AIRA_API_KEY_MAX_DAYS`). An
expired key is answered `401 INVALID_TOKEN`, so issue the next one before `expires_at`.

The use case and the key reach the gateway over Kafka, on the outbox relay's next run — every
10 seconds in the Compose stack. Until the use case has arrived, a request is answered `403`
*"Use case 'migration-demo' is not known to this gateway"*; until the key has, `401
INVALID_TOKEN`. A retired use case answers `403` for good.

---

## 3. What the client changes

### 3.1 The base URL

```diff
- https://kira.example.internal/api/external/chat
+ https://aira.example.internal/kira/api/external/chat
```

### 3.2 The credential

A KIRA bearer token is replaced by the API key — in either header AIRA reads, `x-goog-api-key` or
`authorization: Bearer`, or in a `?key=` query parameter:

```
x-goog-api-key: aira_a69e58c1_68d4…
```

An OIDC bearer token also works, and is the better choice for a client that acts *as a person*.

### 3.3 The use case — `X-AIRA-Use-Case`

This is the one genuinely new concept, and how it behaves depends on the credential:

| the caller belongs to | what happens |
|---|---|
| **exactly one** use case | nothing to do — the request is attributed to it |
| **several** | `403` naming the candidates; send `X-AIRA-Use-Case` |
| **none** | `403` — an unattributed request would bypass every budget and limit |

An API key issued for a use case belongs to exactly one, so a migrated client normally sends
**nothing**. The header exists for an OIDC caller who is in several:

```bash
curl -X POST http://localhost:8001/kira/api/external/chat \
  -H "x-goog-api-key: $KEY" \
  -H 'X-AIRA-Use-Case: migration-demo' \
  -H 'content-type: application/json' \
  -d '{"request":{"parts":[{"text":"Sag OK."}]},"model_id":1004,"maxTokens":16}'
# 200  {"parts":[{"text":"Sag."}],"usage_data":{"token_input":20,"token_output":4}}
```

A client that can change its base URL but not its headers names the use case in the path instead:
`https://aira.example.internal/uc/migration-demo/kira/api/external/chat`. Where both appear the
header wins.

**Neither grants access — they only choose among what you already have.** Naming somebody else's
use case is refused:

```
403  {"code":"STANDARD_USER_PERMISSION_REQUIRED",
      "message":"API key is bound to use case 'migration-demo'."}
```

---

## 4. Where AIRA deliberately answers differently

None of these is an accident, and each has a reason a migrating client can act on.

| | AIRA | why |
|---|---|---|
| **Unmodelled request fields** | accepted, and named in an `X-AIRA-Unmodelled-Fields` response header | a client may send fields this surface does not act on; it keeps working, and none is dropped unseen. Only a field that differs from a modelled one in spelling alone — `conversationHistory` for `conversation_history` — is refused, `400 VALIDATION_ERROR` naming the spelling this surface takes, because accepting it would answer without what was sent |
| **`GET /models`** | needs a credential | the catalog names what an installation runs and what it costs |
| **Attachment signatures** | checked | a file declared `application/pdf` that is not one is refused rather than sent |
| **`responseSchema`** | bounded (32 KiB, 8 levels, 256 properties) and unknown fields refused | an unbounded schema from a caller is an input nobody bounded |
| **`thinking`** | checked against the model | see below |
| **Error codes** | more specific (`INVALID_MAX_TOKENS`, `EMBEDDING_BOUND_EXCEEDED`, …) | the predecessor's codes are all still produced where it produces them; the extra ones name cases it had no word for |
| **`INVALID_TOKEN` vs `NOT_AUTHENTICATED`** | kept apart | a rejected credential is a security signal, an absent one is a deployment slip |
| **What happened** | headers only: `X-AIRA-Request-Id` on every response, `X-AIRA-Reason` on a refusal, `X-AIRA-Model` and `X-AIRA-Pipeline` on an answer | the envelope and the stream events stay the predecessor's — a client that parses them strictly keeps working — and the request id finds the request's details in the console, which say what the pipeline did and which models were tried. A stream that breaks off still ends without `completed`; its reason is in those details |
| **Messages** | English | AIRA is English throughout |
| **`/health`** | cached background probe | probing every upstream on every call makes readiness as slow as the slowest provider — a health check that can take down a healthy service |
| **`/ki-usage`** | grouped by user, `model_id: 0` | AIRA reports the model dimension in its own rows rather than fabricating a cross-tabulation. The console's **Reporting** screen and its CSV export are the fuller answer |

**Thinking.** `"thinking": {"mode": …, "tokens": …}` is checked against what the catalogue declares
for the model: `disabled`, `auto`, `limited` with `tokens` inside the model's bounds, or a level word
the model is declared to take (`low`, `high`, …), passed upstream untranslated. Anything else is a
`422` with the contract's own codes — `INVALID_THINKING_MODE`, `MISSING_THINKING_TOKEN_COUNT`,
`THINKING_TOKEN_COUNT_TOO_LOW`, `THINKING_TOKEN_COUNT_TOO_HIGH`, `UNEXPECTED_THINKING_TOKEN_COUNT`.
Without the field the model's catalogued default applies. `GET /models` shows each model's
`thinkingConfig`: the control modes it takes, its token bounds and its default.

**Attachments.** A part carries either `text` or `mime_type` and base64 `data`. Fifteen media types
are accepted — `application/pdf`, `application/x-javascript`, `text/javascript`, `text/plain`,
`text/html`, `text/md`, `text/csv`, `text/xml`, `text/rtf`, `image/png`, `image/jpg`, `image/jpeg`,
`image/webp`, `image/heic`, `image/heif` — at most 16 parts and 6 MiB of them in all. A PDF or an
image must carry its format's signature bytes; the text types have none to check. A model reads
only the types its catalogue entry declares: one that cannot read the attachment is skipped by a
fallback chain, and refused when it is the only candidate.

---

## 5. Checking it worked

Everything a migrated client does is visible in the console:

* **Use case → Requests** — every request, which model, how it ended, what it cost; this
  surface's calls are the operations `chat`, `streaming-chat` and `embed`
* **Use case → Overview**, the **Consumption** card — tokens and spend, whether or not a budget is
  set
* **Reporting** — across use cases, with a CSV export

A request refused once it is attributed to the use case is recorded too — the log says what was
*asked*, not only what was served. One refused before that — a rejected key, a use case the caller
is not in or did not name — leaves no audit row; its trace span is its record.

---

## 6. The surface is transitional

`Deprecation: true` is on every response, and a sunset date can be configured
(`AIRA_KIRA_SUNSET`, see [`CONFIGURATION.md`](CONFIGURATION.md)) so clients learn the ending from
the API rather than from an email. Every audit row records the surface it came through (`api` is
`kira`) and its operation, so a use case's own **Requests** tab shows whether it still calls this
surface; reporting has no breakdown by surface.

New work should use the Gemini surface: see [`MIGRATION-GEMINI.md`](MIGRATION-GEMINI.md).
