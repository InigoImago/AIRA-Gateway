# FRD-124 — Nothing a request asks for is silently dropped

> Phase: 8 · Status: **Done (2026-08-06)** · Owner: AIRA · Last updated: 2026-08-06
> Related: `docs/PRD.md` §1.2, `docs/adr/ADR-0011.md`, `docs/adr/ADR-0012.md`, `docs/adr/ADR-0013.md`,
> supersedes `FRD-100` FR-7

## 1. Summary

Twelve fields that a legitimate Google client can send were posted at a running AIRA gateway.
**Eleven came back `200 OK` and had no effect whatsoever.** A caller who set `stopSequences` got
unbounded output; who set a `seed` for reproducibility got a different answer every time; who sent
`tools` got prose where a function call was expected; who sent `safetySettings` configured a
governance control that was applied nowhere. Nothing in any response, header or audit row said so.

This project already refuses to degrade a request silently when the *model* cannot do something —
a model that cannot read a PDF is skipped, not sent the prompt without it (`ADR-0012` §3). This is
the same rule turned on the **surface**: what a gateway accepts is a promise, and accepting a field
is a promise to honour it.

## 2. Goals & Non-Goals

**Goals**
- Every field a request carries is honoured, refused by name, or causes the candidate to be skipped.
- Portable sampling controls (`topP`, `topK`, `seed`, penalties, `stopSequences`) actually reach
  the model.
- A dialect that has no word for a control refuses the candidate rather than answering without it.
- Fields that are out of scope by `ADR-0013` are refused with the reason, not with a schema error.

**Non-Goals**
- Implementing the refused features. `tools`, `cachedContent` and `responseModalities` stay out of
  scope; this FRD is about *saying so*.
- Response strictness. A provider adding a response field must never break a caller — the rule is
  deliberately one-directional (see §5.4).

## 3. User Stories

- As an **application developer**, I want a request that sets a `seed` to be reproducible, or to be
  told it cannot be, so I do not ship a test suite that fails intermittently for no visible reason.
- As an **IT Security reviewer**, I want a safety or governance setting to be applied or refused,
  never accepted and ignored, because a control that silently does nothing is worse than no control.
- As a **use-case administrator**, I want a mistyped field to fail loudly during integration rather
  than change behaviour six months later when somebody fixes the typo.

## 4. Functional Requirements

- **FR-1** A request field the gateway does not serve is refused with a **400** naming the field
  and stating why. Applies at every level of the body: top level, `generationConfig`, and `parts`.
