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
| `POST /kira/api/external/chat` | one answer; functions declared in `tools` come back as `function_call` parts |
| `POST /kira/api/external/streaming-chat` | SSE, `status: update` …, a `status: function_call` per call, then `status: completed` |
| `POST /kira/api/external/embed` | one vector |
| `POST /kira/api/external/rerank` | the records, ordered and scored |
| `GET /kira/api/external/models` | the catalog, as integer ids, with `Chat`, `Embedding` and `Reranker` |
| `GET /kira/api/external/health`, `/version-info` | unchanged shapes, no credential needed. Outside a local or demo installation `/health` lists the upstreams only to an operator (`operations.diagnose`, or the break-glass key); anybody else gets the verdict |
| `GET /kira/api/external/ki-usage` | unchanged shape, JSON or CSV — but about **the caller only** (see §4) |

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

A KIRA bearer token is replaced by the API key — in either header this surface reads,
`x-goog-api-key` or `authorization: Bearer`:

```
x-goog-api-key: aira_a69e58c1_68d4…
```

Not in the URL. A `?key=` query parameter is Google's convention, read on the Gemini interface for
the clients that follow it; a KIRA client always sent its credential in a header, and a key in a
URL is written into the log of every proxy it passes. Here such a request is answered
`401 NOT_AUTHENTICATED`, saying where to put the key instead — and that one is best replaced.

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
| **Unmodelled request fields** | accepted, and named in an `X-AIRA-Unmodelled-Fields` response header | a client may send fields this surface does not act on; it keeps working, and none is dropped unseen. Only a field that differs from a modelled one in spelling alone — `conversationHistory` for `conversation_history` — is refused, `422 VALIDATION_ERROR` naming the spelling this surface takes, because accepting it would answer without what was sent |
| **`GET /models`** | needs a credential | the catalog names what an installation runs and what it costs |
| **Attachment signatures** | checked | a file declared `application/pdf` that is not one is refused rather than sent |
| **`responseSchema`** | bounded (32 KiB, 8 levels, 256 properties) and unknown fields refused | an unbounded schema from a caller is an input nobody bounded |
| **How much a request holds** | at most 10 000 turns and 20 000 parts across them, and a body of at most 250 000 JSON values nesting at most 64 levels; past one, `422 VALIDATION_ERROR` naming it, before the request is validated | what a body costs is its structure, and all of it is paid before a rate limit can refuse anything. No chatbot's conversation comes near these, and a long document travels as text — one value, however long |
| **`thinking`** | checked against the model | see below |
| **Error codes** | more specific where the predecessor had no word (`EMBEDDING_BOUND_EXCEEDED`, …) | the predecessor's codes are all still produced where it produces them, with its statuses: an upstream failure is `502 EXTERNAL_KI_API_ERROR` and a provider's `429` stays `429`; an attachment type nobody accepts, a schema it cannot read and a `temperature` outside 0–2 are `422`; `maxTokens` of 0 or less, an unknown `task_type` and an empty or blank embedding text are `VALIDATION_ERROR`; a `model_id` nothing has — zero and negative ones included — is always `422 MODEL_NOT_FOUND`. Two keep their own answer: a thinking word the model does not offer is `INVALID_THINKING_MODE` naming what it offers, because the words are the model's, not a fixed list; and an id the catalogue assigns twice is `503`, because the installation is misconfigured, not the request |
| **A function result** | answers a call of the model turn **directly before** it | Anthropic and OpenAI refuse a conversation whose result is separated from its call, and Gemini reads it as the result of whichever call has its name — an answer to the wrong question. Here it is `422 INVALID_FUNCTION_RESPONSE` before anything is spent |
| **Function parts without `tools`** | refused, `422 INVALID_TOOL_CONFIGURATION`, for a call in the history as well as for a result | a conversation carrying a call is continued by a model that must know the function, and Anthropic refuses a conversation with a call whose function it was not given |
| **`opaque_metadata`** | only `thoughtSignature` is read back | the predecessor copies every key onto the provider's part, so a caller could set any field of it — a file reference among them. Nothing but the signature reaches the provider |
| **An empty answer** | `502 EXTERNAL_KI_API_ERROR` naming why the model stopped, when it has no text, no call and no output token | a blocked prompt, or a cap spent on thinking, served as `parts: []` reads as *the model had nothing to say*. As the predecessor answers it |
| **A structured answer that did not finish** | `502 EXTERNAL_KI_API_ERROR`; on the stream, no `completed` | a document cut off at the cap still looks like JSON up to where it stops. The predecessor returns it with `200` |
| **A structured answer on the stream** | no `update` events: the document comes whole, in `completed` | a fragment of JSON is not a message. As the predecessor sends it |
| **`responseSchema`** | (above) — unknown keywords refused, not dropped | a constraint dropped silently is an answer that breaks it |
| **An embedding text too long for the model** | refused by the provider, `502` with its reason | the predecessor lets Google cut the text off; a vector of part of a document is filed under the whole of it |
| **`/rerank`** | `model_id`, `query` and `records` required | a ranking billed to a model nobody named is a cost nobody chose |
| **A budget's `429`** | no `Retry-After`; when the budget resets is in the message | a client that honours the header would sleep until the period ends |
| **CORS** | no origin unless `AIRA_CORS_ORIGINS` names it | the predecessor allows every origin with credentials, which lets any website a user visits call the API as that user |
| **`INVALID_TOKEN` vs `NOT_AUTHENTICATED`** | kept apart | a rejected credential is a security signal, an absent one is a deployment slip |
| **What happened** | headers only: `X-AIRA-Request-Id` on every response, `X-AIRA-Reason` on a refusal, `X-AIRA-Model` and `X-AIRA-Pipeline` on an answer | the envelope and the stream events stay the predecessor's — a client that parses them strictly keeps working — and the request id finds the request's details in the console, which say what the pipeline did and which models were tried. A stream that breaks off still ends without `completed`; its reason is in those details |
| **Messages** | English | AIRA is English throughout |
| **`/health`** | cached background probe | probing every upstream on every call makes readiness as slow as the slowest provider — a health check that can take down a healthy service |
| **`/ki-usage`** | the caller's own rows, one per model, in the use cases the credential may act in (a key: its own use case); `user_id` is the caller's name and `model_id` the model's id. Any credential may ask, and nobody — whatever their role — gets another person's rows. A window spans at most 366 days | the endpoint names a person in every row, and another person's figures are shown in the console, under the rules that decide who may see them. The console's **Reporting** screen and its CSV export are the answer for a team or an installation |

