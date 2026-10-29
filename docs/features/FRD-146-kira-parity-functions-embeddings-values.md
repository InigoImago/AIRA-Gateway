# FRD-146 — KIRA parity: function calling, the predecessor's vectors, and its values

> Phase: 8 (KIRA parity) · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner's comparison of the predecessor against this gateway, and the
> owner's decisions on it: Live and `/version-info` left out, strict data mode kept, `/ki-usage`
> answers about the caller, ignored fields are ignored.
> Related: `FRD-107` (the surface), `FRD-113` (embedding options), `FRD-131` (tool calling),
> `FRD-111` (thinking), `FRD-114` (the catalogue), `FRD-627` (strict data mode), `FRD-147`
> (reranking, the other half of the same comparison).

## 1. Summary

A comparison of the predecessor with this gateway's KIRA layer found traffic a migrating client
sends that is answered differently or not at all: functions are not offered to the model, an
embedding of `gemini-embedding-2` is a different vector, one model cannot be listed under two ids
with two widths, `/models` spells its capabilities differently and says nothing about thinking
levels, and a handful of statuses and values differ. This FRD closes those, in the KIRA layer where
they are the predecessor's rules, and in the core only where the core lacked a concept.

**Built from the comparison report.** The shapes below come from it and, where it does not give
them, from Google's own documentation. Where a shape is assumed
rather than read off the predecessor, this document says so (§7), so the live comparison test can
confirm or correct it.

## 2. Goals & Non-Goals

**Goals**
- A KIRA client can declare functions, receive a call, and send the result back.
- An embedding of a `gemini-embedding-2` model is the vector the predecessor produced: the same
  task prefix, the same parts, the same method.
- Two catalogue entries can be one upstream model at two widths (`2003` → 3072, `2004` → 768),
  each released, priced and permitted on its own.
- `/models`, `usage_data`, the error statuses and the stream match the predecessor.
- `/ki-usage` tells the caller what **they** used, per model, as JSON or CSV.

**Non-Goals**
- **Live (`WS /live/{model_id}`)** — left out by the owner for now. A new transport needs its own
  ADR and decisions on budget, duration and audit first.
- **`/version-info`'s `jenkins` block** — left out by the owner. The endpoint is unauthenticated and
  a build URL names internal infrastructure.
- **Reranking** — `FRD-147`.
- Changing the Gemini surface's vectors: the predecessor's prefix is applied by the KIRA layer only.

## 3. User Stories
- As an **application developer** migrating an agent, I want my `tools` and `function_result`
  parts to work unchanged, so that the agent keeps calling its functions.
- As an **application developer** with an index built by the predecessor, I want the same vectors
  for the same text, so that I do not have to re-embed my corpus.
- As a **developer**, I want `/ki-usage` to tell me what I used, per model, so that I can check my
  own consumption without an administrator.

## 4. Functional Requirements

### Function calling
- **FR-1 Declaring functions.** `tools` on a chat request: a list of `{type?: "function", name,
  description, parameters}`, `parameters` a JSON Schema object under the predecessor's rule (§14).
  Validated before anything is spent; a missing or empty field is `422 VALIDATION_ERROR`, a filled
  one it cannot take `422 INVALID_TOOL_CONFIGURATION`, naming it (§5.1).
- **FR-2 Parts by role.** A `function_call` part (`{"type"?,"id"?,"name","arguments",
  "opaque_metadata"?}`) only in a `role: model` turn of the history; a `function_result` part
  (`{"type"?,"call_id","name","result","error"?}`) only in the request or a `role: user` turn. An
  untyped part is read by its fields. Anything else is `422 VALIDATION_ERROR`.
- **FR-3 Pairing.** A `function_result` answers a `function_call` of the model turn before it, by
  `call_id`, and names the same function. A result that answers nothing, answers a call under
  another name or names none, a call whose name is not a function name, and two calls with one id
  are `422 INVALID_FUNCTION_RESPONSE`; a call to a function the request does not offer is
  `422 INVALID_TOOL_CONFIGURATION`.
- **FR-4 The answer.** A function call the model makes is a part of the answer,
  `{"type":"function_call","id","name","arguments","opaque_metadata"}`. `opaque_metadata` carries
  the model's `thoughtSignature` there and back, unread; a client returns it with the call.
