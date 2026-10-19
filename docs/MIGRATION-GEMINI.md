# Pointing a Gemini-compatible client at AIRA

For anybody with a framework, SDK or tool that already speaks Google's Generative Language API and
wants it to go through AIRA instead of straight to Google.

Responses are shown as the gateway words them, shortened with `…`. Where something is *not*
verified, it says so rather than implying it.

> The short version: the client changes its **base URL** and its **API key**, and nothing else.
> What has to happen first is administration — every request must belong to a **use case**, and
> somebody must have released the models it may call.

Related reading: [`deployment/`](deployment/README.md) to get a stack running,
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
| `GET /v1beta/me/budget` | not Google's: what the calling credential is allowed, and what is left of it |

Any other verb — `:countTokens`, for one — is answered `400` *"Unknown method"*.

The credential goes in `x-goog-api-key`, `?key=` or a bearer header — whichever your client already
uses. Errors keep Google's envelope, `{"error": {"code": …, "message": …, "status": …}}`, so a
client that parses `error.status` keeps working.

A model name may contain a colon (`qwen3:0.6b`); the verb is split at the **last** one, so
self-hosted names work unchanged.

---

## 2. Set-up, once per client

Identical to the KIRA path — [`MIGRATION-KIRA.md` §2](MIGRATION-KIRA.md#2-what-has-to-be-set-up-first)
has each call with its response, and how a script gets `$TOKEN`. In short:

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

The key expires after 30 days unless `expires_in_days` asks otherwise, and after 180 at most. The
use case and the key reach the gateway on the outbox relay's next run; until the use case has
arrived, a model call is answered `403` *"Use case 'assistant' is not known to this gateway"*.

Two switches are **off by default** and matter to assistants — turn them on for this use case if
you need them:

```bash
curl -X PATCH http://localhost:8002/api/v1/use-cases/assistant/ -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"tools_enabled": true, "prompt_caching_enabled": true}'
```

`tools_enabled` carries function declarations through to the model (AIRA never executes one —
the call comes back to your client). Without it, `tools` is refused by name, which is what an
assistant hits first. The model must also declare tool calling in the catalogue; one that does not
is skipped by a fallback chain.

---

## 3. The use case — `X-AIRA-Use-Case`

An API key is issued **for one use case**, so a client using one sends nothing extra.

An OIDC bearer token carries no use case, so on this surface a model call made with one **always
names it** — with the header, or with a path prefix for clients that cannot set a header:

```
X-AIRA-Use-Case: assistant
POST /uc/assistant/v1beta/models/…
```

Where both appear the header wins. A model call that names none is refused, however many use cases
the caller belongs to:

```
400  {"error": {"code": 400,
      "message": "Missing use case (X-AIRA-Use-Case header or /uc/<use-case> path).",
      "status": "INVALID_ARGUMENT"}}
```

This differs from the KIRA surface, which attributes a caller who belongs to exactly one use case
to it ([`MIGRATION-KIRA.md` §3.3](MIGRATION-KIRA.md#33-the-use-case--x-aira-use-case)). Reading the
catalog (`GET`) spends nothing and needs no use case.

**Neither spelling grants access.** Naming a use case you are not in is a 403, not a shortcut, and
so is a key naming a use case other than its own.

---

## 4. Verified clients

### `google-genai` (the official Python SDK)

Generation, SSE streaming, embedding, the error envelope, an attached image, and turning thinking
off — all unmodified; and the JSON Schema fields (`parameters_json_schema`, `response_json_schema`)
and `thinking_level`. `gateway/tests/test_google_sdk_speaks_to_us.py` runs ten cases from this SDK
against the gateway in AIRA's own test suite, so the compatibility is asserted by the client rather
than by us.

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

### The AI SDK's Google provider (`@ai-sdk/google`), every major

What most TypeScript agents — OpenCode among them — are built on, and it has changed what it sends:
its first major declares tools in Google's OpenAPI subset and sends a single tool as an object
rather than a list; its fourth declares every tool in JSON Schema (`parametersJsonSchema`), asks for
structured output in JSON Schema (`responseJsonSchema`) and sets Gemini 3's `thinkingLevel`. All
four majors — with the `ai` release each ships for — run a two-step tool call, a structured answer
and a stream against the gateway in its own test suite (`tools/clients`, `make test-clients`), and
the test records which form each one sent.

### OpenCode (`@ai-sdk/google`)

A coding assistant, verified end to end including a real tool call — the OpenCode binary itself,
in its current release and in the last 1.0 release, both reading files through tools against the
gateway. Two places write its
configuration, with the key in it — so treat the file as the credential it is:

- the console's **API keys** tab, beside a key it has just issued: **Copy OpenCode config** and
  **Download opencode.json**, and **Download budget plugin**, which shows what is left of the
  allowance inside the session (it reads `GET /v1beta/me/budget`);
- `make showcase`, or `make showcase-agent` alone, which writes `opencode.showcase.json` for the
  showcase's `coding-assistant` use case.

Both list only the models released to the use case that declare tool calling. The shape:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "aira": {
      "npm": "@ai-sdk/google",
      "options": {
        "baseURL": "http://localhost:8001/v1beta",
        "apiKey": "aira_…"
      },
      "models": { "qwen3:0.6b": { "name": "qwen3:0.6b via AIRA …" } }
    }
  },
  "model": "aira/qwen3:0.6b"
}
```

[`tools/opencode/`](../tools/opencode/README.md) has the budget plugin, a `/budget` command, and the
hand-editable configuration the measurement was made with.

Three things that make OpenCode fail against a Gemini endpoint and are not the gateway's:

- **No OpenAI options on this provider.** `extraBody`, `store`, `drop_params` and similar are
  OpenAI-provider settings; `@ai-sdk/google` writes them into the request body, and a field Google's
  API does not have is refused — here as at Google.
- **The key outside the file**: `opencode auth login --provider aira` keeps it in OpenCode's own
  credential store (`~/.local/share/opencode/auth.json`), and `apiKey` can be left out of
  `opencode.json`.
- **A path prefix instead of the header**, where a client cannot set one: `baseURL`
  `https://<host>/gw/uc/<use-case>/v1beta` through the console's proxy, or
  `https://<gateway>/uc/<use-case>/v1beta` directly.

### Anything else

Any client that lets you override the base URL should work — that is the whole claim of a
compatible surface. AIRA has not been run against LangChain, LlamaIndex or the Vertex SDK, and
this page will not pretend otherwise. If you try one, the fastest check is `GET /v1beta/models`
with your key: a `200` means the credential is accepted, and lists what this gateway serves. It
does not check the use case — a model call does, and a `403` there saying the use case is *not
known to this gateway* means its configuration has not arrived yet. Anything after that is the
client's own configuration.

---

## 5. What AIRA carries, refuses, and why

**Both of Google's ways to write a schema are carried.** The OpenAPI subset (`parameters`,
`responseSchema`) is parsed into AIRA's own vocabulary and refused outside it; JSON Schema
(`parametersJsonSchema`, `responseJsonSchema`) is carried **as written** to whichever model answers
— Gemini, Claude and OpenAI-dialect servers all take JSON Schema natively — and a schema a provider
cannot take comes back as its 400, with its reason. Both are bounded alike: 32 KiB, eight levels,
256 properties. `thinkingLevel` (`low`, `high`, …) is the model's own level word and must be one the
catalogue declares for the model. `tools` may be a list or a single tool object.

