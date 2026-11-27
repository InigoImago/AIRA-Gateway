# FRD-155 — Every Gemini client, in every language

> Phase: 8 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner — *"kann das System alle möglichen Frameworks für Gemini mit Java, Python, C#
> und weiteren Sprachen als Client, einfache Anfrage, Key und co abdecken … unterschiedliche
> Sprachen und Auth-Methoden auch für LLM aber auch für Embeddings … TTS mit Gemini … Reranking
> Modelle auch"*. Answered by measurement: the clients were run, not read.
>
> Related: `FRD-100` (the surface), `FRD-124` (nothing silently dropped), `FRD-143` (current and
> older clients), `FRD-113` (embedding options), `FRD-147` (ranking), `FRD-624` (speech),
> `FRD-135` (reasoning, §5.7), `FRD-151` (reasoning in OpenCode), `FRD-128` (counting what a
> vendor did not report), `FRD-508` (the model reference), `ADR-0013` (model access, not agents).

## 1. Summary

The Gemini surface promised that a client pointed at it needs its base URL and key changed and
nothing else. Thirty-one clients in seven languages — Google's SDKs in each language, current and
deprecated, and the frameworks built on them — were run against a gateway of their own, in every
credential and use-case variant, then against real Gemini. **Before this FRD, 405 of 573 cases
passed; after it, 508** — besides 69 calls with another use case's key, refused in every client as
they should be — and every case still failing fails for a reason outside the gateway or for a
decision only the owner can take (§11).

None of the clients that failed sent anything Google refuses. Google's API is protobuf behind
proto3's JSON mapping, which allows one request to be written several ways, and the surface read
only one of them. It now reads them all (FR-1 – FR-5), serves the API's stable version as well as
its beta (FR-6), and carries the three fields current clients send that it did not model (FR-7 –
FR-9).

The owner then took the decisions of §11 (2026-11-27): the reasoning comes back only when the
request asks for it, as Google answers (FR-10); safety settings are carried to Gemini (FR-11);
`:countTokens` is served with the vendor's own count (FR-12); the model reference proposes what the
vendor documents for embeddings and speech (FR-13); Vertex AI's paths stay unserved. And the
repository gained an example per framework, with each credential (FR-14).

## 2. Goals & Non-Goals

**Goals**
- Every spelling Google's own parser reads is read here, as the same request.
- Every refusal by design holds in every spelling, and only where the request asks for something.
- Every bound counted on the raw body counts every spelling.
- The fields current clients send — a function's result, an embedding's title, a ranking's
  repeated configuration — are carried or refused by name, never dropped.

**Non-Goals**
- Vertex AI's paths, a second API surface (`ADR-0013`'s scope, the owner's decision — §11).
- The Live API, the Interactions API, files, caches, Google's own tools (search, code execution).
- A token count this gateway works out itself (FR-12 refuses rather than estimates).

## 3. Measured

A throwaway gateway — the real application, in-memory database, authentication on, every call
naming its use case, an OIDC issuer of its own whose tokens look like Keycloak's, API keys minted as
Management mints them — served the mock under realistic model names, and for the live round the
installation's Vertex models (`gemini-2.5-flash`, `gemini-embedding-001`, `gemini-2.5-flash-tts`,
`semantic-ranker-default-004`). Every request was recorded: path, which credential carrier, which
use-case carrier, the client's own `x-goog-api-client`, the body's field names, the answer.

