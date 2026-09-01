# FRD-131 — Tool calling: carried, never executed, and off until somebody turns it on

> Phase: 9 · Status: **Done** · Owner: Vadim Scheibe
> Related: `ADR-0013` (scope), `ADR-0011` (transport × dialect), `ADR-0012` §3 (dispatch
> conditions), `FRD-110` (ordered parts), `FRD-112` (structured output), `FRD-122` (audit),
> `FRD-124` (nothing is silently dropped), `FRD-125` (the injection filter)

## 1. Summary

A coding assistant's entire loop is tool calling. It asks the model *"which file should I read"*,
gets back a function call rather than prose, executes it **itself**, and sends the result back as
the next turn. AIRA answers such a request with **400** today: `tools` and `toolConfig` are refused
by name at the Gemini surface.

This FRD makes the gateway **carry** a tool declaration to the model and **carry the model's tool
call back** to the caller. It never executes anything — that stays out of scope and always will.
The capability is **per use case and off by default**, because a use case that only summarises
documents has no business declaring functions, and the smallest set of use cases that need it is
the right set to have it.

### 1.1 The refusal is right, its stated reason is wrong

`gateway/src/aira_gateway/api/gemini/schemas.py` refuses `tools` with:

> this gateway provides direct model access and does not execute tools (ADR-0013)

But `ADR-0013` says the opposite about declarations:

> **Tool and function execution.** The gateway may pass a tool definition through … but never
> *executes* anything. Executing a caller's tool would make the gateway a code-execution service
> inside the credential boundary.

Passthrough is **explicitly in scope**; execution is not. The message conflates the two, and a
reader arriving at that line concludes the whole area is closed by decision. The real reason the
field is refused is different and is written down nowhere: **`CanonicalRequest` has no field a tool
declaration could travel in.** That is a capability gap, and a capability gap gets built; a scope
decision does not. Keeping the two apart is the point of having `ADR-0013` at all.

Refusing rather than ignoring was still correct — `FRD-124`'s rule. This FRD replaces the refusal
with the capability and corrects the message.

## 2. Goals & Non-Goals

**Goals**

- A caller may declare tools; the model may answer with a tool call; the caller executes and sends
  the result back as a further turn.
- Works on **every** dialect the gateway serves, or the candidate is skipped by name.
- Off per use case by default; enabling it is a recorded configuration change.
- A tool call is on the audit row, because it is a decision the model made.

**Non-Goals**

- **Executing anything.** Not the caller's tools, not a sandbox, not a shell. `ADR-0013`.
- **Holding conversation state.** The caller sends the whole exchange each turn, tool results
  included, exactly as today.
- **Agent orchestration.** No loop, no step budget, no planner. The loop is the client's; the
  gateway sees N independent requests and governs each one.
- **A built-in tool catalogue.** Tools are the caller's; the gateway has no opinion about what
  `read_file` does.

## 3. User Stories

- As a **use-case administrator**, I want to enable tool calling for the one use case that runs a
  coding assistant, so that the other eleven cannot declare functions at all.
- As a **developer**, I want OpenCode to work against the gateway, so that my assistant's traffic
  is budgeted, rate-limited and auditable like everything else.
- As **IT Security**, I want a tool call to appear in the audit trail, so that "which functions did
  the model ask to run" is answerable after the fact.
- As a **use-case user**, I want a request refused by name when the routed model cannot do tool
  calling, so that I never receive prose where a function call was expected.

## 4. Functional Requirements

- **FR-1** `CanonicalRequest` carries a list of **tool declarations** (name, description, parameter
  schema). The schema reuses `FRD-112`'s parser and its bounds — it is caller-supplied structure
  with caller-controlled recursion, and the same ceilings apply for the same reason.
- **FR-2** `CanonicalMessage` carries two further part kinds: a **tool call** (assistant → caller:
  name + arguments + id) and a **tool result** (caller → assistant: id + content). `FRD-110` made
  parts ordered; this is the second tenant of that structure and must not need a third shape.