**Thinking.** `"thinking": {"mode": …, "tokens": …}` is checked against what the catalogue declares
for the model: `disabled`, `auto`, `limited` with `tokens` inside the model's bounds, or a level word
the model is declared to take (`low`, `high`, …), passed upstream untranslated. Anything else is a
`422` with the contract's own codes — `INVALID_THINKING_MODE`, `MISSING_THINKING_TOKEN_COUNT`,
`THINKING_TOKEN_COUNT_TOO_LOW`, `THINKING_TOKEN_COUNT_TOO_HIGH`. A `tokens` beside any mode but
`limited` is ignored, as the predecessor ignores it, and named in `X-AIRA-Unmodelled-Fields`.
Without the field the model's catalogued default applies. `GET /models` shows each model's
`thinkingConfig`: the control modes and level words it takes in one list, its token bounds (`null`
where none is declared) and its default — `auto` where none is declared, because then the model
decides. `usage_data.token_output` counts the answer without its thinking.

**Attachments.** A part carries either `text` or `mime_type` and base64 `data`. Fifteen media types
are accepted — `application/pdf`, `application/x-javascript`, `text/javascript`, `text/plain`,
`text/html`, `text/md`, `text/csv`, `text/xml`, `text/rtf`, `image/png`, `image/jpg`, `image/jpeg`,
`image/webp`, `image/heic`, `image/heif` — at most 16 parts and 6 MiB of them in all. A PDF or an
image must carry its format's signature bytes; the text types have none to check. A model reads
only the types its catalogue entry declares: one that cannot read the attachment is skipped by a
fallback chain, and refused when it is the only candidate. In a turn the attachments go to the model
first and the text after them, as the predecessor sends a turn. An attachment in
`system_instruction` is ignored, as the predecessor ignores it, and named in
`X-AIRA-Unmodelled-Fields`.

