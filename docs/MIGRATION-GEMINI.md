# Pointing a Gemini-compatible client at AIRA

For anybody with a framework, SDK or tool that already speaks Google's Generative Language API and
wants it to go through AIRA instead of straight to Google.

Responses are shown as the gateway words them, shortened with `…`. Where something is *not*
verified, it says so rather than implying it.

> The short version: the client changes its **base URL** — to one that names its use case,
> `https://<gateway>/uc/<use-case>/v1beta` — and its **API key**, and nothing else. What has to
> happen first is administration — every request must belong to a **use case**, and somebody must
> have released the models it may call.

Related reading: [`deployment/`](deployment/README.md) to get a stack running,
[`MIGRATION-KIRA.md`](MIGRATION-KIRA.md) for the predecessor's own wire contract,
[`REQUEST-LIFECYCLE.md`](REQUEST-LIFECYCLE.md) for what happens to a request on the way through.

---

## 1. The surface

Mounted at **`/v1beta`**, with Google's colon-verb convention — behind `/uc/<use-case>`, which a
model call carries whatever its credential (§3):

| | |
|---|---|
| `POST /v1beta/models/{model}:generateContent` | one answer |
| `POST /v1beta/models/{model}:streamGenerateContent?alt=sse` | SSE, as the SDK expects |
| `POST /v1beta/models/{model}:embedContent` · `:batchEmbedContents` | vectors |
| `GET /v1beta/models` · `GET /v1beta/models/{model}` | the catalog |
| `GET /v1beta/me/budget` | not Google's: what the calling credential is allowed, and what is left of it |
| `POST /v1/projects/{project}/locations/{location}/rankingConfigs/{config}:rank` | Google's ranking API: records ordered against a query and scored |

Any other verb — `:countTokens`, for one — is answered `400` *"Unknown method"*.

**Ranking** is served on Google's own path, so Google's ranking client is pointed at the gateway by
its endpoint, with an API key or token of the gateway as its credential — and names the use case
as any client does, with `/uc/<use-case>` in front of the path or the `X-AIRA-Use-Case` header
(§3). `model` is required and names a catalogue entry declared a reranker; the project, location
and configuration in the path select nothing — the gateway ranks in its own Google project, at the
locations the catalogue gives the model, for the use case the request belongs to. A ranking weighs one request per hundred records
against the use case's rate limit and budget.

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

## 3. The use case — in the base URL

**Every model call names its use case, whatever the credential** — an API key as much as a
Keycloak token. A key is issued for one use case and may name only that one; a person names one of
those their grants reach. Either puts the use case in front of the path, which is the only thing a
client that takes a base URL needs to change — or sends a header, where a client can set one and
not its path:

```
POST /uc/assistant/v1beta/models/…        base URL https://<gateway>/uc/assistant/v1beta
X-AIRA-Use-Case: assistant                with the base URL https://<gateway>/v1beta
```

The gateway strips the prefix before routing, so everything after it is the URL Google's clients
already build. Where both appear the header wins. A model call that names none is refused, and the
refusal says what to add — for a key, its own use case:

```
400  {"error": {"code": 400,
      "message": "Missing use case: every model call names one, with the /uc/<use-case> path prefix
                  or the X-AIRA-Use-Case header. This key belongs to 'assistant': put /uc/assistant
                  in front of the path.",
      "status": "INVALID_ARGUMENT"}}
```

