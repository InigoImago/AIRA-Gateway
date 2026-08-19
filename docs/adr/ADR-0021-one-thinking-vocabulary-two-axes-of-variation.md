# ADR-0021 — One thinking vocabulary, two axes of variation

- **Status:** Proposed
- **Date:** 2026-08-19
- **Deciders:** Vadim Scheibe

## Context

Reasoning control is where the vendors agree least, and the disagreement grows with every family.
Asked plainly by the owner: *"what will this look like when the Anthropic models arrive, and then
Foundry and Azure OpenAI? We need a strategy."*

Measured rather than assumed, against Vertex on 2026-08-19:

| | `gemini-2.5-flash` | `gemini-2.5-pro` |
| --- | --- | --- |
| budget `0` (stop thinking) | accepted | **refused** — *"does not support setting thinking_budget to 0"* |
| smallest accepted budget | `1` | **`128`** |
| largest accepted budget | **`24576`** | **`32768`** |
| `-1` (model decides) | accepted | accepted |

Two models, one family, one platform, one afternoon — and one of them cannot be told to stop. The
budget is also a **ceiling and not a target**: `gemini-2.5-pro` thought 59 tokens at a budget of
`128` and 59 at `32768`.

Across the vendors already in scope the disagreement is larger still:

| Dialect | How thinking is asked for | What it cannot say |
| --- | --- | --- |
| Gemini | `thinkingConfig.thinkingBudget` — `0` off, `-1` auto, else a count | — |
| Anthropic | `thinking{type:"enabled",budget_tokens}` | no `auto`: nothing means "decide" |
| OpenAI / Azure / Foundry | `reasoning_effort` — a word | no `limited`: there is no budget to name |

So the variation has **two axes, and they are different things**:

1. the **shape** — a token budget or an abstract word. A property of the *dialect*.
2. the **envelope** — which modes a model offers, what each level costs, the floor and ceiling. A
   property of the *model*.

They were not treated as two. The envelope lived in the catalogue (rightly), and the shape lived in
scattered `if` branches inside each mapper — with no statement of what a dialect can express and no
check that a new one had answered the question. The cost was measurable:

- the OpenAI dialect sent `minimal` as the literal `"minimal"`, which exists on one vendor's newest
  family and makes every other server answer `400 invalid value`;
- the Anthropic dialect, asked for `auto` or `high` with no resolved budget, **omitted the thinking
  field entirely** — a body byte-for-byte identical to `disabled`, answered `200`, with nothing
  anywhere saying the caller did not get what they asked for;
- the catalogue accepted `disabled` beside a floor of `128`, a level of `100` under that floor, a
  budget for a level the model does not offer, and levels with a ceiling and no table — which sends
  every level as the ceiling and makes `low` and `high` the same instruction.

## Options considered

- **A vocabulary per vendor.** Honest to each provider and it moves the problem to every caller:
  the use case UI, the audit vocabulary and the KIRA contract would all have to grow a vendor
  dimension. Rejected — the point of a gateway is that a use case does not know which vendor
  answered.
- **The lowest common denominator** (offer only what every vendor has). Loses `limited` entirely
  and loses the budget on the two dialects that take one, which is most of the control this feature
  exists to give.
- **One abstract vocabulary, translated per dialect, bounded per model** — extended so that both
  axes are *declared* rather than implied, and checked.

## Decision

Keep the canonical vocabulary — `mode` plus optional `tokens` — and make **both axes declarations
that something checks**, mirroring what `sampling_controls` already does for the other family of
vendor differences.

1. **The dialect declares what it can express.** `Upstream.thinking_modes`, per adapter, never
   defaulted to "all". `test_every_adapter_declares_its_thinking_support` fails when a new adapter
   is silent, at the point somebody can still choose the right answer.
2. **A mode a dialect cannot express is refused by name, never omitted.** Silence is the one answer
   a control plane may not give about a control.
3. **The model declares its envelope**, and the catalogue refuses an envelope that contradicts
   itself: `disabled` beside a non-zero floor, a level outside the floor or ceiling, a level the
   model does not offer, and abstract levels with a ceiling and no table.
4. **Callers never see a token count.** A use case picks `low`/`medium`/`high`; the numbers live in
   the model's catalogue entry and nowhere else. This is the interface that must stay still while
   the vendors move.

A new *model* is therefore a catalogue entry. A new *vendor* is one adapter plus one declaration.
Neither is a change to the vocabulary, and neither can be finished without answering the question.

## Consequences

- **Positive.** The failures above become impossible in the same shape: a dialect that cannot say
  something has to say so, and a model whose numbers contradict its platform is refused where the
  numbers are typed rather than at a caller's request. Adding Foundry's models needs no new
  concepts — `reasoning_effort` is already declared as the shape without `limited`.
- **Negative.** The abstract levels are approximations, and this ADR does not pretend otherwise:
  `minimal` reaches an OpenAI-compatible server as `"low"` because `"minimal"` is not a value that
  dialect has. Approximation is allowed **only where the caller named no number** — `limited` stays
  refused, because there the caller named one and rounding it would spend a different amount with
  nothing to show it.
- **Negative.** The envelope is typed by hand and can be wrong in a way nothing catches: internal
  consistency is checked, agreement with the vendor is not. The follow-up below is the answer.
- **Follow-up — measure the envelope instead of typing it.** The numbers in the table above were
  obtained in about ten small requests: budget `0`, `1`, the floor, the ceiling. The console
  already has *"Check reachability"* (`FRD-506`), a button that informs without blocking; the same
  shape can fill `min_tokens`, `max_tokens` and whether `disabled` is available. That turns a
  declaration from something copied out of documentation into something measured, which is the rule
  this project already applies to every other capability.
- **Follow-up.** Say in the console that a budget is a **ceiling, not a spend**. Measured above and
  genuinely surprising: choosing `high` does not buy a more expensive answer, it permits one.