- **FR-5 The stream.** `update` events for text, then one `{"status":"function_call","data":<part>}`
  per call, then `completed`, whose `parts` carry the calls too.
- **FR-6 Use case.** Functions are allowed only where the use case enables tool calling, as on the
  Gemini surface; refused as `422 INVALID_TOOL_CONFIGURATION`.

### Embeddings
- **FR-7 The predecessor's prefix.** For a model whose **upstream name** begins with
  `gemini-embedding-2`, each text is sent as its own part with the task prefix Google documents
  (§5.2), and no `task_type` field. A list is one embedding of all its parts, as before.
- **FR-8 The predecessor's method.** For such a model the Vertex adapter sends `:embedContent` with
  `embedContentConfig.outputDimensionality`, not `:predict`. The canonical request says whether its
  texts are one embedding (`aggregate`); an adapter that cannot aggregate refuses rather than
  splitting.
- **FR-9 The default task type applies.** The predecessor always sends one (`RETRIEVAL_QUERY` when
  none is named), so for these models the default is applied whenever the model declares task types.

### One model under two ids
- **FR-10 Aliases.** A catalogue entry may name its upstream model in `addressing.model`. The adapter
  sends that name; everything else — release, approval, residency by name, price, numeric id,
  dimensions — is the entry's own. Neither entry inherits anything from the other.

### Values
- **FR-11 `/models` capabilities** are spelled `Chat`, `Embedding`, `Reranker`.
- **FR-12 `/models` thinking.** `thinkingConfig.mode` lists the control modes **and** the level words
  a model declares; `defaultThinking` is always present (the declared default, else `auto` — the
  model decides, which is what happens when nothing is sent); `minTokens`/`maxTokens` are `null`
  rather than absent.
- **FR-13 `usage_data.token_output`** excludes thinking tokens, as the predecessor reports it.
- **FR-14 Ignored, as the owner decided.** `thinking.tokens` with a mode other than `limited`, and
  attachments in `system_instruction`, are ignored and named in `X-AIRA-Unmodelled-Fields`.
- **FR-15 `422` where the predecessor answers `422`**: an attachment type nobody accepts, an invalid
  `responseSchema`, `temperature` outside 0–2, `maxTokens ≤ 0`, an unknown `task_type`, an empty
  embedding text — the last three as `VALIDATION_ERROR`, as the predecessor codes them; `/ki-usage`'s
  invalid date. A `422`'s `details` carry `type`, and `loc` begins with `"body"`.
- **FR-16 An upstream failure is `502 EXTERNAL_KI_API_ERROR`**, whatever the provider said, except
  `429`, which stays `429 EXTERNAL_KI_API_TOO_MANY_REQUEST`.
- **FR-17 An empty answer is `parts: []`**, not one empty text part.
- **FR-18 Documents before text**, as the predecessor orders a turn, unless the turn carries function
  parts — their order is the conversation's.
- **FR-19 The stream keeps its connection**: a `: ping` comment every 15 seconds while nothing else
  was sent, and `X-Accel-Buffering: no`, so a proxy neither closes nor buffers it — **from the
  moment the model is asked**, not from its first chunk (`ADR-0046`): the silence this is for is
  the one before a thinking model's first word, and a status that waited for that word kept the
  connection silent through all of it. The interval is `AIRA_STREAM_KEEPALIVE_SECONDS`.
- **FR-20 Sampling defaults are a catalogue field**, `default_sampling` (`top_p`, `top_k`), applied
  where the caller sets none, **shipped empty**: the predecessor's `0.8`/`40` are an installation's
  choice, not this gateway's. The model's maximum as the default output cap is
  `default_max_output_tokens`, which exists.

### `/ki-usage`
- **FR-21 The caller's own usage.** Any authenticated caller; one row per model they used in the
  window: `user_id` the caller's name, `model_id` the catalogue's numeric id, counts and token sums.
  No other person's figures, whatever the caller's role — the console's reports are where those are
  read, under their own rules. Strict data mode is unaffected: it governs who sees **another**
  person.