- **FR-2** An unknown field is refused naming the field. (Google's own API does this; leniency was
  `FRD-100` FR-7's compatibility argument and is now measured to cost more than it bought.)
- **FR-3** `topP`, `topK`, `seed`, `presencePenalty`, `frequencyPenalty` and `stopSequences` are
  carried to the provider.
- **FR-4** A candidate whose **dialect** cannot express a requested control is **skipped**, with the
  control named; an exhausted chain fails `400 FAILED_PRECONDITION`. Checked per hop, like every
  other requirement.
- **FR-5** An adapter that declares no sampling support refuses every control rather than accepting
  all of them — undeclared means unsupported, as in the catalog.
- **FR-6** `candidateCount` is accepted only as `1`.
- **FR-7** Thinking switched off is **sent** as off. An absent parameter selects the *model's*
  default, which for a reasoning model is on.
- **FR-8** The compatibility surface holds the same rule; its refusals keep the predecessor's
  status codes (**422**) and error vocabulary.
- **FR-9** A refusal is audited like any other (`FRD-122`).

## 5. Design & Architecture

### 5.1 Three answers, not two

    portable and supported     → carried to the dialect       topP, seed, stopSequences, …
    known but out of scope     → refused, saying why          tools, safetySettings, cachedContent
    the dialect cannot say it  → the candidate is skipped     top_k on OpenAI, seed on Anthropic

The third is the interesting one and it is not new: it reuses the `Requirement` mechanism that
already carries region, media types, structured output and thinking. `SamplingExpressible` is the
fifth, and the first that is a property of the **dialect** rather than of the model — no catalog
declaration can say whether `top_k` exists, because that depends on the wire format the request
will travel over.

### 5.2 Support is declared, never inferred

    Gemini      top_p  top_k  seed  presence  frequency  stop
    OpenAI      top_p    —    seed  presence  frequency  stop
    Anthropic   top_p  top_k    —       —         —      stop

`ADR-0011` rule 3 in its usual form — a flag says *whether*, the dialect says *how* — with the twist
that the honest answer is sometimes "it cannot", and that must be a refusal. Each adapter declares
`sampling_controls`; a test fails if one does not, so the omission is caught where somebody can
still choose the right answer rather than in production.

### 5.3 Why refusal and not best effort

`seed` on a Claude candidate produces a perfectly good answer that simply is not reproducible.
`top_k` on an OpenAI-compatible endpoint produces a perfectly good answer sampled from a wider
distribution than was asked for. **Neither response differs from a correct one in any observable
way**, which is the definition of a difference that has to be refused rather than absorbed — the
same argument `ADR-0012` makes for a dropped attachment, one layer down.

### 5.4 Strictness is one-directional

Requests are a promise *we* make; responses are a promise somebody else makes. Tightening both
would turn every upstream release into an outage, so response models keep ignoring extras.

### 5.5 Reversing `FRD-100` FR-7

FR-7 had request models ignore unknown fields so that real Gemini clients sending extra keys were
not rejected. The argument was reasonable and turned out to be empirically wrong in both halves:
Google's API is itself strict, so leniency is not the compatible choice; and the fields clients
actually send are ones that *change the answer*, so ignoring them is not harmless. A client sending
an unknown field is either misspelling a real one or using a feature we do not have, and both are
better said out loud.

## 6. Data Model

None. No migration.

## 7. API / Interface Contract

New in `generationConfig`: `topP`, `topK`, `seed`, `presencePenalty`, `frequencyPenalty`,
`stopSequences`, `candidateCount` (must be `1`).

Refused with `400 INVALID_ARGUMENT`, each naming the field: `tools`, `toolConfig`, `cachedContent`,
`safetySettings`, `responseModalities`, `speechConfig`, `responseLogprobs`, `logprobs`,
`mediaResolution`, `enableEnhancedCivicAnswers`, the `functionCall`/`functionResponse`/
`executableCode`/`fileData` part shapes, `thinkingConfig` at the top level, and anything unknown.

Refused with `400 FAILED_PRECONDITION` when no candidate's dialect can express a requested control.

The KIRA surface refuses unknown fields with **422** and `VALIDATION_ERROR`, its own vocabulary.

## 8. Testing

- **Hermetic** — `gateway/tests/test_no_silent_drop.py`, 38 tests. Eleven of them were shown red
  against the previous behaviour before the fix.
- **Mutation** — `Y1`–`Y8`.
- **Integration** — `tests/integration/test_no_silent_drop.py`, 9 tests, against a real model.
  These assert **behaviour, not wire bodies**: a seed makes three identical requests return one
  answer, a stop sequence truncates the output, thinking switched off produces an answer instead of
  600 tokens of hidden reasoning. Neither claim can be established by inspecting a dict, which is
  precisely how the thinking defect survived a hermetic suite that "tested" it.

## 8a. What the same round found next

Counting audit rows for every kind of refusal showed that all of them are recorded except three:
the body-size ceiling (closed — see `FRD-122` §12) and the two authentication failures (a decision,
also recorded there).

## 9. Rollout

Breaking for any client that was sending a field AIRA ignored — deliberately, since that client is
already not getting what it asked for. Refusals name the field and the reason, so the fix is
mechanical: remove the field, or ask for the capability.

## 10. Open Questions

- Should residency-style **per-use-case** policy ever allow an operator to opt back into leniency?
  Recommendation: no. The value of the rule is that it holds everywhere.
- `topK` on an OpenAI-compatible endpoint could be approximated by `top_p`. Deliberately not done:
  approximation is the failure mode this FRD exists to remove.

## 11. Dependencies & Risks

Depends on the dispatch-chain requirement mechanism (`ADR-0012` §3) and on each adapter declaring
its dialect. The risk is a client breaking on upgrade; the mitigation is that the break is loud,
named, and describes the fix — which is the entire point.
