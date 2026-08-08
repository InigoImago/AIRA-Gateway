# FRD-132 — Which surface a coding assistant needs, measured rather than assumed

> Phase: 9 · Status: **Stage A done (2026-08-08) — B1: no new surface needed** · Owner: Vadim Scheibe · Last updated: 2026-08-08
> Related: `FRD-131` (tool calling — the prerequisite), `FRD-106` (OpenAI surface, withdrawn
> 2026-08-07), `FRD-107` (the KIRA surface, as the precedent for adding one), `FRD-123` (the
> OpenAI dialect, already built as an upstream), `ADR-0011`

## 1. Summary

Connecting coding assistants — OpenCode first — is a named use case. `FRD-131` makes the *feature*
possible; this FRD answers the question one step out: **does such a client need a surface we do not
have?**

**Answered on 2026-08-08 by running it — see §9. The Gemini surface serves OpenCode; no new
surface is needed, and the only gap is tool calling (`FRD-131`).** The reasoning that got us to
measure rather than choose is kept below, because it is why the answer is trustworthy.

The honest answer *before that run* was nobody here knows, and this project has a scar for exactly
this shape.
`FRD-124` found eleven request fields that returned 200 and did nothing, and every one of them was
found by **sending a request**, not by reading code. `FRD-125` found a filter that was configured,
displayed and inert. The `FRD-406` review found a use-case bypass that four green verification
layers had missed and one live request made obvious. Choosing between "the existing Gemini surface
is enough" and "build an OpenAI-compatible surface" from documentation would be the same mistake in
a more expensive place: a surface is a **contract**, and one built on a guess is one that is
maintained forever.

So stage A is a **measurement**, and it is deliberately cheap. Stage B is whatever the measurement
says.

## 2. Goals & Non-Goals

**Goals**

- A written record of what a real coding assistant actually sends and expects, against the running
  gateway with a real model behind it.
- A decision on the surface question, with the evidence attached.
- If a surface is needed: it reuses the existing OpenAI **dialect** rather than restating it.

**Non-Goals**

- Building a surface before stage A. That is the entire point.
- Supporting every coding assistant. OpenCode is the named client; anything else is a bonus that
  falls out of the same contract.
- An "agent mode" in the gateway. `ADR-0013` — the loop is the client's.

## 3. Stage A — the measurement

Run OpenCode against the running stack with the local Ollama model behind it (`FRD-123`,
`AIRA_OPENAI_SERVERS`), and write down what happens. That is a host-side session: OpenCode is
configured on a workstation, the gateway is reachable, and the point is to watch a **real client**
meet a **real server**.

What is recorded, per attempt:

| Question | Why it decides something |
|---|---|
| Which provider shape can OpenCode be pointed at, and can its `baseURL` be overridden? | If a Google-shaped provider with a custom base URL works, no new surface is needed at all. |
| Does it reach `:generateContent` / `:streamGenerateContent` unmodified? | Path shape and streaming envelope are the two things a client cannot adapt to. |
| Which auth header does it send? | `x-goog-api-key`, `Authorization: Bearer`, or `?key=` — all three are already accepted. |
| Where does the **first** request fail, exactly? | A refusal that names the field is the measurement; a 200 that does nothing is the failure this FRD exists to avoid. |
| Does it send fields we refuse (`safetySettings`, `cachedContent`, `candidateCount`)? | `FRD-124` made those refusals deliberate; a real client hitting one is evidence about whether the refusal is right. |
| How many requests per user instruction, and how many tokens? | Calibrates `FRD-405` limits and `FRD-400` budgets for this use-case shape (see §5). |

**Deliverable**: a section appended to this FRD with the actual client output — statuses, messages,
counts. Not a summary of them.

**Stage A does not need `FRD-131`.** Reaching the surface, authenticating and streaming can all be
measured before tool calling exists; the run simply stops at the first tool call, which is itself a
useful, dated data point.

## 4. Stage B — the decision, with its three possible answers

**B1 — the Gemini surface is enough.** OpenCode is pointed at it, tool calling arrives with
`FRD-131`, and nothing else is built. Cheapest by far, and the outcome to hope for.

