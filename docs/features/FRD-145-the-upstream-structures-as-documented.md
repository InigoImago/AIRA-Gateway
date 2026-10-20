# FRD-145 — The upstream structures, held to what Anthropic and Google document

> Phase: 8 (KIRA parity) · Status: **Built** · Owner: Vadim Scheibe
> Origin: the owner, after integration trouble with the Gemini interface and with the Anthropic
> adapter: check the structures again, all of them, whether they fit.
> Related: `FRD-119` (Anthropic on Vertex), `FRD-111` (thinking), `FRD-124` (nothing dropped),
> `FRD-131` (tool calling), `FRD-133` (prompt caching), `FRD-135` (reasoning), `FRD-143` (current
> Gemini clients), `FRD-508` (the reference list).

## 1. Summary

Every field this gateway sends to Anthropic and to Google, and every field it accepts from a Gemini
client, was read against the vendors' own references: Anthropic's Messages API and its thinking,
structured-output and pricing pages; Google's discovery documents for Vertex `v1` and the Gemini API
`v1beta`; and the bodies OpenCode 1.19.3, OpenCode 2.0.32 and the AI SDK's Google provider put on the
wire. The Gemini side sent nothing Vertex would refuse but refused four things current clients send.
The Claude side sent requests Anthropic's current models refuse, on ordinary use.

## 2. What was found