- **FR-3** A **use-case toggle**, `tools_enabled`, **default `false`**. A request declaring tools
  against a use case that has not enabled them is refused **400 FAILED_PRECONDITION**, naming the
  use case and who can change it. Absence of configuration is not permission — the same rule as
  `FRD-114`'s catalog.
- **FR-4** A **catalog capability** `tools`. Undeclared means unsupported (`ADR-0012`). A candidate
  that cannot do tool calling is **skipped** by the dispatch chain with its reason kept, and an
  exhausted chain raises `NoCapableModel` → **400 FAILED_PRECONDITION**. Checked **per hop**, for
  the reason attachments are (`FRD-110`): a fallback that quietly answers without tools returns a
  confident 200 to a client that will then parse prose as a function call.
- **FR-5** Every dialect maps it, or says it cannot:
  - **Gemini**: `tools[].functionDeclarations`, response `functionCall`, request `functionResponse`.
  - **Anthropic**: `tools`, `tool_use` / `tool_result` blocks. **Interacts with `FRD-119` §5.5**,
    where structured output is already implemented as a *forced tool call* — a request asking for
    both a response schema and tools cannot be served on this dialect and is refused by name rather
    than silently losing one of them.
  - **OpenAI**: `tools`, `tool_calls`. **Streaming trap**: a tool call's arguments arrive
    fragmented across chunks and must be reassembled; a naive mapper emits several half-calls.
- **FR-6** Streaming carries tool calls. A tool call split across chunks is emitted **once, whole**.
- **FR-7** The audit row records, through `FRD-122`'s **allow-list**, the tool **names** the model
  asked for and how many. **Not the arguments** by default: they are caller content and belong
  under `store_payloads` and `FRD-406`'s redaction, not in a metadata column that no retention
  clock covers.
- **FR-8** Tool declarations count toward the reservation. They are input tokens — a large tool
  schema is a large prompt, and `FRD-405`'s reservation must not under-count it.
- **FR-9** The injection filter's blind spot is **documented and surfaced**, not silently accepted
  (§8).

## 5. Design & Architecture

The shape is `FRD-110`'s, one layer up: a new part kind in the canonical message, a mapping per
dialect, a capability in the catalog, a condition in the dispatch chain.

```
caller ──declares tools──▶ surface (parses, bounds)
                             │
                    pre-dispatch gate  ── tools_enabled? ──▶ 400 if not
                             │
                        pipeline (see §8 — .text is lossy here too)
                             │
                    dispatch chain ── candidate declares `tools`? ──▶ skip, keep the reason
                             │
                          dialect ──▶ upstream
                             │
                    ◀── tool call ── carried back verbatim, never executed
```

**No new endpoint and no new surface.** This is a field on the existing request, which is what
makes it cheap relative to its value — and what keeps `prepare_for_dispatch` (`FRD-126`) the single
owner of the order.

## 6. Data Model

- `use_cases.tools_enabled` (bool, default `false`) in Management, distributed over
  `aira.usecases`, into the gateway read-model. Migration on both sides.
- `model_catalog.capabilities.tools` (bool) — the existing capability structure, one more flag.
- `request_logs.tool_calls` (text/JSON, nullable) — names and count only, per FR-7.

## 7. API / Interface Contract

The Gemini shape, unchanged from Google's, because a caller's SDK already speaks it:

```jsonc
// request
{"contents": [...],
 "tools": [{"functionDeclarations": [{"name": "read_file", "description": "…",
                                      "parameters": {"type": "object", "properties": {…}}}]}]}
// response
{"candidates": [{"content": {"parts": [{"functionCall": {"name": "read_file",
                                                         "args": {"path": "src/main.py"}}}]}}]}
```

Refusals, each naming what to do:

