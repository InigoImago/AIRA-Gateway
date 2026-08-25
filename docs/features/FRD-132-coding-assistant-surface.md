# FRD-132 — Which surface a coding assistant needs, measured rather than assumed

> Phase: 9 · Status: **Stage A done — B1: no new surface needed** · Owner: Vadim Scheibe
> Related: `FRD-131` (tool calling — the prerequisite), `FRD-106` (OpenAI surface, withdrawn),
> `FRD-107` (the KIRA surface, as the precedent for adding one), `FRD-123` (the OpenAI dialect,
> already built as an upstream), `ADR-0011`

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

## 10. The configuration button (2026-08-08)

The measurement harness under `tools/opencode/` is a file a developer edits by hand. That is fine
for measuring and wrong for using: the person who needs it is the one who has just issued a key,
and they have the key for **one moment**.

So the config is generated **at issuance**, on the API-keys panel, with *copy* and *download*:

- **It carries the plaintext key**, which is the only reason the timing matters. Offered on any
  later screen — the key list, a settings page — it could only contain a placeholder, and a
  placeholder is what somebody pastes and then spends twenty minutes debugging. The screen says the
  file *is* a credential.
- **It names only models whose catalog entry declares `tools`** (`FRD-114`, `ADR-0012`: undeclared
  means unsupported). Read from the catalog at load rather than hard-coded, because "declares tool
  calling" is a measured fact that changes. An assistant pointed at a model that answers in prose is
  exactly the failure `FRD-131` exists to prevent, and it fails as a confident wrong answer with a
  200.
- **It disappears with the key.** Pressing *Done* clears the buttons along with the plaintext;
  leaving them would mean the credential is retrievable from a page that has just said it is not.
- If the catalog cannot be read, or declares no tool-capable model, the file still names one and
  stays editable — an empty `models` block is a config that fails with no clue why.

Tested at three layers, deliberately: the unit tests fix the *content* (the key, the base URL, the
model filter, the fallbacks); the e2e test proves the button produces an actual **file** with
parseable JSON in it — the distinction that `FRD-206` shipped a defect on, when an info button was
a `title` attribute and rendered nothing at all while every unit test passed.

---

## 11. The numbers a coding assistant shows, and where they come from (2026-08-25)

> *"When I start OpenCode and connect it to AIRA, I can see that I am talking to AIRA over the
> Gemini interface, but I cannot see how many tokens were used, and the limits are always at 0%.
> Do we have the possibility of supplying that information over the official Gemini interface? The
> interface should stay like the official one — if it cannot be done, it cannot be done."*

Answered by installing OpenCode and reading both sides, because reasoning about it gave the wrong
answer twice.

### 11.1 The tokens were never missing

A real run of `opencode run` against `qwen3:0.6b` through the Gemini surface:

| Source | Figures |
| --- | --- |
| AIRA's audit row | `prompt 2050 · completion 26 · total 2076`, status 200 |
| OpenCode's own store | `{"input": 2050, "output": 26, "total": 2076}` |

`usageMetadata` is emitted on both the buffered and the streamed exit, `@ai-sdk/google` reads it,
and the two planes agree to the token. **Nothing in the accounting was broken.**

The streamed shape is worth recording since it was suspected: with an upstream speaking the OpenAI
dialect, the totals arrive on a trailing chunk with no `finishReason`, and every earlier chunk
carries zeros. That is the dialect's own convention relayed through a Gemini-shaped surface, and it
is *not* what Google does. It was measured as harmless — the SDK takes the last usage it sees — so
it is recorded here rather than changed. **Changing it means holding the finish chunk**, and a
latency cost paid on every stream to tidy a shape no client was misreading is the wrong trade until
one is.

### 11.2 What was actually zero, and why the API could not have fixed it

Asked what it had resolved for the model, OpenCode answered:

```json
"limit": { "context": 0, "output": 0 },
"cost":  { "input": 0, "output": 0, "cache": { "read": 0, "write": 0 } }
```

**OpenCode does not ask the API for any of that.** It reads `limit` and `cost` from its
configuration file — and the file §10 generates carried only a display name. A context gauge is
`used / limit.context`, so it sat at 0% however full the conversation was. The hand-written harness
in `tools/opencode/` has had `"limit": {"context": 32768, "output": 4096}` since the beginning; the
generated one never inherited it.

So the honest answer to *"can we supply it over the official interface"* is that for this client the
question does not arise: the fix is in a file, not in the surface, and the constraint is met by
construction.

### 11.3 The one thing that *is* the interface, and where we had drifted

The official Gemini model resource carries **`inputTokenLimit`** and **`outputTokenLimit`**. AIRA
published the second under an invented name — `airaMaxOutputTokens`, sitting beside the standard one
it was not — and the first not at all. A client written against Google reads neither.

Filling the standard pair is a move **towards** the official shape. Two rules go with it:

