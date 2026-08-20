# FRD-135 — A model's reasoning: counted always, shown when a use case says so

> Phase: 8 · Status: **Draft** · Owner: Vadim Scheibe
> Related: `FRD-111` (thinking control), `FRD-119` §5.4 (the decision this revises),
> `FRD-133` (cache tokens — the same invariant), `ADR-0016` (why stored content is gated),
> `FRD-122` (the audit row), `FRD-406` (stored prompts)

## 1. Problem

Two problems, and only one of them is a feature.

**The reasoning is not counted.** `_usage_of` reads `promptTokenCount` and `candidatesTokenCount`
and ignores `thoughtsTokenCount`. Google bills thinking at the **output** rate, so every request to
a thinking model is under-reported and every budget under-charged. Measured on 2026-08-17 against
`gemini-2.5-flash`:

```
Google: prompt=25  candidates=1  thoughts=143  total=169
AIRA:   prompt=25  completion=1              →  26 of 169
unrecorded: 143 tokens — 85% of what Google counted
```

That is not a rounding error in a cost-control product, it is the number being wrong in the
expensive direction — the same sentence `FRD-133` used about cache tokens, for the same reason.

**The reasoning cannot be seen.** `FRD-119` §5.4 decided thoughts are never returned, logged or
persisted; the Gemini surface refuses `includeThoughts: true` **by name** rather than answering 200
with no thoughts, and the Anthropic mapper drops `thinking` blocks. That was the right default and
it is the wrong absolute: for an agent, the reasoning is the most informative thing it produces,
and an installation watching its own agents has no way to see why a model did what it did.

## 2. Goals & Non-Goals

**Goals**
- Thinking tokens are **counted and billed** wherever a provider reports them. Unconditional, not
  a setting: this is an accounting defect, not a preference.
- A **use case** may switch reasoning on. When it is on, thoughts come back to the caller and are
  stored **exactly like the answer** — same column, same retention, same role gate.
- The switch is in the console, like every other use-case control.

**Non-Goals**
- Reasoning on by default. Off, everywhere, until somebody decides otherwise (`ADR-0016`).
- A second storage path. If thoughts are stored, they are stored the way the response is; a
  parallel mechanism would be a parallel retention bug.
- Reconstructing reasoning a provider does not return (self-hosted runtimes that report nothing).

## 3. User Stories
- As **IT Steuerung**, I want the token figures to match the provider's bill, so that a budget means
  what it says.
- As a **use-case administrator** running agents, I want to see the model's reasoning in a trace, so
  that "why did it call that tool" is answerable.
- As **IT Security**, I want reasoning to be off unless somebody turned it on, and to be readable
  only by whoever may read stored prompts.

## 4. Functional Requirements

- **FR-1 Counted, always.** `CanonicalUsage.reasoning_tokens`, populated from the provider's own
  figure. **A subset of `completion_tokens`, never an addition** — the invariant `FRD-133` set for
  cache tokens, and the reason is the same: the total a request consumed is one number, and keeping
  it whole is what lets every existing budget, report and index carry on meaning the same thing.
  `completion_tokens` therefore *includes* thinking, which is what the provider bills.
- **FR-2 On the audit row and in reporting**, beside the other token columns.
- **FR-3 A use-case switch**, `include_reasoning`, default **off**. Carried to the gateway on the
  config event like every other use-case setting.
- **FR-4 Off means refused, not dropped.** With the switch off, `includeThoughts: true` is refused
  by name exactly as today (`FRD-124`): answering 200 with no thoughts is the silent drop.
- **FR-5 On means returned and stored like the answer.** The thoughts travel in the response and
  are written into the stored response payload — same column, same `store_payloads` gate, same
  retention, same role check on reading (`ADR-0016`, `FRD-406`). No second path.
- **FR-6 The console offers it** on the use case, beside `store_payloads` and `tools_enabled`.
- **FR-7 Where a provider returns no reasoning**, the switch changes nothing and says so: a use case
  with reasoning on, calling a model that reports none, is not an error.

## 5. Design & Architecture

### 5.1 Why counting is not a setting

An installation can decide whether it wants to *see* reasoning. It cannot decide whether it was
*charged* for it. Making FR-1 conditional would mean two deployments of the same product disagreeing
about what a request cost, which is the one thing a governance layer may not do.

### 5.2 The subset invariant, stated once

```
prompt_tokens     ⊇ cached_input_tokens, cache_write_tokens
completion_tokens ⊇ reasoning_tokens
```