**B2 — an OpenAI-compatible surface is needed** (`/v1/chat/completions`, `/v1/embeddings`,
`/v1/models`). This is `FRD-106`, withdrawn on 2026-08-07 as "a thought experiment about
generalisation" — and a named client that cannot be served without it is precisely the evidence
that was missing then. Reviving it is **much cheaper than it was**: `FRD-123` built the OpenAI
dialect as an *upstream*, so the request and response mappings already exist and a surface is
largely their inverse. `FRD-107` is the precedent for how a second surface is added without a
second copy of the controls: `api/serving.py` owns the pre-dispatch gate, the pipeline, the
dispatch chain and the audit writer, `prepare_for_dispatch` owns the order, and
`test_surface_layering.py` fails a surface that calls a step directly.

**B3 — an Anthropic-compatible surface is needed.** Same structure, and the Anthropic dialect
already exists too (`FRD-119`). Only if stage A shows the client cannot be pointed elsewhere.

The decision is recorded as an ADR amendment, because "we un-withdrew `FRD-106`" needs its reason
attached.

## 5. What a coding assistant does to the governance model

This is worth writing down before anything is built, because it is not a bug in either party.

**An assistant makes many model calls per human instruction.** One "fix this test" can be twenty
requests: read files, propose, run, read output, correct. That is the `FRD-125b` problem at scale —
there, one caller request made two model calls and left one audit row, and the fix was to record
each call and book it with `requests=0` so a caller's *request count* stayed truthful. Here the
calls are the **caller's own**, so they are genuinely twenty requests. Consequences:

- **Rate limits calibrated for a chatbot will trip immediately.** A per-minute request bucket sized
  for a human typing is wrong by an order of magnitude for an assistant. This is configuration, not
  code — but a use case created from the chatbot template will fail confusingly, so the demo seed
  should carry an assistant-shaped example (`FRD-130`).
- **"Requests" means something different on the reporting screen** for this use case. Spend stays
  comparable; request counts do not. Worth a note on the screen rather than a new metric.
- **Token volume is dominated by repeated context.** Which is what `FRD-133` is about, and why that
  FRD is written now and built later: the assistant work must stand on its own without assuming the
  saving.

## 6. Security & Privacy

Nothing here changes the trust boundary, and one thing sharpens it: a coding assistant sends
**source code** as prompt content. That lands in `request_logs` when `store_payloads` is on for the
use case. Two existing controls cover it and both should be named in the use case's setup rather
than discovered later: per-use-case payload storage (`FRD-404`) and retention. `FRD-406`'s
redaction removes credentials from that source — which for a repository is a more common event
than for prose.

## 7. Testing & Acceptance Criteria

- **Stage A acceptance**: this document contains the real client's output, dated, including the
  first failure and its exact message. A stage-A run that produces a summary instead of output has
  not happened.
- **Stage B acceptance** (if a surface is built): `test_surface_layering.py` passes unchanged; an
  audit-row comparison test proves a request through the new surface leaves the **same facts** as
  the equivalent Gemini one — the `test_a_kira_request_is_audited_exactly_like_a_gemini_one`
  pattern, which is the only way to be sure no control was skipped rather than merely present.

## 8. Open questions

- Whether OpenCode's model listing expects fields we do not serve. Stage A answers it.
- Whether the assistant needs `stop_sequences` or `seed` semantics we refuse per dialect
  (`FRD-124`) — a refusal a client cannot work around would change the surface decision.

---

## 9. Stage A: the run (2026-08-08)

Ollama in the stack, `qwen3:4b` pulled because it declares `tools`; the gateway serving it as
`gpu-b`; a use case `coding-assistant` with an API key (30 days, the new default); OpenCode
**1.18.15** installed from npm and pointed at the Gemini surface with `tools/opencode/opencode.json`.

### 9.1 The answer to the question this stage exists for

**B1. The existing Gemini surface serves OpenCode.** No new surface is needed, and `FRD-106` stays
withdrawn. Provider resolution, the `baseURL` override, authentication, model selection, plain
generation and SSE streaming all worked unmodified. The client failed at exactly one thing:

```
$ OPENCODE_CONFIG=.../opencode.json opencode run "Reply with the single word OK."
> build · qwen3:4b
Error: Value error, 'tools' is not served by this gateway: this gateway provides direct model
access and does not execute tools (ADR-0013). Silently ignoring the declaration would return
prose where a function call was expected
```

Reaching that refusal **is** the successful outcome: it means everything up to the missing
capability held, and the missing capability is `FRD-131`, already written.

### 9.2 The wire, measured by hand

Sent directly, so the evidence does not depend on the client:

```
GET /v1beta/models                    -> 200; 5 models
[plain generate]                      -> 200  finishReason STOP, usage reported
[with a system instruction]           -> 200
[with tools]                          -> 400  INVALID_ARGUMENT, 'tools' is not served
[with toolConfig]                     -> 400  same refusal, named
[streaming ?alt=sse]                  -> 200  18 SSE lines
```

The gateway's own access log for the OpenCode run, with the model name percent-encoded by the
client — a colon in a model name survives the round trip, which is the defect `FRD-123` recorded:

```
POST /v1beta/models/qwen3%3A4b%3AgenerateContent        200 OK
POST /v1beta/models/qwen3%3A4b%3AstreamGenerateContent  200 OK
POST /v1beta/models/qwen3%3A4b%3AstreamGenerateContent  400 Bad Request
```

### 9.3 Three requests for one instruction, and every one of them audited

`request_logs`, filtered to the use case:

| operation | outcome | tokens |
|---|---|---|
| `generateContent` | `served` | 176 |
| `streamGenerateContent` | `invalid_request` | — |
| `streamGenerateContent` | `client_gone` | — |

**One trivial instruction produced three gateway requests**, of which one was the assistant's own
housekeeping. §5's warning is now a number rather than a caution: limits and budgets calibrated for
a chatbot are wrong for this use-case shape by a multiple, and the `requests` figure on the
reporting screen counts a different thing here. The refused and abandoned calls are both on the
audit trail, which is `FRD-122` doing its job — a refusal that left no row would have made this
table say the assistant sent one request.

### 9.4 What stage A found that had nothing to do with surfaces

**`reasoning_effort: "none"` does not mean "do not think" — it means "do not emit a separate
reasoning channel", and the two are the same thing only on some models.** Measured on this Ollama,
same minute, one prompt, `max_tokens: 400`:

| | `qwen3:0.6b` | `qwen3:4b` |
|---|---|---|
| field omitted | 115 tokens, content `"OK"`, reasoning separated | 132 tokens, content `"OK"`, reasoning separated |
| `reasoning_effort: "none"` | **3 tokens**, content `"OK."` | **103 tokens, content = 480 characters of raw chain-of-thought** |
| `reasoning_effort: "low"` | 105 tokens, content `"OK"` | 123 tokens, content `"OK"` |
| `reasoning_effort: "minimal"` | — | **400**, `invalid reasoning value` |

The dialect maps `disabled` → `"none"`, on a measurement recorded in the code against the 0.6B
model, where it is correct. On the 4B model of the *same family on the same server* the same
mapping returns somebody's thinking as the answer, billed, with a 200 — and the seed declared
`disabled` as the **default** for whatever model was configured, so it was the ordinary path.

Fixed as data rather than as code: both seeds now key the thinking declaration **by model**, from a
measurement, and a model nobody has measured is declared with no thinking at all (`FRD-114` FR-7).
`qwen3:4b` therefore does not offer `disabled`, and `FRD-111` refuses a request asking for it by
name — which is a far better answer than a 200 carrying reasoning. `minimal` is gone from the
`tools/` seed too; the same correction had been made in the *Management* seed on 2026-08-06 and the
second copy was never updated.

The rule worth carrying: **a capability belongs to a model, not to a family, a vendor or a
runtime** — and a declaration measured against one model is not evidence about its siblings.

### 9.5 Consequently

- Stage B is **not needed**. B1 holds; `FRD-106` stays withdrawn, and this run is the evidence that
  was missing when it was.
- `FRD-131` is the whole of the remaining work for this use case, and its shape is unchanged by
  what was measured here.
- The `tools/opencode/` config and README are kept as the harness for re-running this after
  `FRD-131` lands.