Google's API rejects unknown fields and so does this one. A field accepted and dropped answers with
a **200** and is wrong in a way nobody can see: a dropped `stopSequences` leaves the output
unbounded, a dropped `seed` gives a different answer every call, and a dropped `safetySettings`
applies a governance control nowhere.

So there are three answers, and never a silent one:

* **carried** — portable across the models AIRA serves;
* **refused by name, with the reason** — out of scope by design:
  * `cachedContent` — provider-side state;
  * `safetySettings` — a safety threshold that holds on one vendor and silently not on its fallback;
  * parts as `fileData`, `executableCode` or `codeExecutionResult` — attachments go inline, and code
    execution is not offered;
  * `responseLogprobs`, `logprobs`, `mediaResolution`, `enableEnhancedCivicAnswers`;
  * a `candidateCount` other than 1;
  * a `responseMimeType` other than `text/plain`, or `application/json` without a `responseSchema`
    or `responseJsonSchema`;
  * a declaration made twice — a function with both `parameters` and `parametersJsonSchema`, an
    answer with both `responseSchema` and `responseJsonSchema`, a `thinkingLevel` beside a
    `thinkingBudget` — which Google refuses as well;
  * a `responseModalities` other than text, or speech alone — it would return prose where images
    were asked for;
  * a `toolConfig` mode other than `AUTO`;
  * a `thinkingConfig` at the top level rather than inside `generationConfig`;