| Language | Client (version) | Before | Why |
| --- | --- | --- | --- |
| Python | `google-genai` 2.34 | tools | `response_json_schema` in a declaration (FR-7) |
| Python | `google-generativeai` 0.8.6 (deprecated) | tools, schema, task types | enums by number (FR-2) |
| Python | `langchain-google-genai` 4.4 | every chat call | `safetySettings: []` (FR-3) |
| Python | LiteLLM 1.80 | tools, schema | snake_case names (FR-1) |
| Python | LlamaIndex, PydanticAI, Haystack, Google ADK, `requests` | — | — |
| Python | `google-cloud-discoveryengine` 0.20 (ranking) | — | — |
| Java | `com.google.genai` 2.6 / 1.0 | automatic function calls (2.6), titles | `response` (FR-7), `title` (FR-8) |
| Java | LangChain4j 1.24, Spring AI 2.1 | — | — |
| Java | `google-cloud-discoveryengine` 0.99 (ranking) | — | — |
| Go | `google.golang.org/genai` 1.79 | every call, unless told `v1beta` | `/v1` is its default (FR-6) |
| Go | `generative-ai-go` 0.20 (deprecated) | every chat call, task types | `model` in the body (FR-5), enums |
| Go | LangChainGo 0.1.15 | every chat call | safety thresholds (§11), `model` in the body |
| Go | `cloud.google.com/go/discoveryengine` 1.38 | every ranking | `rankingConfig` in the body (FR-9) |
| JavaScript | `@google/genai` 3.6 | every call, unless told `v1beta` | `/v1` is its default (FR-6) |
| JavaScript | `@google/generative-ai` 0.24 (deprecated) | every chat call | `safetySettings: []` |
| JavaScript | `@langchain/google-genai` 2.3 | every chat call | the same, underneath |
| JavaScript | Genkit 1.44, AI SDK 7 / `@ai-sdk/google` 4, Gemini CLI 0.71 | — | — |
| JavaScript | `@google-cloud/discoveryengine` 3.5 (ranking) | every ranking | `userLabels: {}` (FR-9) |
| PHP | `google-gemini-php/client` 2.8 | every call | `safetySettings: []`, `title: null` |
| Ruby | `gemini-ai` 4.3 | every chat call | `contents` and `parts` as objects (FR-4) |
| Ruby | `ruby_llm` 2.2 | — | — |
| Rust | `gemini-rust` 2.1, `genai` 0.7 | — | — |

**Credentials.** Every client that reached the gateway at all was served with each of: the API
key in its own setting (`x-goog-api-key` or `?key=`, as the client chose); a person's access token
in the same setting; the token as `Authorization: Bearer`; a client-credentials (service-account)
token as bearer; and the use case by path and by header — where the client can send a header. A key
of another use case was refused `403` with the reason, in every client's own error type. A client
without a header setting (LangChain4j's embedding model, the Gemini CLI, the Node ranking client's
path) names its use case in the path and carries a token where its key goes.

**Live**, against Vertex in `europe-west1`: generation, streaming, chat, structured output, tools
and embeddings (`gemini-embedding-001` through `:predict`, 768 dimensions asked and answered)
from every client above; speech from Google's SDK in Python, Java, Go and JavaScript and from
LangChain and raw HTTP — 24 kHz PCM, 1.1–1.5 s for one sentence, 37 audio tokens, streamed as up to
79 chunks. Ranking answered `401 CREDENTIALS_MISSING` from Google for every client: the ranking
API takes no API key (`FRD-147` §5.2), and the installation's only Google credential here is one.

## 4. Functional Requirements

- **FR-1 — Proto names.** Every field of every request model is read by its camelCase name and by
  its proto name in snake_case. A refusal names the field in camelCase.
- **FR-2 — Enums by number.** `type` in a schema, `taskType`, `functionCallingConfig.mode`,
  `responseModalities` and a declaration's `behavior` are read by name or by Google's number;
  `0`/`…_UNSPECIFIED` is no value.
- **FR-3 — Defaults are not set.** `null`, `[]`, `""`, `false`, `0` and `…_UNSPECIFIED` are a
  field not sent: a field refused by design is refused only where it asks for something, and one
  that asks for nothing is accepted. `safetySettings` whose every entry names no threshold asks for
  nothing. An object is never "not set" — `"codeExecution": {}` switches code execution on, and is
  refused. `candidateCount: 0` is one candidate, `responseMimeType: ""` text, `role: ""` the user.
- **FR-4 — One object is a list of one**, for `contents`, `parts`, `tools`, `functionDeclarations`,
  a function result's `parts` and a batch's `requests`; `null` there is an empty list.
- **FR-5 — The model in the body.** `model` in a generation body is accepted where it names the
  model of the URL, with or without `models/`, and refused `400` where it names another.
- **FR-6 — `/v1`.** `GET /v1/models`, `GET /v1/models/{model}` and `POST /v1/models/{model}:{verb}`
  are the `/v1beta` routes. A wrong URL under `/v1`, `/v1beta` or `/v1beta1` is answered in
  Google's envelope, and one in Vertex AI's shape (`…/publishers/…/models/…`) says which API this is
  and how to configure the client.