**Functions.** A request offers functions in `tools` — `{"type": "function", "name", "description",
"parameters"}`, `type` optional, the other three required — and only where the use case has tool
calling enabled; otherwise the answer is `422 INVALID_TOOL_CONFIGURATION`. A missing or empty field
is `422 VALIDATION_ERROR`; a filled one the predecessor would refuse — a description of blanks, a
name declared twice, a `parameters` schema outside its rule — is `422 INVALID_TOOL_CONFIGURATION`.
The rule, at every level of the schema: the keywords `$ref`, `additionalProperties`, `anyOf`,
`default`, `description`, `enum`, `format`, `items`, `maxItems`, `maxLength`, `maxProperties`,
`maximum`, `minItems`, `minLength`, `minProperties`, `minimum`, `nullable`, `pattern`,
`properties`, `required`, `title` and `type` only; `type` always, one of `object`, `array`,
`string`, `number`, `integer`, `boolean` — or one of them beside `"null"`, `["string", "null"]`,
which is how JSON Schema and its generators write an optional argument, carried to the model as
written; `required` naming only properties; `items` on an array and nowhere else; `anyOf` and
`enum` never empty. The root is an `object` — a function that takes nothing declares
`{"type": "object"}`. The model's call comes back as a part:

```json
{"type": "function_call", "id": "…", "name": "get_weather", "arguments": {"city": "Bonn"},
 "opaque_metadata": {"thoughtSignature": "…"}}
```

`opaque_metadata` is always there, `null` when the model sent no signature. The client runs the
function, puts the call into `conversation_history` as a `model` turn — with its `opaque_metadata`
unchanged, which a thinking model needs to continue — and sends the outcome in the next request:

```json
{"type": "function_result", "call_id": "…", "name": "get_weather", "result": {"temperature": 14}}
```

`type` may be left out on both parts: a part with `call_id` or `result` is a result, a part with a
`name` and no text or attachment is a call. A result may add `"error": {…}`; the model then reads
`{"result": …, "error": …}`. A call without an `id` may be replayed and cannot be answered.

Refused with the predecessor's codes, all `422`: a call to a function the request's `tools` do not
declare is `INVALID_TOOL_CONFIGURATION`; a call whose name is not a function name, two calls with
one `id`, a result without `name`, a result that answers no call of the model turn directly before
it, or answers it under another name, are `INVALID_FUNCTION_RESPONSE`. A call belongs only in a
`model` turn and a result only in a `user` turn or the request; anywhere else is `VALIDATION_ERROR`.
On the stream every call is its own `function_call` event, and `completed` carries them too. The
gateway never runs a function.

**Embeddings of `gemini-embedding-2`.** For a model whose name at Google begins with
`gemini-embedding-2`, each text is sent inside its task's prompt — `task: search result | query: …`
for `RETRIEVAL_QUERY`, the default, `title: none | text: …` for `RETRIEVAL_DOCUMENT`, and so on for
all eight — as its own part, and a list is one embedding of all its parts, which is how the
predecessor embeds them: an index it built keeps matching. The model's catalogue entry must declare
the task types it takes. Two widths of one model are two catalogue entries, each with its own id and
`embedding.default`, the second naming the first in `addressing.model` — for example `2003` at 3072
and `2004` at 768. Each is released, priced and permitted on its own.

**Reranking.** `POST /rerank` takes `{"model_id", "query", "records": [{"id", "title",
"content"}]}`, every field filled, and answers the records in the reranker's order, each with a
`score`. A model not declared a reranker is `422 NO_RERANKER_CAPABILITIES`. A ranking is booked in
the unit Google bills — one query per hundred records — against the use case's rate limit and
budget, and priced at the entry's input price per million such units. Google documents its ranking
API at `global`, a location that names no region, so the installation permits the reranker there by
name:
`AIRA_ALLOWED_MODEL_REGIONS=global=semantic-ranker-default-004`.

**Speech.** This surface has no text-to-speech endpoint. A client that needs one uses the Gemini
interface with the same credential and use case:

```bash
curl -X POST http://localhost:8001/v1beta/models/gemini-2.5-flash-tts:generateContent \
  -H "x-goog-api-key: $KEY" \
  -H 'content-type: application/json' \
  -d '{"contents":[{"role":"user","parts":[{"text":"Guten Morgen"}]}],
       "generationConfig":{"responseModalities":["AUDIO"],
         "speechConfig":{"voiceConfig":{"prebuiltVoiceConfig":{"voiceName":"Kore"}}}}}'
# 200  {"candidates":[{"content":{"parts":[{"inlineData":
#        {"mimeType":"audio/L16;codec=pcm;rate=24000","data":"…"}}]}, …}], …}
```

**The stream** sends a `: ping` comment every 15 seconds while the model is quiet, and
`X-Accel-Buffering: no`, so a proxy neither closes nor buffers it.

---

## 5. Checking it worked

Everything a migrated client does is visible in the console:

* **Use case → Requests** — every request, which model, how it ended, what it cost; this
  surface's calls are the operations `chat`, `streaming-chat`, `embed` and `rerank`
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