* **the candidate is skipped** — the model or its dialect has no word for it, so a fallback chain
  moves on rather than answering with less than was asked for.

Three that surprise people:

* **`includeThoughts`** follows the use case. On `:generateContent`, a thinking model's reasoning
  comes back by default and is stored with the answer; `false` withholds it. A stream carries no
  reasoning, so `true` on `:streamGenerateContent` is refused by name — and so is `true` where the
  use case has turned reasoning off, rather than answering without the thoughts.
* **speech is carried.** `responseModalities: ["AUDIO"]` with a `speechConfig` returns one
  `inlineData` part of audio from a model the catalogue declares as speaking, buffered or streamed.
  The audit trail keeps the audio's description and hash, not the audio.
* **an incapable model is skipped, never sent a stripped request.** If a chain's next candidate
  cannot read the PDF you attached, it is passed over — and if none qualifies the request fails.
  A dropped attachment produces no error, it produces a confident wrong answer with a 200.

**Attachments** go inline, as `inlineData` with a `mimeType` and base64 `data`. Fifteen media types
are accepted — `application/pdf`, `application/x-javascript`, `text/javascript`, `text/plain`,
`text/html`, `text/md`, `text/csv`, `text/xml`, `text/rtf`, `image/png`, `image/jpg`, `image/jpeg`,
`image/webp`, `image/heic`, `image/heif` — at most 16 parts and 6 MiB of them in all. A PDF or an
image must carry its format's signature bytes; the text types have none to check. A model reads only
the types its catalogue entry declares.

**When something did not work**, AIRA says what in Google's own terms and in headers, so no SDK
parses anything new:

* a refusal's `error.details` holds one `google.rpc.ErrorInfo` whose `reason` names the control —
  `BLOCKED_BY_PIPELINE` with `metadata.step`, `BUDGET_EXCEEDED` with `metadata.resets_at`,
  `RATE_LIMITED`, `NO_CAPABLE_MODEL`, `UPSTREAM_ERROR`, … — and `metadata.request_id`. The
  `google-genai` SDK keeps `details` on its `APIError` and prints it, so the reason and the id are in
  the line a developer reads;
* every response carries `X-AIRA-Request-Id`; an answer carries `X-AIRA-Model`, the model that
  actually answered, and `X-AIRA-Pipeline`, what the use case's pipeline did (`pii_filter=redacted`,
  `model_route=a>b`, …); a refusal carries `X-AIRA-Reason`.

With strict data mode on — the default — the use case's own people cannot read prompts and answers.
Paste the request id into **Use case → Requests → Request id**: the request's details say, step by
step, what the pipeline decided, which models were tried and why each did not answer, and the
message the caller was given.

---

## 6. Checking it worked

* **Use case → Requests** — every request, which model, how it ended, what it cost, which functions
  it asked for
* **Use case → Overview**, the **Consumption** card — tokens and spend, whether or not a budget is
  set
* **Reporting** — across use cases, with a CSV export

A request refused once it is attributed to the use case is recorded too: the log says what was
*asked*, not only what was served. One refused before that — a rejected key, a use case the caller
is not in or did not name — leaves no audit row; its trace span is its record.
