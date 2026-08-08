# FRD-132 — Which surface a coding assistant needs, measured rather than assumed

> Phase: 9 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-08
> Related: `FRD-131` (tool calling — the prerequisite), `FRD-106` (OpenAI surface, withdrawn
> 2026-08-07), `FRD-107` (the KIRA surface, as the precedent for adding one), `FRD-123` (the
> OpenAI dialect, already built as an upstream), `ADR-0011`

## 1. Summary

Connecting coding assistants — OpenCode first — is a named use case. `FRD-131` makes the *feature*
possible; this FRD answers the question one step out: **does such a client need a surface we do not
have?**

The honest answer today is *nobody here knows*, and this project has a scar for exactly this shape.
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