- **FR-21a In the credential's use cases.** The rows count the caller's traffic in the use cases
  the credential may act in — an API key's one use case, a signed-in person's memberships — exactly
  as `/v1beta/me/budget` does. A key a use-case administrator issued in a member's name (`FRD-604`
  FR-5) acts as that member, and the administrator holds its plaintext: without this bound, the key
  answered for the member's traffic in **every** use case, including ones the administrator has no
  part in, past strict data mode and without a recorded read. A credential that may act nowhere is
  answered with nothing.
- **FR-21b A bounded window, asked once.** The window spans at most 366 days, the console's bound
  (`400 INVALID_TIME_RANGE` past it), and the answer is one aggregate over the request log, filtered
  by person in columns an index can answer. Any credential holder may call this, so it must not
  cost more than the figure it returns. Both ends of the window are compared in UTC: a date without
  an offset is read as UTC, one with an offset converted.
  *Changed by `FRD-629`:* a date without an offset is read on the installation's clock.
- **FR-22 CSV** when the caller sends `Accept: text/csv`: the same columns, UTF-8 with a BOM.

## 5. Design & Architecture

### 5.1 Function calling in the KIRA layer

The core already carries functions (`ToolDeclaration`, `ToolCallPart`, `ToolResultPart`, `FRD-131`).
The KIRA layer only parses and maps:

- `schemas.py`: `ToolSpec`, and `RequestContent.parts` accepting the two typed part kinds next to
  text and attachments. The role rules (FR-2) are model validators on the turn types.
- `mapping.py`: `tools` → `ToolDeclaration` with `parameters_json_schema` (carried as written, as the
  Gemini surface does for `parametersJsonSchema`); `function_call` → `ToolCallPart`, its
  `opaque_metadata.thoughtSignature` → `signature`, and no other key; `function_result` → `ToolResultPart`, a
  non-string `result` serialised as JSON.
- The pairing rule (FR-3) is checked in the mapper, over the mapped conversation.
- The use case's switch is the shared `check_tools_permitted`. Its refusal becomes its own type,
  `ToolsNotPermitted`, so this surface can give it the predecessor's code without reading a message.

Validation rules (FR-1), each with a test: a name matches `^[A-Za-z_][A-Za-z0-9_.-]{0,63}$`; names are
unique; the description says something; `parameters` passes the predecessor's rule
(`tool_schema.fault`) and its root `type` is `object`; at most 128 functions;
`function_call`/`function_result` parts without `tools` on the request are refused.

*Since 2026-10-22, after a security audit:* a property's `type` may also be one of the six beside
`"null"` — `["string", "null"]`, JSON Schema's spelling of an optional argument and Google's
documented one — carried to the model as written; every dialect reads it. It had been looked up in
a set and was a `500` with no audit row. Any other list or object there is
`INVALID_TOOL_CONFIGURATION` naming the path, and a part whose `type` is not a word is
`VALIDATION_ERROR` (`test_a_kira_callers_value_is_never_a_server_error.py`, which also sweeps every
function field with every kind of JSON value).

### 5.2 The predecessor's embedding

Google documents the prompt structure `gemini-embedding-2` expects in place of `task_type`. Mapped
from the eight task types:

| Task type | Text sent |
|---|---|
| `RETRIEVAL_QUERY` | `task: search result \| query: {text}` |
| `RETRIEVAL_DOCUMENT` | `title: none \| text: {text}` |
| `QUESTION_ANSWERING` | `task: question answering \| query: {text}` |
| `FACT_VERIFICATION` | `task: fact checking \| query: {text}` |
| `CODE_RETRIEVAL_QUERY` | `task: code retrieval \| query: {text}` |
| `CLASSIFICATION` | `task: classification \| query: {text}` |
| `CLUSTERING` | `task: clustering \| query: {text}` |
| `SEMANTIC_SIMILARITY` | `task: sentence similarity \| query: {text}` |

Applied in `api/kira/mapping.to_embedding`, never in the adapter: the Gemini surface sends what its
caller wrote, and its vectors do not move. The upstream body for such a model:

```json
POST …/publishers/google/models/gemini-embedding-2:embedContent
{"content": {"parts": [{"text": "task: search result | query: a"},
                       {"text": "task: search result | query: b"}]},
 "embedContentConfig": {"outputDimensionality": 768, "autoTruncate": false}}
```

