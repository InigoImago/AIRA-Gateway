# FRD-119 — Anthropic models on Vertex: the second dialect

> Phase: 8 (KIRA parity) · Status: **Done (2026-08-06)** · Owner: Vadim Scheibe · Last updated: 2026-08-06
> Origin: confirmed 2026-08-06 — Model Garden serves **Gemini and Anthropic** models.
> Programme: `ADR-0010`. Requires `FRD-115` (transport, auth, region). Amends `FRD-110`–`FRD-114`.

## 1. Problem

Model Garden makes two vendors available through one platform, one project and one credential. It
does **not** make them speak one API.

Anthropic models on Vertex are invoked through `:rawPredict` / `:streamRawPredict` and expect the
**Anthropic Messages API** body. The differences are not cosmetic:

| | Gemini | Anthropic |
|---|---|---|
| Messages | `contents[].parts[]`, roles `user`/`model` | `messages[].content[]`, roles `user`/`assistant` |
| System prompt | `systemInstruction` (a content) | `system` (top-level) |
| Output cap | `maxOutputTokens`, optional | `max_tokens`, **required** |
| Attachments | `inlineData{mimeType,data}` | content blocks `image` / `document` with a `source` |
| Thinking | `thinkingConfig.thinkingBudget` | `thinking{type,budget_tokens}`, **budget < max_tokens**, and thoughts are **returned** |
| Structured output | `responseSchema` | **none** — done with a forced tool call |
| Usage | `usageMetadata.promptTokenCount/...` | `usage.input_tokens/output_tokens` |
| Streaming | SSE of partial `GenerateContentResponse` | SSE of typed events (`content_block_delta`, `message_delta`, …) |
| Embeddings | yes | **none** |

Every row is a mapping the adapter has to own. Three of them — the required `max_tokens`, the
absent `responseSchema`, and the returned thinking — change requirements in FRDs that are already
written, and those amendments are §6.

This is also the first genuine test of `FRD-100`'s claim that the canonical core is
provider-agnostic. Until now "two surfaces, two upstreams" meant two spellings of Google's format.

## 2. Goals & Non-Goals

**Goals**
- Dispatch canonical requests to Anthropic models on Vertex: generation, streaming, attachments,
  thinking, structured output.
- Map faithfully in both directions, including usage and finish reasons, so budgets, pricing and
  reporting are correct without knowing which vendor answered.
- Keep the vendor invisible above `upstreams/`.

**Non-Goals**
- **Anthropic's direct API** (`api.anthropic.com`). We reach Anthropic through Vertex, in the EU,
  under one credential. A direct adapter would be a second credential and a second residency
  question.
- **Returning thinking blocks to callers.** `FRD-111` §2 already decided this for Gemini; Anthropic
  makes it live, because it returns them by default when thinking is enabled (§5.4).
- Tool use as a caller-facing feature. It is used *internally* for structured output (§5.5);
  exposing it is its own FRD.
- Embeddings. Anthropic has none; `FRD-114`'s capability declaration is what stops a request from
  ever getting here (§5.7).

## 3. User Stories
- As a **use-case administrator**, I want to put a Claude model in a fallback chain beside a Gemini
  one and have both behave identically to my callers.
- As **IT Steuerung**, I want spend across both vendors in one report, priced correctly per model.
- As an **application developer**, I want the same request body regardless of which model answers.

## 4. Functional Requirements

- **FR-1 Generation and streaming** via `:rawPredict` / `:streamRawPredict`, with
  `anthropic_version` set as Vertex requires.
- **FR-2 Message mapping.** Canonical `Role.MODEL` ⇄ `assistant`; `Role.SYSTEM` ⇄ the top-level
  `system` parameter, **concatenated** when several system messages exist, because Anthropic takes
  one and the canonical model permits more.
- **FR-3 `max_tokens` is always sent.** Required by the API; resolved per §5.3.
- **FR-4 Attachments** map to `image` and `document` content blocks, subject to what the model
  declares (`FRD-114`) — Anthropic's supported media types are a subset of `FRD-110`'s allow-list,
  and a type this model cannot take is refused with a message naming the model, not silently
  dropped.
- **FR-5 Thinking** maps to `thinking{type:"enabled",budget_tokens}`, with the **budget strictly
  below `max_tokens`** and validated before dispatch (§5.4).
- **FR-6 Thinking blocks are consumed, never returned.** §5.4.
- **FR-7 Structured output via a forced tool call.** §5.5.
- **FR-8 Usage mapping.** `input_tokens`/`output_tokens` → canonical prompt/completion. Cache-read
  and cache-creation token fields, where present, are **recorded and not silently folded into the
  input count** — same rule as unpriced traffic: a figure we do not understand is not zero.
- **FR-9 Stop reasons mapped**, including `max_tokens` (truncation) and `refusal`, so `FRD-112`
  FR-6 can tell a complete document from a truncated one.
- **FR-10 Errors mapped** onto `UpstreamError` with the upstream status preserved, so the existing
  429/503/504 pass-through keeps working across both vendors.
