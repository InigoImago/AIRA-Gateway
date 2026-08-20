# Migrating a KIRA client to AIRA

For somebody who has a working **the predecessor API** client and wants it to keep working against AIRA.

Every request and response in this document was executed against a running stack on 2026-08-12 and
the outputs are what came back. Where AIRA answers differently from the predecessor, it says so.

> The short version: a KIRA client changes its **base URL** and its **credential**. What it cannot
> bring with it is the thing AIRA exists for — every request has to belong to a **use case**, and
> somebody has to have released the models it may call. Those are administration steps, done once
> per client, and the rest of this page is them.

Related reading: [`SETUP.md`](SETUP.md) to get a stack running,
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
| `GET /kira/api/external/health`, `/version-info`, `/ki-usage` | unchanged shapes |

Errors keep the predecessor's envelope — a flat `{"code": …, "message": …}`, never Google's nested
`error` object — so a client that branches on `code` keeps branching on the same strings.

Every response carries `Deprecation: true` and a `Link` to this page. That is deliberate: a
compatibility surface with no announced ending becomes a permanent one.

---

## 2. What has to be set up first

Four steps, once per client. They can be done in the console (`http://localhost:4200`) or over the
API; the API is shown because it is scriptable.

All of these need a token for a **Global Administrator** (creating a use case) or an administrator
**of that use case** (the rest). See [`INTEGRATIONS.md`](INTEGRATIONS.md) for obtaining one.

### 2.1 Which models exist at all

A model is usable only if a Global Administrator has catalogued **and approved** it. Ask first,
because you can only release what is approved:

```bash
curl -s http://localhost:8002/api/v1/models/ -H "authorization: Bearer $TOKEN"
# [{"name":"qwen3:0.6b","approved":true,"numeric_id":1004, …},
#  {"name":"all-minilm","approved":true,"numeric_id":9002, …}]
```

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

Two gates, two owners: a **Global Administrator** decides what the installation may use at all, an
administrator **of this use case** decides which of those it reaches. The inner gate can never open
what the outer one closed — releasing an unapproved model is refused by name.

Asking for a model that was not released:

```
400  "Use case 'migration-demo' has not been released 'gpt-4o'. …"
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
and issue keys. `reaches: 0` means nobody holds that group **yet** — the grant is not wrong, the
group is simply empty or not yet created. AIRA never writes to the directory.

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

The key reaches the gateway over Kafka. Measured on the walkthrough above: **about two seconds.**
If a fresh key is refused for a moment, that is why.

---

## 3. What the client changes

### 3.1 The base URL

```diff
- https://kira.example.internal/api/external/chat
+ https://aira.example.internal/kira/api/external/chat
```

### 3.2 The credential

A KIRA bearer token is replaced by the API key, in any of the three headers AIRA accepts:

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

**The header never grants access — it only chooses among what you already have.** Naming somebody
else's use case is refused:

```
403  {"code":"STANDARD_USER_PERMISSION_REQUIRED",
      "message":"API key is bound to use case 'migration-demo'."}
```

---

## 4. Where AIRA deliberately answers differently

None of these is an accident, and each has a reason a migrating client can act on.

| | AIRA | why |
|---|---|---|
| **Unknown request fields** | `422`, naming the field | the predecessor ignores them. A field accepted and dropped answers with a 200 and is wrong in a way nobody can see — eleven such fields were found on the Gemini surface, all silently doing nothing |
| **`GET /models`** | needs a credential | the catalog names what an installation runs and what it costs |
| **Attachment signatures** | checked | a file declared `application/pdf` that is not one is refused rather than sent |
| **`responseSchema`** | bounded (32 KiB, 8 levels, 256 properties) and unknown fields refused | an unbounded schema from a caller is an input nobody bounded |
| **Error codes** | more specific (`INVALID_MAX_TOKENS`, `EMBEDDING_BOUND_EXCEEDED`, …) | the predecessor's codes are all still produced where it produces them; the extra ones name cases it had no word for |
| **`INVALID_TOKEN` vs `NOT_AUTHENTICATED`** | kept apart | a rejected credential is a security signal, an absent one is a deployment slip |
| **Messages** | English | AIRA is English throughout (`CLAUDE.md` §2) |
| **`/health`** | cached background probe | probing every upstream on every call makes readiness as slow as the slowest provider — a health check that can take down a healthy service |
| **`/ki-usage`** | grouped by user, `model_id: 0` | AIRA reports the model dimension in its own rows rather than fabricating a cross-tabulation. The console's **Reporting** screen and its CSV export are the fuller answer |

---

## 5. Checking it worked

Everything a migrated client does is visible in the console:

* **Use case → Traces** — every request, which model, how it ended, what it cost
* **Use case → Consumption** — tokens and spend, whether or not a budget is set
* **Reporting** — across use cases, with a CSV export

If a request is refused, the audit trail records the refusal too — the log says what was *asked*,
not only what was served.

---

## 6. The surface is transitional

`Deprecation: true` is on every response, and a sunset date can be configured
(`AIRA_KIRA_SUNSET`, see [`CONFIGURATION.md`](CONFIGURATION.md)) so clients learn the ending from
the API rather than from an email. Its usage appears in reporting under the `kira` API name, which
is how you find out who has not migrated yet.

New work should use the Gemini surface: see [`MIGRATION-GEMINI.md`](MIGRATION-GEMINI.md).