The KIRA surface applies the same rule ([`MIGRATION-KIRA.md`
§3.3](MIGRATION-KIRA.md#33-the-use-case--in-the-base-url)). Reading the catalog (`GET`) or what
is left of a budget spends nothing and names no use case; a key's reading is its own use case's.

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
    # The SDK adds `/v1beta` itself, so its base is the use case's address without it.
    http_options=types.HttpOptions(
        base_url="http://localhost:8001/uc/assistant", api_version="v1beta"
    ),
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

### OpenCode, both generations

Two products under one name, and they send different requests: **OpenCode 1** (npm `opencode-ai`)
talks to Gemini through the AI SDK's Google provider above; **OpenCode 2** (npm `@opencode/cli`) is a
rewrite with a Gemini client of its own, which streams with `streamGenerateContent?alt=sse` and
declares every tool in JSON Schema (`parametersJsonSchema`). Both are run against the gateway in its
own test suite (`make test-clients`): a coding turn with a real tool call and its answer, and the
terminal interface with the budget plugin, read back from the screen. OpenCode 2 is run with its own
configuration and with the one the console hands out, which is OpenCode 1's shape and which it
still reads.

Two places write the configuration, with the key in it — so treat the file as the credential it is:

- the console's **API keys** tab, beside a key it has just issued: **Copy OpenCode config** and
  **Download opencode.json**, and **Download budget plugin**;
- `make showcase`, or `make showcase-agent` alone, which writes `opencode.showcase.json` for the
  showcase's `coding-assistant` use case.

Both list only the models released to the use case that declare tool calling. The shape, which both
generations read:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "aira": {
      "npm": "@ai-sdk/google",
      "options": {
        "baseURL": "http://localhost:8001/uc/assistant/v1beta",
        "apiKey": "aira_…"
      },
      "models": { "qwen3:0.6b": { "name": "qwen3:0.6b via AIRA …" } }
    }
  },
  "model": "aira/qwen3:0.6b"
}
```

OpenCode 2's own shape for the same provider, with the key taken from the environment or from its
credential store:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "providers": {
    "aira": {
      "name": "AIRA",
      "env": ["AIRA_API_KEY"],
      "package": "@opencode/ai/providers/google",
      "settings": { "baseURL": "https://<host>/gw/uc/<use-case>/v1beta" },
      "models": { "gemini-3.5-flash": { "capabilities": { "tools": true } } }
    }
  },
  "model": "aira/gemini-3.5-flash"
}
```

**The budget in the session.** The budget plugin adds an *AIRA budget* section to OpenCode's session
sidebar — the limit that will refuse first, as a bar and a sentence, then what you spent today, this
week and this month — and a `/budget` dialog with every allowance. One file serves both generations:
save it as `.opencode/plugins/aira-budget/tui.tsx` (or under `~/.config/opencode/plugins/` for
every project). OpenCode 2 finds it there; OpenCode 1, from 1.4, also needs `.opencode/tui.json`
with `{"plugin": ["./plugins/aira-budget/tui.tsx"]}`. It uses the key OpenCode already sends to the
gateway and stores none. For many workstations, every release from v0.1.7 on carries the file as
`opencode-aira-budget-<version>.tar.gz` with its checksum. Unpack it into `~/.config/opencode`; for
OpenCode 1, add the same entry to `~/.config/opencode/tui.json`. The archive brings no `tui.json`
of its own, so a developer's settings are never overwritten. [`tools/opencode/`](../tools/opencode/README.md) has the plugin and the
hand-editable configuration the first measurement was made with.

Three things that make OpenCode fail against a Gemini endpoint and are not the gateway's:

- **No OpenAI options on this provider.** `extraBody`, `store`, `drop_params` and similar are
  OpenAI-provider settings; written into a Gemini request body, a field Google's API does not have
  is refused — here as at Google.
- **The key outside the file**: OpenCode 1 keeps it with `opencode auth login --provider aira`
  (`~/.local/share/opencode/auth.json`), OpenCode 2 with `opencode auth login aira`, which asks for
  it. Then `apiKey` can be left out of the configuration. Do not pass the key on OpenCode 2's command
  line (`--answer`): it writes its command line into its log.
- **The use case in the base URL**, which every model call needs: `baseURL`
  `https://<host>/gw/uc/<use-case>/v1beta` through the console's proxy, or
  `https://<gateway>/uc/<use-case>/v1beta` directly — the console's file carries it already. A
  configuration rolled out before every call had to name its use case lacks it, and every turn is
  answered `400` until it is added.

