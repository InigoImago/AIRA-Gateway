# Pointing a Gemini-compatible client at AIRA

For anybody with a framework, SDK or tool that already speaks Google's Generative Language API and
wants it to go through AIRA instead of straight to Google.

Every example here was executed against a running stack on 2026-08-12. Where something is *not*
verified, it says so rather than implying it.

> The short version: the client changes its **base URL** and its **API key**, and nothing else.
> What has to happen first is administration — every request must belong to a **use case**, and
> somebody must have released the models it may call.

Related reading: [`SETUP.md`](SETUP.md) to get a stack running,
[`MIGRATION-KIRA.md`](MIGRATION-KIRA.md) for the predecessor's own wire contract,
[`REQUEST-LIFECYCLE.md`](REQUEST-LIFECYCLE.md) for what happens to a request on the way through.

---

## 1. The surface

Mounted at **`/v1beta`**, with Google's colon-verb convention:

| | |
|---|---|
| `POST /v1beta/models/{model}:generateContent` | one answer |
| `POST /v1beta/models/{model}:streamGenerateContent?alt=sse` | SSE, as the SDK expects |
| `POST /v1beta/models/{model}:embedContent` · `:batchEmbedContents` | vectors |
| `GET /v1beta/models` · `GET /v1beta/models/{model}` | the catalog |

The credential goes in `x-goog-api-key`, `?key=` or a bearer header — whichever your client already
uses. Errors keep Google's envelope, `{"error": {"code": …, "message": …, "status": …}}`, so a
client that parses `error.status` keeps working.

A model name may contain a colon (`qwen3:0.6b`); the verb is split at the **last** one, so
self-hosted names work unchanged.

---

## 2. Set-up, once per client

Identical to the KIRA path — [`MIGRATION-KIRA.md` §2](MIGRATION-KIRA.md#2-what-has-to-be-set-up-first)
has each call with its response. In short:

```bash
# 1. what the installation has approved at all
curl -s http://localhost:8002/api/v1/models/ -H "authorization: Bearer $TOKEN"

# 2. the use case
curl -X POST http://localhost:8002/api/v1/use-cases/ -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"slug":"assistant","name":"Coding assistant"}'

# 3. the models it may call — empty means none
curl -X PATCH http://localhost:8002/api/v1/use-cases/assistant/ -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"allowed_models":["qwen3:0.6b"]}'

# 4. who may use it — a person, or a Keycloak group
curl -X POST http://localhost:8002/api/v1/use-cases/assistant/members/ -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"username":"ucuser","role":"user"}'
curl -X POST http://localhost:8002/api/v1/use-cases/assistant/groups/ -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"group_path":"/aira/assistants","role":"user"}'

# 5. the key — shown once
curl -X POST http://localhost:8002/api/v1/use-cases/assistant/api-keys/ -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"label":"opencode","owner":"ucuser"}'
```

Two switches are **off by default** and matter to assistants — turn them on for this use case if
you need them:

```bash
curl -X PATCH http://localhost:8002/api/v1/use-cases/assistant/ -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"tools_enabled": true, "prompt_caching_enabled": true}'
```

`tools_enabled` carries function declarations through to the model (AIRA never executes one —
the call comes back to your client). Without it, `tools` is refused by name, which is what an
assistant hits first.

---

## 3. The use case — `X-AIRA-Use-Case`

An API key is issued **for one use case**, so a client using one normally sends nothing extra.
The header exists for an OIDC caller who belongs to several:

| the caller belongs to | what happens |
|---|---|
| **exactly one** use case | attributed to it automatically |
| **several** | `403` naming the candidates; send `X-AIRA-Use-Case: <slug>` |
| **none** | `403` — an unattributed request would bypass every budget and limit |

There is a second spelling for clients that cannot set a header: a path prefix,
`POST /uc/<slug>/v1beta/models/…`. Where both appear the header wins.

**Neither grants access.** Naming a use case you are not in is a 403, not a shortcut.

---

## 4. Verified clients

### `google-genai` (the official Python SDK)

Run against the gateway on 2026-08-12: generation, SSE streaming, embedding, the error envelope,
and turning thinking off — all unmodified.

```python
from google import genai
from google.genai import types

client = genai.Client(
    api_key="aira_a69e58c1_68d4…",
    http_options=types.HttpOptions(base_url="http://localhost:8001", api_version="v1beta"),
)

response = client.models.generate_content(
    model="qwen3:0.6b",
    contents="Say OK.",
    config=types.GenerateContentConfig(
        max_output_tokens=24,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    ),
)
print(response.text, response.usage_metadata.total_token_count)
```

Four cases from this SDK run in AIRA's own test suite, so the compatibility is asserted by the
client rather than by us.

### OpenCode (`@ai-sdk/google`)

A coding assistant, verified end to end including a real tool call. `make showcase` writes a ready
configuration and prints the command; the shape is:

```json
{
  "provider": {
    "aira": {
      "npm": "@ai-sdk/google",
      "name": "AIRA Gateway (Gemini surface)",
      "options": {
        "baseURL": "http://localhost:8001/v1beta",
        "apiKey": "{env:AIRA_API_KEY}"
      },
      "models": { "qwen2.5:3b": { "tool_call": true } }
    }
  }
}
```

### Anything else

Any client that lets you override the base URL should work — that is the whole claim of a
compatible surface. AIRA has not been run against LangChain, LlamaIndex or the Vertex SDK, and
this page will not pretend otherwise. If you try one, the fastest check is
`GET /v1beta/models` with your key: a `200` means the credential, the use case and the catalog are
all in order, and anything after that is the client's own configuration.

---

## 5. What AIRA refuses, and why

Google's API rejects unknown fields and so does this one. A field accepted and dropped answers with
a **200** and is wrong in a way nobody can see — measured once: of twelve fields a legitimate client
can send, eleven came back 200 and did nothing. `stopSequences` unbounded output, `seed` gave a
different answer every call, `safetySettings` applied a governance control nowhere.

So there are three answers, and never a silent one:

* **carried** — portable across the models AIRA serves;
* **refused by name, with the reason** — out of scope by design (`cachedContent` is provider-side
  state; `responseModalities` would return prose where audio was asked for);
* **the candidate is skipped** — the model or its dialect has no word for it, so a fallback chain
  moves on rather than answering with less than was asked for.

Two that surprise people:

* **`includeThoughts`** is refused. AIRA drops a model's reasoning and never stores it, so serving
  the request would answer with no thoughts and say so nowhere.
* **an incapable model is skipped, never sent a stripped request.** If a chain's next candidate
  cannot read the PDF you attached, it is passed over — and if none qualifies the request fails.
  A dropped attachment produces no error, it produces a confident wrong answer with a 200.

---

## 6. Checking it worked

* **Use case → Traces** — every request, which model, how it ended, what it cost, which functions
  it asked for
* **Use case → Consumption** — tokens and spend, whether or not a budget is set
* **Reporting** — across use cases, with a CSV export

A refused request is recorded too: the log says what was *asked*, not only what was served.