**Claude on Vertex (from Anthropic's reference).**

| Rule | Before |
|---|---|
| Claude Opus 4.7 and later refuse `thinking: {type: "enabled"}`; they think `adaptive`, steered by `output_config.effort` (`low` … `max`). Claude 4.5 and earlier refuse `adaptive`. | Only `enabled` with a budget; a level word or `auto` was refused by the dialect, so the five effort words the reference list declares for every current Claude could not be used. |
| A budget is at least 1024 tokens. | Not checked. |
| The newest models return thinking blocks empty unless `display: "summarized"`. | Never asked: a use case with reasoning on got none. |
| From Opus 4.7 on: `top_k` refused, `temperature` only 1, `top_p` only ≥ 0.99. | Forwarded: a client's own temperature was a 400. |
| A text block must contain non-whitespace text; a turn must not be empty. | Sent: a model turn that was only thoughts became `content: []`. |
| Structured output takes ten string formats, `minItems` 0 or 1, no length or numeric constraints. | `format: int32` (Google's width of a number) was sent; `minItems: 1` was refused; `minProperties` was sent. |
| Media goes in `image` (four formats) or `document` (PDF, plain text) blocks. | Every non-PDF type was sent as an image. |
| An hour's cache write costs 2x input, five minutes' 1.25x; usage says which (`cache_creation`). | All writes at the one write price. Thinking tokens (`output_tokens_details`) not counted. |
| A refused or truncated answer says so in `stop_reason`. | Under a schema it became "schema not satisfied". |

**The Gemini surface (each measured by posting what the client sends).**

| Client | Sends | Before |
|---|---|---|
| OpenCode 1 and 2 | `includeThoughts` on every stream to a thinking model | 400: streams carried no reasoning |
| OpenCode 2, the AI SDK for Gemini 3 | media in `functionResponse.parts` when a tool returns an image | 400 "Extra inputs" |
| AI SDK 1–3, OpenCode 1 with MCP tools | a parameter as `anyOf` without `type`, or `type: null` | 400 "type: Field required" |
| OpenCode 2 (last step of a bounded run), OpenCode 1 (structured answer), AI SDK (strict tools) | `functionCallingConfig` `NONE`, `ANY`, `VALIDATED`, `allowedFunctionNames` | 400 by name |
| google-genai replaying a Gemini 3 turn | `thoughtSignature` on a text part | 400 |
| every client | reads `cachedContentTokenCount` | never sent |

**To Google.** Every field sent exists in `v1`. Three defects remained: a call and its result were
sent without the id that pairs them (Google pairs by id where both carry one, which is how two
parallel calls of one function are told apart); on a stream, calls Google sent without an id got
the same fallback id in two chunks; and `MODEL_ARMOR` stopping an answer was read as `OTHER`, not
as a refusal.

## 3. Functional Requirements

- **FR-1 — thinking in the shape the model takes.** `limited` is `enabled` with `budget_tokens`
  (at least 1024, below `max_tokens`); `auto` is `adaptive`; a level word is `adaptive` with
  `output_config.effort` set to it. Which a model accepts is its catalogue declaration, as for every
  other dialect: the reference list declares a budget range for Claude 4.5 and earlier and the five
  effort words for 4.7 and later. With the use case's reasoning on, `display: "summarized"`.
- **FR-2 — a continued call.** With a budget, Anthropic requires the model's own thinking block
  before a call it continues, which this gateway does not carry; that stays a named refusal. With
  `adaptive` thinking no turn has to begin with one, and the turn is sent.
- **FR-3 — fixed sampling, declared.** A catalogue declaration `fixed_sampling` (Management field,
  console checkbox, model event, read model) says a model takes no `top_k` and no `temperature` or
  `top_p` other than 1. The reference list fills it from models.dev's `temperature: false`. A
  request that sets one skips such a model by name (`SamplingAccepted`); temperature 1 and top_p 1
  ask for nothing and pass — KIRA sends `temperature: 1` on every request.
- **FR-4 — content Anthropic accepts.** Empty and whitespace text blocks, and turns left with no
  block, are left out; at the surface, a turn that was only thoughts is left out whole. Images go as
  `image` (JPEG, PNG, GIF, WebP), PDF and plain text as `document`; any other type is refused by name.
- **FR-5 — schemas Anthropic accepts.** `int32`, `int64`, `float`, `double` and `enum` formats are
  left out (they state no constraint); another format outside Anthropic's ten, `minItems` above 1,
  `minProperties` and `maxProperties` skip the model by name.
- **FR-6 — the price of a cache write.** The hour's share of the writes (`long_cache_write_tokens`)
  is priced at twice the input price, never below the five-minute rate; thinking tokens are counted
  as `reasoning_tokens`, buffered and streamed.
- **FR-7 — a refused or truncated structured answer** keeps `REFUSAL` or `MAX_TOKENS`.
- **FR-8 — reasoning on a stream.** `CanonicalChunk.reasoning_delta`, filled by all three dialects
  under the use case's switch, sent to a Gemini client as `thought: true` parts before the text,
  and stored with the answer by a surface that sends it. `includeThoughts` on a stream follows the
  same rule as on `generateContent` (`FRD-135` FR-4). This revises `FRD-135` §5.4.
- **FR-9 — tool modes.** `ToolChoice(mode, allowed)`: Google's four modes and the names they may
  narrow to, refused by name where a mode makes no call or a name is not declared. To Google as
  written; to Anthropic as `tool_choice` (`none`, `any`, `tool`; several allowed names offer only
  those functions); to the OpenAI dialect as `none`, `required` or a named function. `VALIDATED` on
  either of those, and `ANY` with a Claude thinking budget, skip the model by name.
- **FR-10 — media in a tool result.** `ToolResultPart.attachments`, checked like any attachment and
  counted in the request's media types; to Google in `functionResponse.parts`, to Anthropic inside
  the `tool_result`; the OpenAI dialect refuses it by name.
- **FR-11 — schemas as generators write them.** A schema node may leave `type` out when it is a
  union (`anyOf`), and `NULL` is a type.
- **FR-12 — to Google.** Ids are sent on a call and its result when both carry the same one; stream
  calls without an id are numbered across the stream; `MODEL_ARMOR` is a refusal.
- **FR-13 — back to a Gemini client.** `cachedContentTokenCount`; a `thoughtSignature` on a text part
  is accepted and not forwarded (Google requires one only on a call).

## 4. Not done, and why

- **Claude's thinking blocks are not carried through a tool loop.** Anthropic recommends passing them
  back; in `adaptive` mode it does not require it. Carrying them would put the model's signed
  reasoning, and for older models its text, into an opaque field the caller holds, and replay must
  reproduce the original block order exactly. Left until it can be measured against Claude.
- **`countTokens`** stays unserved, as `docs/API.md` says.
- **Claude Sonnet 5.5's "off"** is `between_tools`, not `disabled`; the reference list declares no
  `disabled` for it, so it is not offered.
- **`ANY` with adaptive thinking** is refused by Anthropic on Opus 5.5, Sonnet 5.5, Fable 5.1 and
  Mythos 5.1, which no declaration states; it reaches the caller as Anthropic's 400.
- **`output_config.format` under an organisation policy** that forbids Anthropic's
  `structured_outputs` — likely the same policy that refuses a strict tool. The diagnosis already
  names the policy when Vertex refuses for it; not measured.

## 5. Verification

- Hermetic: `gateway/tests/test_claude_as_anthropic_documents_it.py`,
  `gateway/tests/test_gemini_structures_current_clients_send.py`,
  `gateway/tests/test_a_model_that_samples_at_its_own_settings.py`; the Management field and the
  console checkbox in their suites.
- Live, against Vertex `europe-west1`, `gemini-2.5-flash`, with the bodies the mapping builds: a
  paired call and result carrying ids, an image in a function result with a typeless union
  parameter, `toolConfig` `ANY` with an allowed name, `NONE` and `VALIDATED`, and a stream with
  thoughts — each `200`.
- **Not live against Claude**: Vertex refuses an API key for Anthropic models, and the sandbox has
  no service account. The console's *Ask the model* on a Claude model's level words sends the new
  `adaptive` body through the real adapter.
- Mutations `ST-1`–`ST-41`, `FS-1`–`FS-8`; `V7`, `U2`, `RS3` and `FX-G3-2b` re-anchored, `RS3`
  now guarding the streamed thought.
