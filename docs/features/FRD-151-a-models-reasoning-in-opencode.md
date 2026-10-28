# FRD-151 — A model's reasoning, shown in OpenCode

> Phase: 8 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner, from OpenCode — other providers, GitHub Copilot among them, let a developer
> watch the model think; does AIRA? Related: `FRD-135` (reasoning returned, on by default),
> `FRD-145` FR-8 (streams carry it), `FRD-111` (thinking control and the declared default),
> `FRD-508` (drafts from the model reference), `FRD-132` (the OpenCode configuration), `ADR-0021`
> (a level is the vendor's word), `ADR-0038` (a person decides the catalogue).

## 1. Problem

AIRA returns a model's reasoning (`FRD-135`), streamed as it arrives (`FRD-145` FR-8). In OpenCode
nothing of it showed. Measured with both generations against the gateway, three reasons, each
enough on its own:

1. **OpenCode named no thinking setting.** The configuration the console hands out described each
   model's name, limits and price. OpenCode offers a model's thinking settings as *variants* — the
   choice `ctrl+t` cycles through — and builds them itself only for a model marked as reasoning,
   which models.dev does for the providers it lists and the console's file did not. There was
   nothing to choose, and every request went out without a thinking setting.
2. **A request without one asked for no reasoning.** The gateway asks for the reasoning only where
   it knows the model is thinking (`FRD-135` FR-5): Google refuses `includeThoughts` from a model
   that is not, and Claude's newest models return their thinking empty unless the request asks for
   `display: "summarized"`. What tells the gateway is the model's declared thinking default
   (`FRD-111` FR-4) — and the model reference drafted none, so no catalogue entry had one.
3. **So a model that thinks anyway did it unseen.** Gemini 2.5 Pro and Flash, every Gemini 3 and
   later, and Claude from Opus 5 on think when nothing is asked. They did, the provider billed it,
   and nobody had asked to see it.

Copilot shows the reasoning because it knows each model, picks a setting and asks. The same three
things had to hold here: a setting to choose, a default where none is chosen, and the request for
the reasoning that follows from either.

## 2. Goals & Non-Goals

**Goals**
- OpenCode offers each model the thinking settings its catalogue entry declares, and shows the
  reasoning of the one chosen.
- With nothing chosen, a model that thinks anyway shows that reasoning — at no extra cost, because
  the default names what the model does unasked.
- Which default a model gets stays a person's decision; the reference proposes it from the
  vendor's own documentation, and the editor offers it for a model already on file.

**Non-Goals**
- A budget variant (§5.2), and the level words OpenCode cannot send (§5.3).
- Setting a default on entries already on file without anybody choosing it (`ADR-0038`).
- The showcase's configuration file (§11).
- The KIRA surface, whose contract has no field for reasoning (`FRD-135` §7).

## 3. User Stories
- As a **developer in OpenCode**, I want to see what the model is working through and choose how
  hard it thinks, as I can with Copilot.
- As a **catalogue administrator**, I want to know which thinking default a model needs without
  researching each vendor's documentation, and to decide it myself.
- As **IT Steuerung**, I want that without a token more on the bill.

## 4. Functional Requirements

- **FR-1 Variants from the declaration.** The configuration the console hands out gives each model
  the variants its thinking declaration offers and OpenCode can send: `none` (`thinkingBudget: 0`)
  where `disabled` is declared; `minimal`, `low`, `medium` and `high` (`thinkingLevel`) where the
  model declares the word; `auto` (`thinkingBudget: -1`) where `auto` is declared. A model without
  a thinking declaration gets no variants.
- **FR-2 Least thinking first.** `none`, then the levels from `minimal` to `high`, then `auto` —
  whatever order the catalogue holds the words in. OpenCode 1 sends the *first* variant with its
  own small requests, a session's title among them (measured), so the cheapest setting the model
  takes belongs there. Before, those requests named no setting. Where a model's only variant is
  `auto` they are now sent `auto`: what a model that thinks unasked does anyway, and thinking for
  one that does not — Gemini 2.5 Flash-Lite, whose drafted declaration cannot say `disabled`.
- **FR-3 The variants do not ask for the reasoning.** No `includeThoughts`: the gateway asks
  wherever the use case returns reasoning, and a file that asked itself would be refused by name
  on a use case that does not (`FRD-135` FR-4). OpenCode 2 adds `includeThoughts: true` on its
  own once a variant is chosen (measured); on a use case that returns no reasoning that request is
  refused by name, as it is from any client.
- **FR-4 The reference proposes the vendor's default.** A draft of a model whose vendor documents
  what it does when a request names no thinking setting carries that as its default: the setting
  that, on this gateway's wire, reproduces it. `auto` for Gemini 2.5 Pro and 2.5 Flash (Google's
  dynamic budget) and for Claude Fable 5 and 5.1, Opus 5 and 5.5, Sonnet 5 and 5.5 and Haiku 5.5
  (adaptive thinking) — with `auto` declared, where the list names only effort words. Google's
  documented level for each Gemini 3 and later model. The draft's notes say so.