| Situation | Status | Message names |
|---|---|---|
| use case has not enabled tools | 400 `FAILED_PRECONDITION` | the use case, and that an administrator enables it |
| no candidate declares `tools` | 400 `FAILED_PRECONDITION` | the models tried and why each was skipped |
| tools **and** a response schema on Anthropic | 400 `FAILED_PRECONDITION` | that this dialect implements schemas *as* a tool call |

## 8. Security & Privacy

**The injection filter cannot see a tool result, and that is the interesting risk.** `FRD-110`
recorded that `.text` became lossy — an injection inside a PDF is invisible to the filter. Tool
calling makes that sharper: a tool result is **content the caller's own machine produced**, which
the model then reads and acts on. A file the assistant read, a web page it fetched, a command's
output — each is an injection vector into a model that is about to propose the next command.

Three answers, none of them "trust it":

1. The filter's `scope` gains `tool_results`, so a use case may put returned content through the
   same check as user text. Off by default, because it doubles the classifier cost on a request
   type that already makes many calls (`FRD-125b`).
2. `FRD-122`'s row records the tool names, so "the model kept asking to read `/etc/shadow`" is
   answerable.
3. The FRD says plainly that a governed *model* is not a governed *agent*: the gateway bounds what
   the model is asked and what it costs, and cannot bound what the caller does with the answer.
   That boundary is `ADR-0013`, and this feature does not move it.

**Least privilege is the toggle.** Default off, per use case, enabled by a use-case administrator,
distributed like every other config change and therefore visible in the console and in the event
log. A gateway where every use case may declare functions is one where the blast radius of a
compromised key includes function calling everywhere.

## 9. Observability

- `aira.tools.offered` (count) and `aira.tools.called` (count) as span attributes, so the
  proportion of turns that produce a call is visible — the number that tells you whether an
  assistant is working or looping. A third, `aira.tools.names`, carries the names where there was a
  call.
- **The span attribute is `offered`; the audit row's key is `declared`.** This section said
  `aira.tools.declared` until 2026-08-31, which is a name nothing sets — so a reader building a
  panel from this document got an empty one, and an empty panel reads as *"nothing happened"*
  rather than as a wrong query. The two words are deliberate and stay: the row is what the request
  *declared*, the span is what it *offered* to the model.
- A skipped candidate keeps its reason on the row, as `ADR-0012` §3 already requires.

## 10. Testing & Acceptance Criteria

Hermetic first, and then the layer that has found what the others could not:

- **Unit**: the canonical part kinds; each dialect's mapping in both directions; the streaming
  reassembly (asserted on a *fragmented* stream, because a whole-chunk fixture would pass against
  the naive mapper); the per-hop capability check; the toggle.
- **Mutation**: the toggle (default off), the per-hop check, the reassembly, the Anthropic
  schema/tools conflict.
- **Integration** (`FRD-129` style, against the running Ollama): a real two-turn exchange —
  declare a tool, receive a call, send a result, receive an answer — with the audit rows checked in
  Postgres, not in the response.
- **e2e**: the use-case toggle in the console, and a request refused while it is off.

**Acceptance**

- *Given* a use case with `tools_enabled=false`, *when* a request declares tools, *then* 400 naming
  the use case, and an audit row recording the refusal (`FRD-122`).
- *Given* it is enabled and the routed model declares `tools`, *when* the model answers with a call,
  *then* the caller receives the call verbatim and the row records its name.
- *Given* a fallback chain whose first candidate lacks `tools`, *when* a tool request arrives,
  *then* the chain skips it, the row names the skip, and the answer comes from a capable model —
  **never** an answer without tools.

## 11. Open questions

- **Parallel tool calls.** All three vendors can return several calls in one turn. Carried as a
  list from the start; the alternative is a second shape later.
- **Whether `tools_enabled` should also gate the *response*.** A model could in principle emit a
  call unprompted. It cannot without a declaration, so this is theoretical — noted rather than
  handled.