### Anything else

Any client that lets you override the base URL should work — that is the whole claim of a
compatible surface — given a base URL that names its use case, `…/uc/<use-case>/v1beta`. AIRA has
not been run against LangChain, LlamaIndex or the Vertex SDK, and this page will not pretend
otherwise. If you try one, the fastest check is `GET /v1beta/models` with your key: a `200` means
the credential is accepted, and lists what this gateway serves. It does not check the use case — a
model call does: a `400` *"Missing use case"* means the base URL lacks `/uc/<use-case>`, and a `403`
saying the use case is *not known to this gateway* means its configuration has not arrived yet.
Anything after that is the client's own configuration.

---

## 5. What AIRA carries, refuses, and why

**Both of Google's ways to write a schema are carried.** The OpenAPI subset (`parameters`,
`responseSchema`) is parsed into AIRA's own vocabulary and refused outside it; JSON Schema
(`parametersJsonSchema`, `responseJsonSchema`) is carried **as written** to whichever model answers
— Gemini, Claude and OpenAI-dialect servers all take JSON Schema natively — and a schema a provider
cannot take comes back as its 400, with its reason. Both are bounded alike: 32 KiB, eight levels,
256 properties. `thinkingLevel` (`low`, `high`, …) is the model's own level word and must be one the
catalogue declares for the model. `tools` may be a list or a single tool object.

**How much one request may hold.** At most 128 function declarations across its tools — Google's
own maximum for a request to Gemini — 10 000 turns in `contents` and 20 000 parts across them, and a
body of at most 250 000 JSON values nesting at most 64 levels — the last two counted before the body
is decoded, the others before the request is validated — each refused with a `400` naming it: what
a body costs is its structure, and every bit of it is paid before a rate limit can refuse anything.
An inline attachment or a long text is one value, however long, so the bound is never about size —
a large tool result that would break it travels as text.

**Functions, as current clients write them.**

- A parameter may be a union without a `type` (`{"anyOf": [{"type": "string"}, {"type": "null"}]}`)
  and may be `type: null`: that is how JSON Schema generators write an optional argument.
- `toolConfig` takes Google's four modes — `AUTO`, `ANY` (a call is required), `NONE` (no call) and
  `VALIDATED` — and `allowedFunctionNames` with `ANY` or `VALIDATED`. A model whose provider cannot
  say the mode is skipped by name. Claude and the OpenAI dialect have no `VALIDATED`, and Claude
  refuses `ANY` with a thinking budget.
- A function's result may carry media in `functionResponse.parts` (an image a tool looked at, a PDF
  it read). It is checked like any attachment and goes to a model that reads it there: Gemini, or
  Claude. The OpenAI dialect's tool messages take text only, so such a model is skipped.
- A call and its result are paired by `id` where both carry one; without an id they are paired by
  name, as Google pairs them.
- Function calling is the use case's to switch on, and the switch covers the conversation as well
  as the declarations: where it is off, a request carrying a `functionCall` or a `functionResponse`
  is refused `400 FAILED_PRECONDITION` even when it declares nothing. A function's result reaches
  the model unread by any checking step, so a use case that has not enabled functions receives
  none.
- `usageMetadata.cachedContentTokenCount` reports what a provider's prompt cache served.

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
  * a `thinkingConfig` at the top level rather than inside `generationConfig`;
* **the candidate is skipped** — the model or its dialect has no word for it, so a fallback chain
  moves on rather than answering with less than was asked for.

Three that surprise people:

* **`includeThoughts`** follows the use case. A thinking model's reasoning comes back by default,
  buffered or streamed, as `thought: true` parts before the answer, and is stored with it; `false`
  withholds it. `true` where the use case has turned reasoning off is refused by name, rather than
  answered without the thoughts.
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
