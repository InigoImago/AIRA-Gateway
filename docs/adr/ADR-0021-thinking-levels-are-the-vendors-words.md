# ADR-0021 — Three settings we own, and the vendor's own words for the rest

- **Status:** Accepted
- **Date:** 2026-08-19 (superseding this document's own first version, same day)
- **Deciders:** Vadim Scheibe

## Context

Reasoning control is where the vendors agree least. Asked plainly by the owner: *"what will this
look like when the Anthropic models arrive, and then Foundry and Azure OpenAI? We need a
strategy."*

The **first version of this ADR** answered: keep one abstract vocabulary — `disabled`, `auto`,
`limited`, `minimal`, `low`, `medium`, `high` — declare per dialect which of them it can express,
and declare per model what each level costs in tokens. That last part is what this revision
removes, and the objection that removed it came from using the console to catalogue a real model:

> *"If I now pick medium or low, you ask me how many tokens that should be. You do not even find
> these parameters on the vendors' own pages. How am I, cataloguing the model, supposed to know it
> when the vendor never stated it? … Your story with the percentages is nonsense. And on the
> thinking limit, I do not find that on the vendor pages either — for agentic coding it would be
> fatal."*

Every clause is correct, and the last one is the serious one.

**Nobody publishes the number.** Not Google, not Anthropic, not Microsoft. The console's own field
label, read out by a screen reader, was *"How many thinking tokens `medium` means"* — a question
with no source. An intermediate proposal to derive it as a fraction of the model's range was worse,
not better: a fraction of a range is an invented number with a formula in front of it.

**And the number was not inert.** It was sent upstream as `thinkingBudget`, which is a *ceiling on
the model's reasoning*. So a hand-typed `medium = 2000` truncates an agentic run that needed twenty
thousand thinking tokens — a wrong answer with a `200` on it, and nothing anywhere saying why. The
catalogue field could not be filled correctly and did damage when filled incorrectly.

**Meanwhile the vendors moved to words.** Measured against Vertex on 2026-08-19:

| | result |
| --- | --- |
| `gemini-2.5-flash` + `thinkingLevel: "low"` | **400** — *"thinking_level is not supported by this model"* |
| `gemini-2.5-flash` + `thinkingBudget: -1` | 200, 25 thinking tokens |
| Gemini 3 | takes `thinkingLevel` (this credential reaches no Gemini 3 model, so: from the vendor) |
| OpenAI / Azure / Foundry | `reasoning_effort` — a word, and no budget at all |
| Anthropic | `budget_tokens` — a number, and no word at all |

So the words are the direction of travel, they are largely the *same* words across vendors, and
which ones a given model takes is a property of that model.

## Decision

**Split the vocabulary in two, by who owns the word.**

1. **Three settings are ours**, and every dialect spells them differently:
   `disabled` (Google's budget `0`, OpenAI's `"none"`), `auto` (Google's `-1`; Anthropic and the
   OpenAI dialect have no way to say it and refuse by name), `limited` (a token count the **caller**
   named). A closed set, because each one is a decision this gateway makes.

2. **Everything else is a level word the vendor accepts**, declared per model as **free text** and
   passed through untranslated. `ThinkingMode` no longer contains `low`/`medium`/`high`/`minimal`;
   `Thinking.mode` is a `str`.

3. **No level carries a token count anywhere.** The `level → tokens` table is deleted from the
   catalogue, the console, and the resolution path.

4. **The reservation asks the model, not the request.** `Thinking.tokens` is now only *what goes on
   the wire* — which only `limited` has. The pre-dispatch reservation reads the model's declared
   ceiling instead. These were one field, and that conflation is exactly why a level had to invent a
   number: so the reservation had something to read.

5. **A dialect declares whether it has a level field at all** (`Upstream.expresses_thinking_levels`),
   beside the existing `thinking_modes`. Anthropic does not, and refuses a level by name.

   *Amended 2026-08-20 (`FRD-612`).* This sentence cited `thinking_modes` as an existing fact, and
   it was: declared by four adapters, asserted by one test, and **read by no code on any path**. So
   the console offered `auto` for a model on an OpenAI-dialect endpoint, accepted it in silence,
   and every request asking for it was refused at mapping time — as a `500`, because
   `DialectUnsupported` was not in `REFUSALS` either. Both halves are now joined: the refusal is
   named on both surfaces, and the console's button in §6 asks about the **ticked modes** as well
   as the words, answering them from the dialect without sending anything.

6. **The model is asked whether a word works.** Free text needs an authority, and no rule in this
   repository can be one — a list here is always a release behind the vendors. The console's
   *"Ask the model"* button sends one capped request per word and shows the provider's own refusal.

   The same button answers the **modes**, and answers them differently: from the dialect, with no
   request at all. Whether a wire format has a field for *"you decide"* is not a question about the
   model or the region it runs in, so asking a provider would spend a token to learn something the
   adapter already states.

## Why the check is a button and not a rule

Measured before building it, because whether it could be done cheaply decided whether it was worth
doing:

- `:countTokens` is **free and useless here** — it answers `200` to an unsupported `thinkingLevel`
  and to an out-of-range budget alike, because it never reads `generationConfig`.
- `generateContent` with `maxOutputTokens: 1` **does** judge, and is nearly free: a word the model
  refuses costs nothing (the refusal precedes any generation), a word it accepts costs one output
  token.

It informs and never blocks (`FRD-506`'s rule): the model may be unreachable while somebody is
filling in a form, and a red chip must mean *the model refused this word*, never *we could not ask*.

### What the words actually do, measured

The credential this installation holds reaches no Gemini 3 model in any regional endpoint, and
**does** reach one at `global` — which was itself a finding (see below). Against
`gemini-3.5-flash` there, on 2026-08-19:

| sent | answer | thinking tokens |
| --- | --- | --- |
| `thinkingLevel: "minimal"` | 200 | **0** |
| `thinkingLevel: "low"` | 200 | 69 |
| `thinkingLevel: "medium"` | 200 | 74 |
| `thinkingLevel: "high"` | 200 | 64 |
| `thinkingLevel: "hight"` (a typo) | **400** | *"Invalid value at … ThinkingLevel"* |
| `thinkingBudget: -1` | 200 | 60 |
| `thinkingBudget: 0` | 200 | 0 |

All four words work on a model that has them, `minimal` really does mean no thinking, and the same
model takes the budget as well. The typo is refused by the vendor with the value named — which is
the second kind of answer the check surfaces, distinct from *"thinking_level is not supported by
this model"*: one is a word that does not exist, the other a word this model does not take.

The trivial prompt spent 64–74 tokens at every level, which is `ADR-0021`'s other measurement
restated: **a level is a ceiling, not a spend.**

### The region that could serve them was the one we could not address

`gemini-3.5-flash` and `gemini-3-flash-preview` answered **only at `global`** — 404 in
`europe-west1`, `europe-west4`, `europe-north1` and `us-central1`. And `host_for()` built
`global-aiplatform.googleapis.com`, which **resolves** and answers 404. A dead host that fails DNS
is obvious; one that resolves and 404s reads as *"the model does not exist there"*. Reported as
*"I cannot call any 3.5 models to test them"*, and it had nothing to do with thinking.

It found something on its first press. The migration carried `gemini-2.5-flash`'s old declaration
across as `levels: [low, medium, high]`; all three came back red with Google's own sentence. That
declaration had looked correct in the console for as long as it had existed.

## Consequences

- **Positive.** Nothing in the catalogue can truncate a model's reasoning any more, because nothing
  in the catalogue is a budget except the bounds on `limited`, where the caller names the number.
- **Positive.** A vendor's next word costs nothing: type it, check it, ship it. The previous design
  made it a change to an enum, a TypeScript union, a console list and a validator.
- **Positive.** The console asks two questions about thinking instead of ten.
- **Negative.** A typo now looks like a working declaration until somebody presses the button or a
  caller gets a `400`. That is the price of free text and the reason the button exists; the
  alternative priced in a vocabulary that cannot keep up.
- **Negative, found on 2026-08-20 and fixed.** The same freedom applies to the three modes that are
  *ours*, and there the catalogue could claim something the dialect has no field for — with no
  authority consulted anywhere, because the declaration that knows was read by nothing. A caller
  then received `500 Internal error`. `FRD-612` names the refusal on both surfaces and gives the
  button the second half of the question.
- **Negative — and deliberate.** On a model whose dialect takes only numbers (Gemini 2.5, Anthropic
  today), the level words are simply **not offered**. `low` and `high` do not collapse into one
  instruction there; they are absent, and a caller asking for one is refused by name with the list
  of what that model does take. An earlier draft of this revision proposed mapping them all onto
  "let the model decide", which the owner rejected on the right grounds: *"for a chatbot it would be
  different"* — the whole point of `low` is that it is not `high`, and a translation that erases the
  difference is worse than an honest refusal.
- **Unchanged.** `FRD-135`'s reasoning visibility, the `limited` bounds, and the rule that thinking
  tokens are billed as output and counted always.