Every price, budget and report already reads the two totals. Adding a subset field changes no
arithmetic anywhere; adding a *sibling* field would have required finding every place that sums
them, and missing one is how a cost feature becomes wrong quietly.

### 5.3 Storage is the response's storage

Reasoning is content, of exactly the kind `ADR-0016` reasoned about: the sensitive part and the
useful part are the same part. So it is not given a column of its own, a flag of its own, or a
retention rule of its own — it goes where the answer goes, and inherits the gate, the sweep and the
read-audit that already exist. A use case that does not store payloads does not store reasoning
either, and that follows without a line of code.

### 5.4 Every dialect, or it is not a use-case switch

FR-5 says *returned*, without naming a vendor, and for a fortnight it was implemented on one. The
switch reached the canonical request through the whole pre-dispatch sequence and **only the Gemini
mapper read it**: a use case with reasoning on that routed to a Claude model on Vertex, or to any
server speaking the OpenAI dialect, was answered `200` with no thoughts and no explanation — FR-4's
silent drop arriving through the door FR-5 was supposed to open.

Both halves were invisible from the console, because the *counting* was right on all three
(`reasoning_tokens` is read from every dialect, FR-1 being unconditional): the reporting screen
showed thinking being paid for that no answer ever carried.

The three dialects differ in one way that decides the shape of the fix:

| dialect | where the reasoning is | returned unasked? |
| --- | --- | --- |
| Gemini | `parts[].thought: true` | **no** — it is only sent when the request asks |
| Anthropic | `content[].type == "thinking"` | no — only with a thinking budget set |
| OpenAI | `message.reasoning` | **yes** — a reasoning model thinks with no parameter at all |

So the Gemini mapper can read it unconditionally and the other two cannot: an unconditional read on
the OpenAI dialect returns a chain of thought to every use case that never asked, into a response
this gateway also persists (§8). Both mappers therefore take the switch as an argument that
**defaults to off**, so a call site that forgets it withholds rather than discloses, and the
adapters pass `request.include_reasoning` — one wire each, and the mutation harness guards both,
because a dropped argument is invisible: the answer is simply always withheld, which looks exactly
like the feature being off.

**Streams return no reasoning on any dialect** and that is unchanged: `CanonicalChunk` has no
channel for it, so there is nothing dialect-specific to state.

### 5.5 What this revises

`FRD-119` §5.4 said reasoning is never returned, logged or persisted. It is now: **never, unless a
use case says otherwise, and then like any other content.** The refusal of `includeThoughts` stays
exactly as it is wherever the switch is off — that behaviour was right and is kept, not softened.

## 6. Data Model

- `usecases.include_reasoning` (boolean, default false) on both planes, on the config event.
- `request_logs.reasoning_tokens` (integer, nullable) — nullable because every row written before
  this existed knows nothing, and zero would claim the model did not think.

## 7. API / Interface Contract

- Gemini surface: `thinkingConfig.includeThoughts` accepted when the use case allows it; refused by
  name otherwise, unchanged. Honoured on **every** upstream dialect (§5.4), not only Google's.
- KIRA surface: unchanged — the contract has no field for it, and inventing one is not compatibility.
- Reporting: one more token column.

## 8. Security & Privacy

- Off by default, per use case, and readable only through the paths stored prompts already use.
- Reasoning can restate the prompt verbatim; nothing here may make it reachable to somebody who may
  not read the prompt itself.

## 9. Observability

- `reasoning_tokens` on the row and in the report, so "what did thinking cost us" is answerable per
  use case and per model — the question that motivated FR-1.

## 10. Testing & Acceptance Criteria

- A provider response carrying thoughts produces `completion_tokens` **including** them and
  `reasoning_tokens` equal to them; cost equals the output rate applied to the whole.
- With the switch off, `includeThoughts: true` is refused by name.
- With it on, thoughts reach the caller and appear in the stored payload; with `store_payloads` off,
  they appear nowhere.
- A model that reports no reasoning with the switch on is served normally.
- **Each dialect, both directions** (§5.4): with the switch on the thoughts come back, and with it
  off they do not — the second matters more on the OpenAI dialect, which sends them unasked.
- Mutation: dropping `thoughtsTokenCount` from the usage mapping must turn a test red — it is the
  85% that was invisible. Likewise dropping `include_reasoning=` from either adapter's call: a lost
  argument is silent, because withholding is the safe default it falls back to.