`autoTruncate: false` for the reason `:predict` sends it (`embedding_mapping`). Without `aggregate`
— the Gemini surface's `batchEmbedContents` — each text is its own `:embedContent` call with one
part, so the Gemini surface keeps one vector per text.

### 5.3 Aliases

`addressing.model` is the name on the wire. `_VertexAdapter` resolves it once, `upstream_name()`,
and every URL it builds uses that name; everything keyed by the model — the catalogue row, the
residency permission by name, the price, the audit row — uses the entry's own name. An alias is
therefore a separate release: approving `gemini-embedding-2` approves nothing about
`gemini-embedding-2-768`.

### 5.4 What changes in the shared layer
- `CanonicalEmbeddingRequest.aggregate`.
- `ToolsNotPermitted(GeminiHTTPError)` — same status and message on the Gemini surface.
- `default_sampling` on the catalogue, the event and the read model; applied in `fitted`.

## 6. Data Model
- `model_catalog.default_sampling` (JSON, nullable) in Management, the `model.*` event and the
  gateway's read model; a migration on both planes. No other table changes.

## 7. API / Interface Contract

First built from the report, with four shapes assumed. The second comparison (§14) read them off the
predecessor: `result` and the CSV order were right; the result names its call in `call_id`, not
`id`, and the signature's key is `thoughtSignature`, not `thought_signature`. Both are corrected;
neither shape had been released.

## 8. Security & Privacy
- Functions stay behind the use case's switch (FR-6) and are never executed (`ADR-0013`).
- `/ki-usage` no longer needs `report.read_all` because it no longer answers about anyone else
  (FR-21); the figures are the ones `/v1beta/me/budget` already gives the same caller, in the same
  use cases (FR-21a).
- No new stored field, transmission or telemetry: the privacy notice is unchanged.

## 9. Observability
- The KIRA refusal log names `INVALID_TOOL_CONFIGURATION` / `INVALID_FUNCTION_RESPONSE`.
- `aira.request.tools` on the span, as on the Gemini surface.

## 10. Testing & Acceptance Criteria
- Every rule of §5.1 has a test in `gateway/tests/test_kira_functions.py`; a round trip (declare →
  call → result → answer) through the mock, streamed and not.
- The upstream body for `gemini-embedding-2` is pinned byte for byte (§5.2), for one text, a list
  and every task type; the Gemini surface's body for the same model carries no prefix.
- `2003` answers 3072 values and `2004` answers 768, from one upstream model; an alias unreleased to
  the use case is refused although its upstream model is released.
- `/ki-usage` returns only the caller's rows, never a colleague's, whatever the caller's role, and
  the CSV starts with a BOM; a key answers for its own use case only, a window over a year is
  refused, a mixed-zone window answers, and the answer is one query over the request log.
- Mutations for each property.

## 11. Dependencies & Risks
- The assumed shapes (§7). A wrong guess is a `422` a migrating client sees at once, which is the
  direction this surface prefers.
- `:embedContent` on Vertex refuses an API key (`embedding_mapping`); a `gemini-embedding-2` alias
  needs the installation's Google identity, as the predecessor had.

## 12. Rollout / Demo
- The mock embeds aggregated parts deterministically and answers a function call when a function is
  offered, so the showcase can demonstrate both without a cloud call.

## 13. As built

- **Functions** — `schemas.ToolSpec`, the typed part checks in `RequestContent` and the role rules
  in `ConversationContent`/`ChatRequest`; `mapping.tools_of` (FR-1, `INVALID_TOOL_CONFIGURATION`),
  `mapping.check_results_answer_calls` (FR-3) and `function_call_part`/`function_call_event`
  (FR-4, FR-5). The use case's switch raises `ToolsNotPermitted`, which the Gemini surface answers
  as before. The stored answer leaves `opaque_metadata` out (`mapping.stored`).
- **Embeddings** — `api/kira/embedding_prompts.py` holds Google's table and decides by
  `ModelDeclaration.wire_name`; `CanonicalEmbeddingRequest.aggregate` and `task_in_text`;
  `embedding_mapping.embed_content_body`. `autoTruncate: false` is sent as on `:predict`, which the
  predecessor's body may not carry: it changes no vector of a text within the model's limit. AI
  Studio aggregates through `embedContent` too; `:predict` and OpenAI-compatible servers refuse
  `aggregate` (`DialectUnsupported`).