- **FR-7 — A function's result.** `response` (Google's OpenAPI subset) and `responseJsonSchema`
  (JSON Schema) are parsed and bounded like the parameters, at most one of the two, and carried to
  Gemini in the field they were written in. A Claude or OpenAI-dialect candidate is passed over by
  name: neither has a word for it. `behavior` is accepted as `BLOCKING` or unset; `NON_BLOCKING`
  (the Live API's) is refused by name. Google's own tools (`googleSearch`, `codeExecution`,
  `urlContext`, …) are refused by name.
- **FR-8 — An embedding's title**, per entry, is read through the use case's pipeline after the
  texts, weighs one more text at the rate limit where the pipeline reads each text, counts towards
  the characters one request may carry, and is carried to the Gemini API (`title`), Vertex's
  `:predict` (the instance's `title`) and `:embedContent` (the config's `title`). An
  OpenAI-dialect candidate is passed over by name.
- **FR-9 — Ranking.** `rankingConfig` in the body is accepted and selects nothing, as the path's
  does not; `userLabels` is accepted empty and refused by name otherwise; `topN: 0` is every record,
  Google's meaning; a negative one is refused.
- **FR-10 — The reasoning when asked for** (`FRD-135` §5.7). A thinking model's reasoning comes
  back where the request sends `includeThoughts: true` and the use case returns reasoning — not
  otherwise, as Google answers. `true` where the use case does not is refused by name, as before.
  The OpenCode file the console hands out asks for it, on the model and in each variant that
  thinks, wherever the use case returns reasoning (`FRD-151`). A Claude model's thinking block goes
  back inside a call's signature only where the reasoning was returned: the envelope is readable,
  so a Claude model of the budget generation (4.5 and earlier) continues a function call with the
  caller's own budget only for a caller who asks — and is told so.
- **FR-11 — Safety settings.** `safetySettings` — Google's five Gemini categories and five
  thresholds, by name in either case or by number, in either spelling — are carried to Gemini on
  AI Studio and Vertex AI as sent. An entry naming no threshold, or `…_UNSPECIFIED`, asks for the
  model's default and is left out. A category Gemini does not filter (the PaLM models', Vertex AI's
  own) and a threshold Google does not name are refused, naming what is accepted. A Claude or
  OpenAI-dialect candidate is passed over by name, as for every Gemini-only field; a model with
  nothing behind it refuses the request naming `safetySettings`.
- **FR-12 — `:countTokens`.** `POST /v1beta/models/{model}:countTokens` (and `/v1`, under a use case
  like every verb) with `contents` or a `generateContentRequest` — one of the two — answers
  `{"totalTokens": n}`: the vendor's own count of what a generation of the same request is billed
  for as input, at the endpoint the vendor does not charge for (Vertex AI and AI Studio). It passes
  the controls a generation passes before anything leaves — the use case, the rate limit at one
  request, the pipeline (a redactor sees the text first), the model's approval, release and region —
  and leaves a row, operation `countTokens`, that books no token and no request against a budget.
  Nothing is reserved for it. What is counted is the input — the turns, the system instruction, the
  functions; what would shape an answer (its cap, sampling, thinking, schema, safety) is read and
  validated as a generation's, and not held against a model that produces no answer. Only the model routing chose is counted: no fallback answers, since
  another tokenizer's figure answers another question. A model whose vendor counts nothing for this
  gateway (Claude, the OpenAI dialect) is refused by name, never estimated. A model's
  `supportedGenerationMethods` lists `countTokens` where its adapter counts.
- **FR-13 — The model reference corrects the list.** A draft of `gemini-embedding-001` declares
  Google's eight task types, batches and the widths Google recommends (768, 1536, 3072); one of
  `gemini-embedding-2` batches and no task type, since Google documents its task written into the
  text and a task type sent to it would change nothing under a 200. Google's speech models on Vertex
  AI are drafted as taking a temperature, which the list says they do not and Google answered with
  speech. For a model already on file the editor offers each — the task types where none are
  declared, the sampling where the form fixes it — and changes nothing until somebody clicks.
- **FR-14 — An example per framework.** `examples/` holds one short program per language and
  framework — Google's SDKs in Python, Java, JavaScript and Go, LangChain (Python and JavaScript),
  LlamaIndex, LangChain4j, Spring AI, the AI SDK, the PHP client, RubyLLM, `gemini-rust`, plain HTTP
  in Python and C#, and curl — each with every credential its client can carry, and a README that
  shows how address, use case, credential and model fit together, what each client must be told,
  and which were left out and why. Every one ran in every form against a gateway with
  authentication on; the Python ones on Google's SDK and on plain HTTP run so in the test suite.
  They are left out of the release's source SBOM, as the other test tooling is.