- **FR-11 No embedding.** The adapter does not implement it; `FRD-114`'s capability declaration
  refuses such a request before dispatch (§5.7).

## 5. Design & Architecture

### 5.1 Where it sits

`VertexAnthropicAdapter` under `FRD-115`'s `VertexTransport`: the transport owns URL, OAuth,
retries and Google-level errors; this adapter owns the body in both directions. Mapping functions
are pure and unit-tested without HTTP, exactly as `upstreams/gemini_mapping.py` is — that file is
the template, and the symmetry is deliberate.

### 5.2 The canonical core gets its first real exercise

`FRD-110` reshapes `CanonicalMessage` into ordered parts. That shape maps cleanly onto Anthropic's
content blocks — arguably more cleanly than onto Gemini's, since Anthropic *is* a list of typed
blocks. If a mapping here needs a field the canonical model does not have, that is a signal the
canonical model is Gemini-shaped rather than neutral, and the right response is to fix the
canonical model rather than to smuggle a vendor field through it. Worth watching during
implementation; noted here so the temptation is recognised.

### 5.3 `max_tokens` is required, and our canonical field is optional

`CanonicalRequest.max_output_tokens` is `int | None`, because Gemini defaults it server-side.
Anthropic rejects a request without it. A caller who omits it — which most will, because it is
optional today — would get a 400 from the upstream that has nothing to do with what they did.

So: **`FRD-114` carries a per-model default output cap**, and the adapter sends
`max_output_tokens or model.default_max_output_tokens or model.max_output_tokens`. A model with no
declaration at all is `FRD-114` FR-7's fail-closed case and refuses before dispatch, naming the
missing declaration — which is a better failure than a vendor error message.

This also improves the budget reservation: `_estimate` currently falls back to a global default
when the caller sets no cap. A per-model figure is a better estimate for both vendors, so the
change is worth having independently of Anthropic.

### 5.4 Thinking: two differences, one of them a data-protection matter

**The budget must be below `max_tokens`.** Anthropic's thinking budget is drawn from the same
output allowance. A configuration where the declared thinking maximum exceeds a model's output cap
produces requests that always fail. So validation is `budget < max_tokens` at dispatch, and
`FRD-114`'s declaration is validated for internal consistency when it is authored — the catalog
should not be able to hold a combination that cannot work.

**Thinking blocks come back.** With thinking enabled, the response contains `thinking` content
blocks alongside `text` ones. `FRD-111` §2 decided not to return chain-of-thought: it is the least
reviewed text a model produces, it frequently restates the input, and it would land in a response
that AIRA also persists under `store_payloads`.

With Gemini that decision was cheap — we simply do not ask for thoughts. With Anthropic it is an
active obligation: the adapter **drops thinking blocks when assembling the canonical response**,
and they are never persisted, logged or traced. Their *token count* still reaches usage via FR-8,
because they were billed.

This is a case where a mapping omission would leak the exact category of content we decided not to
handle, so it gets its own test asserting that a known thinking string appears in no response body,
no audit row and no span.

### 5.5 Structured output without `responseSchema`

Anthropic has no schema parameter. The established technique is a **forced tool call**: define one
tool whose `input_schema` is the caller's schema, set `tool_choice` to that tool, and read the
model's tool input as the result.

`FRD-112`'s schema subset is OpenAPI-3.0-flavoured (`STRING`, `OBJECT`, `propertyOrdering`) and
Anthropic's `input_schema` is JSON Schema. The adapter translates: uppercase types lower-cased,
`propertyOrdering` dropped (no equivalent — and dropping it changes nothing semantically, only
key order), bounds mapped by name. **A schema field with no faithful equivalent is a refusal, not
a silent drop** — a caller who constrained a value with `pattern` and gets an unconstrained answer
has been quietly misled.

The response is then the tool input, serialised as the JSON document the caller expected. Two
consequences the tests must pin:

- **Streaming.** The document arrives as `input_json_delta` fragments — still text deltas of one
  JSON document, which is exactly what `FRD-112` FR-5 already promises. The shape holds.
- **Not answering.** A model that returns text instead of calling the tool has not satisfied the
  schema. That is `FRD-112` FR-6's case and must be an error, not prose returned as data.

`FRD-114`'s `structured_output` capability therefore means "this model can do it *somehow*", and
how is the adapter's business — which is the right place for it, and is why `FRD-112` §5.3 checks
the capability **after routing**.

### 5.6 Streaming events

Anthropic streams typed SSE events; the canonical `CanonicalChunk` needs text deltas plus a final
chunk carrying finish reason and usage. The mapping: `content_block_delta` of type `text_delta` →
a text chunk; `input_json_delta` → a text chunk (§5.5); `thinking_delta` → **discarded** (§5.4);
`message_delta` → the finish reason and the output token count; `message_start` → the input token
count, which arrives *first* here and *last* in Gemini.

That ordering difference is worth stating: the final-chunk usage assembly must accumulate rather
than assume one event carries everything, or one vendor's token counts silently become zero.