- **FR-5 Nothing is proposed where the vendor says nothing, or says otherwise.** Not for Claude
  Opus 4.8 and earlier, which think only when asked; not where Google's documented level is
  `minimal`, where there is next to nothing to return; not for Gemini 2.5 Flash-Lite, on which
  Google's two references disagree; not for a level the list does not declare, which the draft
  notes. Named per model, never per family: a vendor changes this from one model to the next.
- **FR-6 Offered for a model on file.** The editor shows the reference's proposal beside *What
  happens when a caller says nothing* for a model on file that names no default, with a button that
  takes it — and ticks `auto` where the declaration on file does not offer it yet, so the entry
  still saves. Nothing changes until somebody clicks. A level the declaration on file does not
  offer is not proposed: the two disagree, and a person settles that.
- **FR-7 The gateway, unchanged in mechanism.** A declared default resolves into the request's
  thinking setting (`FRD-111` FR-4), and a thinking setting is what makes the gateway ask:
  `includeThoughts` on Google's wire, `display: "summarized"` on Anthropic's adaptive thinking
  (`FRD-145`). Held end to end by a test, from a request naming nothing to a thought in the stream.

## 5. Design & Architecture

### 5.1 Why the gateway must know the model is thinking

The obvious alternative — always ask for the reasoning — fails on both vendors that need asking.
Google answers `includeThoughts` from a model that is not thinking with a `400`; that is why
`FRD-135` FR-5 asks only where thinking is on. And on Anthropic's wire "ask for the summary" is a
field of the `thinking` block: sending `{"type": "adaptive", "display": "summarized"}` to Claude Opus
4.8, which thinks only when asked, would switch its thinking on and bill it. Both need the same
fact — *this model thinks when nothing is asked* — and the catalogue already has the field that
states it: the thinking default, whose whole meaning is "what the model does when the caller says
nothing".

### 5.2 Why no budget variant

A budget needs a number. The only ones a vendor states are its bounds: the floor is too little to
be what somebody choosing a thinking variant wants, and the ceiling does not fit. OpenCode sends
`maxOutputTokens` with every request — the model's output cap, at most 32 000 — and a budget at or
above it is refused before dispatch (`FRD-111` FR-3a): Claude Sonnet 4.5's 63 999 would fail every
turn. OpenCode's own budget variants call 16 000 tokens *high* — a number no vendor publishes, which
`ADR-0021` removed from this catalogue for the same reason. A budget-only model's thinking is set by
its declared default (`limited` with a count), which reaches OpenCode with no variant at all — and
continues a tool loop only because the model's thinking block now travels with the call
(`FRD-152`); before, a budget was refused on every continued call.

### 5.3 Why four words

OpenCode 1 sends through the AI SDK's Google provider, which checks `thinkingLevel` against
`minimal`, `low`, `medium` and `high` before the request leaves the workstation. A variant naming
Claude's `xhigh` or `max` would fail every turn it was chosen for, so it is not written. The gateway
takes them; OpenCode cannot send them.

### 5.4 Where the defaults come from

The public list the reference reads says which settings a model accepts, never what it does
unasked. The vendors say it per model:

| | source | what it states |
| --- | --- | --- |
| Gemini 3 and later | Google Cloud, *Thinking*, "Supported thinking levels by model" | the level each model uses when none is sent |
| Gemini 2.5 | the same page, "Supported thinking budgets by model" | a budget set dynamically when none is sent — `auto` |
| Claude | Anthropic, *Migration guide* and *Models overview* | which models run adaptive thinking when `thinking` is left out |

The table lives in `apps/catalog/reference/thinking_defaults.py`, one row per model, beside the
link it was read from. It is consulted when a draft is made; the shipped copy of the reference is
regenerated with it, and a test fails when the two disagree. A model the table does not name gets
no proposal, which is the state every model was in before.