## 5. Design

- `api/gemini/schemas.py`: one `ConfigDict` for every request model (`alias_generator=to_snake`,
  `populate_by_name`, `loc_by_alias=False`); `unset`, `enum_name`, `one_is_a_list`, `spellings` at
  the top; `_refuse` reads both spellings and returns the body without what asks for nothing.
  `_counted_before_validated` counts both spellings, one object as a list of one.
- `core/schema.py`: `TYPES_BY_NUMBER`.
- `core/canonical.py`: `ToolDeclaration.response`, `.response_json_schema`, `declares_results`;
  `CanonicalEmbeddingRequest.titles`, `.titled`, `.title_of`.
- Dialects: `gemini_mapping` (`_declared_result`, the title in both embedding bodies),
  `vertex/embedding_mapping` (`predict_body`, `embed_content_body`), and the refusals in the OpenAI
  and Anthropic mappers, raised as `DialectUnsupported` — the chain passes over such a candidate
  already (`pipeline/dispatch.py`).
- `api/gemini/routes.py`: the `/v1` routes on the same handlers; `_the_url_names_the_model`.
- `exception_handlers.py`: `GOOGLE_PATH`, `VERTEX_MODEL_PATH`, `NOT_VERTEX`.
- `api/ranking/schemas.py`: `rankingConfig`, `userLabels`, `topN`.
- Reasoning (FR-10): `api/serving/controls.resolve_reasoning` returns it on an explicit yes only;
  `management/frontend/…/opencode-config.ts` writes `includeThoughts` where the use case's
  `include_reasoning` is on (`api-keys-panel`'s `reasoning` input).
- Safety (FR-11): `api/gemini/schemas.SafetySetting`, `settings_that_ask`; `core/canonical`
  `SafetySetting`, `CanonicalRequest.safety_settings`, `sets_harm_filters`; carried in
  `gemini_mapping.canonical_to_gemini_request`, refused in the OpenAI and Anthropic mappers.
- Count (FR-12): `schemas.CountTokensRequest`/`CountTokensResponse`; the route's `countTokens`
  branch; `prepare_for_dispatch` reserves nothing for `COUNT_METHOD`; `prepare.count` walks
  `answer_with_fallback` with no fallbacks and the generation's conditions; `Accounting.counted`;
  `count_tokens` on the AI Studio adapter and the mock (Vertex AI had one); the model-call purpose
  `count`. The AI Studio adapter's count also serves `FRD-128` FR-6, so a stream abandoned there is
  counted rather than left unknown.
- Reference (FR-13): `apps/catalog/reference/embedding_defaults.py`, `normalize.SAMPLES_DESPITE_THE_LIST`,
  the shipped `models.json`; the editor's `proposedEmbedding` and `proposedFreeSampling`.

## 6. Data Model

None.

## 7. API / Interface Contract

`docs/API.md` §2.1 and `docs/MIGRATION-GEMINI.md` §1, §4, §5.

## 8. Security & Privacy

- **The bounds hold in either spelling.** The counts made before validation — turns, parts,
  functions — read both spellings and count one object as the list of one validation reads it as;
  `test_declarations_in_snake_case_are_counted_before_validation` and its siblings pin it.
- **Every refusal by design holds**, in either spelling and for an empty object; only a request
  that asks for nothing passes where it was refused before.
- **A title is the caller's text.** It is read by the use case's pipeline — a redactor sees it, and
  the stored body is rewritten where it was — and it reaches the same recipient as the text it
  titles. The privacy notice already names "the content of the request" going to the provider, so
  the register and its edition are unchanged.
- **No credential handling changed.** What was measured is that every carrier the gateway reads
  works in every client that can send it.
- **Less reasoning is processed** (FR-10): it reaches a caller, and the stored payload, only where
  the request asked for it. The privacy notice said "where the use case asks for it"; it now says
  where the request asks for it and the use case allows it — a new edition (2026-11-27).
- **A count sends the whole request to the vendor** (FR-12) — which is why it passes the pipeline
  first and leaves a row. The notice's "the content of the request" going to the provider covers it.
- **A caller sets Google's filters** (FR-11), lower or higher than a model's default, as Google's own
  API lets it. An installation that must hold a floor has no control for that yet.

## 9. Observability

None new. A refusal under the new rules is recorded as every refusal is.

## 10. Testing

- `gateway/tests/test_the_gemini_surface_reads_what_google_reads.py`: each spelling, each default,
  each refusal by design in both spellings, the bounds, `/v1`, the Vertex hint, the ranking fields,
  the result schemas per dialect, the titles through every body, the redactor and the weight.
- `test_no_silent_drop.py` and `test_rerank.py` changed where a case asked for nothing (a safety
  setting without a threshold, `topN: 0`) and now name one that does.
- Mutations GR-1 – GR-41, GR-10 folded into MG-1.
- The client matrix itself (§3) is a harness outside the repository, kept with the round's report.
- FR-10: `test_the_callers_word_on_reasoning.py`, `test_a_model_that_thinks_unasked.py`, the
  OpenCode runs in `test_real_gemini_clients.py` (both generations show the reasoning from the
  console's new file), `opencode-config.spec.ts`, `api-keys-panel.spec.ts`,
  `use-case-detail.spec.ts`, `test_claudes_thinking_travels_with_the_turn.py`.
- FR-11: the harm-filter section of `test_the_gemini_surface_reads_what_google_reads.py` (a chain
  passing a candidate over by name, among them), `test_no_silent_drop.py`, and three cases of the
  live-stack suite that refused the field and now refuse it for the dialect.
- FR-12: `test_a_count_is_the_vendors_and_costs_nothing.py`.
- FR-13: `test_model_reference.py`, `model-catalog.spec.ts`.
- FR-14: `test_the_python_examples_run.py` — the five Python examples on Google's SDK and on plain
  HTTP, in each of their credential forms, against the gateway on a socket with authentication on,
  an OIDC issuer of the test's own and its client-credentials grant.
- Mutations GR-42 – GR-71 and EXM-1 – EXM-3; GR-7, RS2, TO-4 and BOM12 re-anchored, M23 widened,
  GR-3 given the unit test of `unset` it had lost.

## 11. Decisions

Put to the owner with the measurement, and decided on 2026-11-27.

1. **The reasoning came back unasked, and many clients showed it as the answer** — decided: only
   when asked (FR-10). Since `FRD-135` §5.6 the gateway asked a thinking model for its reasoning
   whenever the use case returned it, where Google returns it only when the request asks
   (`includeThoughts: true`). Live, a client that does not know `thought: true` parts put the
   thought summary into its answer: the deprecated SDKs in Python, JavaScript and Go, LangChain4j,
   `gemini-rust`, LangChain (Python) — whose answer became a list of blocks rather than a string —
   and every hand-written integration that reads `parts[0].text`, which is the thought. The
   deprecated JavaScript SDK's structured answer (thoughts, then the JSON) no longer parsed; the PHP
   client refused `text()` on a two-part answer. With the use case's reasoning switched off, all of
   them answered correctly.
2. **Safety settings with a threshold** were refused (`FRD-124`); LangChainGo sends one per category
   on every call — decided: carried to Gemini, the other dialects passed over by name (FR-11).
3. **Vertex AI's paths** — decided: not served. A client in Vertex mode — `google-genai` with
   `vertexai=True`, Spring AI's Vertex module, LangChain4j's, the Vertex SDK — is told what to
   configure instead.
4. **`:countTokens`** was "Unknown method" — decided: served with the vendor's count (FR-12). Asked
   first how tokens were counted before: the row's figures are the vendor's own report; before
   dispatch the reservation estimates (characters over four, deliberately high); only a stream
   abandoned before its usage report asks Vertex AI's `:countTokens`. None of these was a figure to
   answer a count with but the last, which is the one served.
5. **The Interactions API** — not decided, and not built. Genkit sends `gemini-2.5-flash-tts` to
   `/v1beta/interactions`.
6. **The catalogue's defaults** — decided: corrected (FR-13). The reference declared no embedding
   task types or batches, so LangChain's embeddings — which always name a task type — were refused
   until an administrator declared them; and it declared Gemini's speech models on Vertex AI as
   sampling at fixed settings, so LangChain's speech request (temperature 0.7) was refused, although
   Google accepted it live.
7. **C#** is still not measured: the sandbox cannot reach NuGet.

## 12. As built

As §4 – §5, on branch `feat/clients-in-every-language`.