- **Aliases** — `core.canonical.wire_name`; `VertexTransport.url(wire_model=…)` for Gemini and
  Claude on Vertex. Agent Platform's open models name the model in the body and take no alias.
- **Empty embedding text** — `""`, `[]`, `[""]` and a text of blanks are `VALIDATION_ERROR`
  (a text of blanks was `EMPTY_EMBEDDING_INPUT` until the second comparison, §14).
- **Turn order** — documents first in a turn without function parts; an earlier test pinned the
  opposite and was turned round.
- **Stream** — `api/serving/keepalive.py` reads the model's chunks in one task of its own and sends a
  comment when fifteen seconds pass without one.
- **`/ki-usage`** — `ReportingService.by_model(principal.use_cases, …, person=…)`, one grouped
  query; a credential with no person or no use case is answered with nothing rather than with the
  installation. The person filter is `request_person_is` (`db/models/owned.py`), the indexable
  spelling of the grouping expression, which every report narrowed to a person now uses.
- **Sampling defaults** — `model_catalog.default_sampling` on both planes (Management migration
  `0011`, gateway `0054`), validated against `fixed_sampling`, applied in `prepare.fitted` only for
  the controls the routed adapter expresses; two fields in the console's model editor.

## 14. The second comparison

The owner ran the comparison again, in more depth this time, and brought the
findings. The owner's line for the round: **keep everything secure, and take over no odd behaviour**
— this is enterprise software. So each finding was decided on that line rather than taken whole.

**Taken over** — a client of the predecessor fails without it, and nothing is loosened:

- **The function parts' fields** (FR-2, FR-3): `call_id` on a result; `type` optional on both
  parts, read from the fields; a call's `id` optional (such a call is replayed and cannot be
  answered); `error` on a result, sent as `{"result", "error"}`; `opaque_metadata` always present,
  `null` when empty, its key `thoughtSignature`.
- **Stricter checks** (FR-1, FR-3), in the predecessor's codes: `tools[].type` modelled;
  `description` and `parameters` required; the predecessor's schema rule for `parameters`
  (`api/kira/tool_schema.py`); a call to a function not offered, a call's name, duplicate call ids,
  a result without a name.
- **Errors that say what happened**: an answer with no text, no call and no output token is
  `502 EXTERNAL_KI_API_ERROR` naming the finish reason (`mapping.check_answered`), not `parts: []`;
  a `model_id` nothing can have — zero, negative, past the column — is `422 MODEL_NOT_FOUND`, and
  so is a model with nowhere it may be sent; a blank embedding text is `VALIDATION_ERROR`.
- **On the stream**, a `responseSchema` answer sends no `update` events: the document comes whole,
  in `completed`.
- **A `401` names its scheme** — `WWW-Authenticate: Bearer`, on both surfaces (RFC 9110, RFC 6750).
- **`/ki-usage`** takes seconds since 1970 as well as ISO 8601.

**Declined**, each because the predecessor's behaviour is the odd one, and said so in the migration
guide's §4:

- **Copying `opaque_metadata` onto the provider's part.** A caller could write any field of a
  Gemini part — a `fileData` reference to a bucket the gateway's account can read among them. Only
  the signature is read.
- **A result answering any earlier call**, and **function parts without `tools`**: providers other
  than Gemini refuse such a conversation, and Gemini answers a misplaced result against the wrong
  call. Refused here before anything is spent.
- **`200` for a structured answer that did not finish**, and **dropping unknown `responseSchema`
  keywords**: both hand an application a document that breaks what it asked for.
- **Letting Google truncate an embedding text**: the vector of part of a document is filed under
  the whole of it.
- **A default `model_id` on `/rerank`**: a cost nobody chose.
- **`Retry-After` on a budget's `429`**: a client would sleep until the period ends.
- **An unknown thinking word as `VALIDATION_ERROR`**: the words are the model's, and
  `INVALID_THINKING_MODE` names what it offers.
- **`/models` without a credential, every CORS origin with credentials, `/ki-usage` across
  people, Live** — the owner's earlier decisions stand; CORS is configuration
  (`AIRA_CORS_ORIGINS`), and the attachment bounds stay as they are.