### 5.5 What OpenCode does with the file

Measured with OpenCode 1.19.3 and 2.0.32:

- OpenCode 1 merges the file's `variants` over the ones it builds itself, and builds none for a
  model the file does not mark `reasoning` — which the console does not, because OpenCode 1 then
  sends `thinkingLevel: "high"` to every such model, refused by name where `high` is not declared.
  A variant is chosen with `ctrl+t` or `opencode run --variant <name>`.
- OpenCode 2 reads the same shape and turns each variant into its own request settings; a variant
  is chosen after the model's name, `--model aira/<model>#<variant>`.
- Both print the reasoning a stream carries with `opencode run --thinking`, whatever the file says
  about `reasoning`. The terminal interface was not read back in this round.

## 6. Data Model

None. The thinking default has existed since `FRD-111`; 27 drafts of the shipped reference now
carry one.

## 7. API / Interface Contract

No API changes. The configuration the console hands out gains `variants` per model:

```json
"gemini-3.5-flash": {
  "name": "gemini-3.5-flash via AIRA",
  "variants": {
    "minimal": { "thinkingConfig": { "thinkingLevel": "minimal" } },
    "low": { "thinkingConfig": { "thinkingLevel": "low" } },
    "medium": { "thinkingConfig": { "thinkingLevel": "medium" } },
    "high": { "thinkingConfig": { "thinkingLevel": "high" } }
  }
}
```

## 8. Security & Privacy

More requests return reasoning: those to a model whose default is declared, from a use case that
returns reasoning. It is stored exactly like the answer — same column, same retention, same role to
read it (`FRD-135` §5.3) — so the privacy register is unchanged: the category, the column and the
gate are the ones already named. A use case that must not keep reasoning turns it off, and then the
default changes nothing but the thinking setting sent, which is the model's own anyway. The
configuration file gains no personal data.

## 9. Observability

Nothing new. `reasoning_tokens` is on the row as before; what thinking cost was already counted,
because the default names what the model did anyway.

## 10. Testing & Acceptance Criteria

- `opencode-config.spec.ts`: the four words and no others; least thinking first whatever the
  declared order; `none` and `auto` as Google spells them; no budget variant; no `includeThoughts`;
  nothing for a model without thinking. `api-keys-panel.spec.ts`: the variants in the file handed
  out, and the hint beside it that names `ctrl+t`.
- `test_model_reference.py`: a model that thinks unasked is proposed its default, with `auto`
  declared where needed; one that thinks only when asked, nothing; a level the list does not
  declare is not proposed and the draft says why; the shipped copy carries exactly the table.
- `model-catalog.spec.ts`: the proposal beside a model on file, taken with a click; none over a
  default on file, nor for a level the declaration does not offer; a new entry filled with it.
- `test_a_model_that_thinks_unasked.py`: the default makes Claude ask for the summary and Gemini
  for the thoughts at its own level; without it the request is what it was; end to end, a stream
  that names nothing carries the thought exactly when the default is declared.
- `test_real_gemini_clients.py`, both generations: a chosen variant is the level sent and its
  reasoning is shown; with none chosen, the declared default is what answers, and its reasoning is
  shown; OpenCode 1's own small request goes with the cheapest variant.
- Mutations `TO-1`–`TO-13`.

## 11. Dependencies & Risks

- **The defaults are the vendors' statements, read once.** A vendor that changes a model's
  default, or a model the table does not name, gets the state before this feature: no proposal,
  and an administrator who decides. The table is re-read when a model is added to it.
- **Verified against Google on 2026-10-28, not against Anthropic.** Gemini 2.5 Flash, Gemini 3.5
  Flash and GLM-5.2 through both OpenCode generations, with the console's file: the reasoning shows
  wherever the vendor sends it — Gemini 3.5 Flash sends a summary for some steps only, and at `low`
  often none. Claude needs a service account this environment does not have.
- **The showcase's file has no variants.** `make showcase-agent` builds its file from the gateway's
  model listing, which carries no thinking declaration; the console's file does.
- **A file handed out before this has no variants.** The console builds the file while a new key's
  plaintext exists, so a developer who wants the variants adds the block by hand or takes a new
  key. The default needs no new file.

## 12. Rollout

After the upgrade, open each model on file that thinks unasked in the catalogue and take the
proposed default — the editor offers it beside *What happens when a caller says nothing*. From then
on OpenCode shows that model's reasoning with nothing chosen; the variants arrive with the next
configuration file.