- **Absent, never zero.** Google omits a limit it has no figure for, and a `0` is not "unknown" to a
  client — it is a full context window. The list endpoint now dumps with `exclude_none`, which the
  streamed exit has done since `FRD-100` and this one had not: the same fact at two exits
  disagreeing, one more time.
- **The extension stays.** `airaMaxOutputTokens` keeps carrying the same figure. Withdrawing a field
  a caller has read since `FRD-114` is not a tidy-up a compatibility surface gets to perform.

### 11.4 The field that did not exist

Neither plane had a **context window**. It is a fact about a vendor's model that this installation
had never recorded, so `limit.context` and `inputTokenLimit` had nothing to be filled from.

`context_window` is now a catalog field, travelling the ordinary route: the model editor's
capabilities tab → the serializer → the model event → the gateway's read-model → the Gemini model
resource, and into the generated configuration. Nullable, nothing backfilled, and **not enforced**:
the upstream refuses what does not fit, and a second copy of that ceiling here would be one more
thing to keep true. One consistency rule comes with it — `max_output_tokens` may not exceed it,
because the answer is drawn from the same window as the prompt.

The local seed declares `40960` for `qwen3:0.6b`, which is the figure its comment had already been
explaining: on that runtime the window *is* the output ceiling, and until now there was nowhere to
say so.

### 11.5 What the pass found on the way

- **Nothing compared the two halves of a model event.** Management's `_payload` and the consumer's
  `_DECLARATION_DEFAULTS` are hand-written lists in different languages, and adding a field to one
  and forgetting the other is completely silent: the console offers it, the database stores it,
  Kafka carries it, the read-model does not have it. `tools/tests/test_a_model_event_is_applied_whole.py`
  now fails in **both** directions — a published field nobody applies, and a default nobody sends.
- **The price unit was stated two ways**, now one — see §11.6.
- **`thoughtsTokenCount` was sent as `null`** on the buffered exit — see §11.6.

### 11.6 The two the pass left behind (2026-08-25)

Both were recorded above as noticed-and-not-fixed. Both are fixed now, and both turned out to be
the same shape as the section they were found in: **a fact with two statements of it.**

#### The currency had one setting and three contradictions

`AIRA_CURRENCY` documents itself as *"currency all prices and cost budgets are expressed in"* and
defaults to `EUR`. It had **exactly one reader in the whole system** — the reporting CSV's column
header, on the gateway. Meanwhile three console screens said *US dollars* in so many words: the
model catalog's price paragraph, the use-case budget window, and the installation's. A German
installation on defaults therefore invited somebody to type dollars into a form and handed them back
a file that said euros.

The console's argument for hard-coding it was written down — *"every provider on this gateway prices
in dollars"* — and it is a claim about **vendors**, not about an installation. A reseller contract in
euros makes it false and nothing would have said so.

So `/v1/me` carries the currency, beside the API-key policy that is already there for exactly this
reason (*"so the console states the numbers the server enforces instead of carrying its own copy"*),
and `MeService` holds it as a signal the three screens read. Empty until the first response, and an
empty currency renders **no unit at all**: an unlabelled amount is a reader who asks, a wrongly
labelled one is a reader who does not.

The generated OpenCode configuration takes the same fact and uses it to decide something different:
prices are written **only when the installation prices in USD**, because OpenCode prints the running
total with a `$`. No conversion — `AIRA_CURRENCY`'s own comment refuses exchange rates for the reason
they are refused everywhere here, that a rate needs a date per booking. Absent is the honest answer,
and OpenCode then shows no cost, which is true.

*Found on the way:* seven spec files stub `MeService`, and every one of those doubles was **less
capable than the thing it stands in for** — the moment the service grew a member, 144 tests failed
on a template error a long way from what any of them was testing. `CLAUDE.md` §3 warns about a
stand-in that is *more* permissive; this is the same trap facing the other way.

#### `thoughtsTokenCount` was invented as a null

`UsageMetadata.thoughtsTokenCount` documents itself as *"omitted when zero rather than sent as `0`,
because Google omits it for a model that did not think and a compatibility surface should not invent
a field the original leaves out"* — and the buffered exit sent `"thoughtsTokenCount": null`, which is
the same invention wearing a different value.

The existing test dumped a hand-built `UsageMetadata` with `exclude_none` and checked the key was
gone. That proves the *schema* can do it. Its own comment records the previous version of this
mistake — *"the first version of this asserted that the field exists and is omitted when empty —
both true with the mapping handing over `None`, so the mutation that stopped filling it survived"* —
and this is that lesson one level further out: what a caller receives is decided by the **route**, so
the route is what is asked now, through a real request.

`exclude_none` on that exit reaches the rest of the response too, and every case is Google's own
shape: a text part carries no `functionCall: null`, and a candidate carries no key for what it did
not do. It is the **third** exit of this file to need saying so — the streamed one since `FRD-100`,
the model list since §11.3 — which makes "a fact stated at one exit and missing from another" this
file's oldest recurring shape rather than an observation about any one of them.