### 5.7 No embeddings, refused early

`Upstream.embed` raises `NotImplementedError` — but no caller should ever reach it, because
`FRD-114` declares Anthropic models without the `embed` capability and the request is refused with
a message naming the model. The exception exists as a backstop for a misconfigured catalog, not as
the mechanism.

## 6. Amendments this FRD makes to already-written FRDs

Listed together so they are not lost in the prose:

- **`FRD-110`** — the allow-list becomes an intersection: what AIRA accepts **and** what the target
  model declares (FR-4). Attachment token estimates are per vendor.
- **`FRD-111`** — the abstract level → budget table is per model and now genuinely per vendor;
  `budget < max_tokens` becomes a validation rule; §5.4's drop-and-never-persist obligation is
  added to its non-goal.
- **`FRD-112`** — `structured_output` means "by some mechanism"; the adapter may refuse individual
  schema fields it cannot express faithfully; §5.3's post-routing check becomes load-bearing rather
  than defensive.
- **`FRD-113`** — embedding is Gemini-only; the capability declaration is what enforces it.
- **`FRD-114`** — gains `publisher`, a **default output cap** (§5.3), per-model attachment media
  types, and consistency validation for the thinking block (§5.4).

## 7. API / Interface Contract

No public API change. Internal: `Upstream`, unchanged — asserted, as in `FRD-115` §10.

## 8. Security & Privacy

- **§5.4 is the security-relevant requirement**: chain-of-thought must not reach a caller, a log,
  a span or the audit table. Tested by assertion on all four, not by inspection.
- Attachments follow `FRD-110`'s rules unchanged; the adapter forwards, never inspects.
- One credential and one residency posture for both vendors (`FRD-115`), which is the practical
  benefit of the Model Garden route and worth preserving by not adding a direct Anthropic path.

## 9. Observability

`aira.upstream.publisher = "anthropic"` (from `FRD-115`), and cache-token fields from FR-8 recorded
distinctly. Thinking token counts are recorded; thinking *content* is not.

## 10. Testing & Acceptance Criteria

- **Unit (mapping, hermetic)** — roles both ways; several system messages concatenated; attachments
  to content blocks; usage field mapping including cache tokens; every stop reason; `max_tokens`
  always present including when the caller omitted it.
- **Unit (thinking)** — the budget is validated below `max_tokens`; a response containing a known
  thinking string yields a canonical response **without it**, and that string appears in no audit
  row, log record or span attribute. Written to fail first against a mapper that concatenates all
  content blocks — which is the obvious implementation and the wrong one.
- **Unit (structured output)** — a `FRD-112` schema becomes a tool definition with `tool_choice`
  set; the tool input becomes the response body; a schema field with no faithful equivalent is
  **refused**; a model answering with text instead of the tool is an error, not prose.
- **Unit (streaming)** — typed events assemble into canonical chunks; `thinking_delta` discarded;
  usage accumulated across `message_start` and `message_delta` (written to fail against a
  last-event-wins implementation).
- **Unit (no embed)** — refused by capability before dispatch; the adapter's backstop never reached
  in the normal path.
- **Integration** — against the real project: a Gemini and an Anthropic model answer the same
  canonical request; both audit rows carry correct usage, cost and region; a fallback chain
  containing both works in either order.
- **Mutation** — thinking blocks are actually dropped; `max_tokens` is actually always sent; the
  budget check actually compares against `max_tokens`; the tool-call path is actually forced.

**Acceptance**
- *Given* a use case whose fallback chain is `[claude-…, gemini-…]`, *when* the first is
  unavailable, *then* the second answers and the caller cannot tell from the response shape which
  did.
- *Given* an Anthropic model with thinking enabled, *when* a caller asks a question, *then* the
  response contains only the answer, the recorded output tokens include the thinking tokens, and
  no thinking text exists anywhere in the system.

## 11. Dependencies & Risks

- **`FRD-115`** (transport, auth, region) — hard prerequisite. **`FRD-114`** for publisher,
  capabilities and the default output cap. `FRD-110`–`FRD-113` for what is being mapped.
- **Risk — the canonical core turns out to be Gemini-shaped.** §5.2. The mitigation is to notice it
  and fix the core rather than to add a vendor-specific field; the architecture assertion in
  `FRD-115` §10 is the tripwire.
- **Risk — `anthropic_version` and available models on Vertex change.** Both are configuration
  (`FRD-114`, `FRD-115` §5.6), not code.
- **Closed 2026-08-06 (`ADR-0013`)** — direct model access against the Vertex publisher endpoints,
  which is what §5 assumes throughout. The platform's agent surface is out of scope.

## 12. Rollout / Demo

A second mock publisher that returns Anthropic-shaped bodies, so the whole mapping — including the
thinking-drop and the forced-tool path — is exercised hermetically and demonstrable in demo mode
without a GCP project. That mock is also what makes the fallback-across-vendors acceptance test
runnable in CI.
