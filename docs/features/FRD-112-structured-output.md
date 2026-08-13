# FRD-112 — Structured output (`responseSchema`)

> Phase: 8 (KIRA parity) · Status: **Done** · Owner: Vadim Scheibe
> Origin: the predecessor's contract (`responseSchema`), programme: `ADR-0010`.
> Touches: `FRD-100` (surface), `FRD-300` (pipeline routing), `FRD-114` (model capabilities).

## 1. Problem

An application that feeds a model's answer into code needs the answer to be parseable. Asking for
JSON in the prompt and hoping is the failure mode this feature exists to remove: the predecessor
lets a caller attach a schema, and the model is constrained to produce a document that matches it.

AIRA has no equivalent, so every consumer that relies on it — anything that extracts fields,
classifies into a fixed vocabulary, or returns a list of objects — parses free text or cannot
migrate.

## 2. Goals & Non-Goals

**Goals**
- A caller attaches a response schema; the model returns JSON conforming to it.
- The supported schema vocabulary is the predecessor's OpenAPI-3.0 subset, so existing schemas
  work unchanged.
- A schema is **bounded**: size, nesting depth and property count, because it arrives from the
  network.
- A request whose schema a model cannot honour is refused *after* routing, not before (§5.3).

**Non-Goals**
- **Validating the response against the schema ourselves.** The provider enforces it; re-checking
  in the gateway would mean executing caller-supplied constraints — including regular expressions —
  on every response, on the hot path. §5.4 explains why that is the wrong trade here.
- **Translating between schema dialects.** We accept the subset and pass it on. A caller sending
  JSON Schema draft 2020-12 gets an error naming the field we did not understand, not a best-effort
  conversion.
- Tool/function calling. Adjacent, differently shaped, not needed for parity.

## 3. User Stories
- As an **application developer**, I want the answer as an object with the fields I named, so that
  I can use it without writing a parser for prose.
- As a **use-case administrator**, I want a request that asks for a schema to be routed only to
  models that can honour it, so that enabling model fallback does not silently start returning
  free text to a caller that cannot read it.

## 4. Functional Requirements

- **FR-1 Schema on the request.** Optional. When present, the response is a JSON document matching
  it and the response media type is `application/json`.
- **FR-2 Vocabulary.** The predecessor's field set: `type`, `properties`, `items`,
  `propertyOrdering`, `required`, `enum`, `format`, `description`, `title`, `pattern`, `nullable`,
  `default`, `example`, `minimum`/`maximum`, `minLength`/`maxLength`, `minItems`/`maxItems`,
  `minProperties`/`maxProperties`, `anyOf`. Types: `STRING`, `INTEGER`, `NUMBER`, `BOOLEAN`,
  `ARRAY`, `OBJECT`. An unknown field is a 400 that names it.
- **FR-3 Bounds.** Maximum serialized size, maximum nesting depth, maximum total property count.
  Each configurable, each with a conservative default, each refused with a message naming the
  bound. A schema is caller-supplied structure and recursion is caller-controlled.
- **FR-4 Capability-checked after routing.** A model must declare `structured_output` (`FRD-114`).
  The check runs on the model actually dispatched to, including after `model_route` and at every
  step of a `fallback_models` chain (§5.3).
- **FR-5 Streaming is allowed and says what it is.** The endpoint accepts a schema; the deltas are
  fragments of one JSON document and are not individually parseable. Documented, not prevented.
- **FR-6 A model that cannot satisfy the schema fails loudly.** Where the upstream reports a
  finish reason other than a normal stop for a schema-constrained request, the gateway surfaces it
  rather than returning a truncated document that looks like data.

## 5. Design & Architecture

### 5.1 A parsed schema, not an opaque blob

The schema is modelled explicitly — a recursive Pydantic model over FR-2's vocabulary — rather than
passed through as `dict[str, Any]`. Three reasons, in order of importance:

1. FR-3's bounds need something to count.
2. An unknown field becomes an error naming the field, at our boundary, instead of a provider error
   naming nothing useful.
3. The KIRA surface (`FRD-107`) and the Gemini surface can then be two mappings onto one model,
   which is the whole reason the canonical core exists.

### 5.2 On the wire — one capability, two mechanisms

Gemini has the feature directly: `generationConfig.responseMimeType = "application/json"` and
`generationConfig.responseSchema = <schema>`.

**Anthropic has no schema parameter at all.** The equivalent is a forced tool call — one tool whose
`input_schema` is the caller's schema, `tool_choice` pinned to it, and the model's tool input read
back as the document (`FRD-119` §5.5).

Azure OpenAI has a third: `response_format: {type: "json_schema", json_schema: {…, strict: true}}`
(`FRD-120` §5.4), where `strict` mode further restricts what is expressible.

So `structured_output` in `FRD-114` means "this model can do it **by some mechanism**", and which
one is the adapter's business — three unrelated mechanisms behind one flag is exactly what
`ADR-0011` rule 3 exists to keep out of the catalog. Two things follow that are easy to get wrong:

- **A schema field with no faithful equivalent is a refusal, not a silent drop.** Anthropic's
  `input_schema` is JSON Schema and our subset is OpenAPI-3.0-flavoured; most fields translate, and
  a caller who constrained a value and quietly got an unconstrained answer has been misled.
- **A model that answers with prose instead of calling the tool has not satisfied the schema.**
  That is FR-6, and with this mechanism it is a real path rather than a defensive one.

The canonical request carries `response_schema: Schema | None`; adapters without the capability are
excluded by FR-4 before they are ever asked.

### 5.3 The check belongs after routing — and with two vendors it is load-bearing

`FRD-300` gives a use case a `model_route` step (an LLM classifier picks a model per category) and
a `fallback_models` dispatch chain. Both mean **the model that answers is not necessarily the model
the caller named**.

If the schema capability were checked against the requested model, a use case with a fallback chain
could accept a schema request, have the primary fail, fall back to a model without structured
output, and return prose to a caller that will try to `JSON.parse` it. The failure would surface as
a parse error in someone else's application, days later, with no obvious link to AIRA.

So: the capability is checked against the model **about to be dispatched to**, at every hop. A
fallback candidate lacking the capability is skipped like any other unusable candidate; if no
candidate qualifies, the request fails with a message saying so rather than answering in the wrong
shape. Returning the wrong *shape* is worse than returning an error, because only the error is
noticed.

### 5.4 Why we do not validate the response

Re-validating would mean running caller-supplied `pattern` regexes, and caller-supplied `minItems`
recursion, over provider output on every request. `ADR-0007` already rejects nested-quantifier
regexes in pipeline configuration for exactly this reason; the same exposure would arrive here by a
different door, on a hotter path, for a check the provider has already performed.

The schema is therefore **forwarded, never executed**. The gateway's own exposure is bounded by
FR-3 alone, which is a counting exercise and cannot backtrack.

> If local validation is ever wanted — the plausible reason being a provider that honours schemas
> only approximately — `ADR-0007`'s regex rule applies to `pattern` at that moment, and the check
> belongs off the hot path.

## 6. Data Model

None. The schema is per-request. The audit row records **that** a schema was used and its digest
(the same treatment as an attachment in `FRD-110`), not the schema itself: schemas are large,
repetitive, and occasionally reveal the caller's data model.

## 7. API / Interface Contract

```json
{ "contents": [...],
  "generationConfig": {
    "responseMimeType": "application/json",
    "responseSchema": { "type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": { "recipeName": {"type": "STRING"},
                        "ingredients": {"type": "ARRAY", "items": {"type": "STRING"}} },
        "propertyOrdering": ["recipeName", "ingredients"] } } } }
```

Errors (`400 INVALID_ARGUMENT`, or the KIRA vocabulary via `FRD-107`): unknown schema field; type
outside the supported set; a bound exceeded, naming it; no dispatchable model declares
`structured_output`.

## 8. Security & Privacy

- The schema is caller-supplied structure: FR-3's bounds are the control, and §5.4 explains why
  there is no expression evaluation to attack.
- Schemas are not persisted in full (§6). They can encode a customer's internal data model, and
  the payload column is subject to retention and — eventually — redaction that could not read them.

## 9. Observability

`aira.response_schema` (boolean) and `aira.response_schema.digest` on the span, so "structured
requests are slower / more expensive" is answerable without storing schemas.

## 10. Testing & Acceptance Criteria

- **Unit** — each supported type and field parses; an unknown field is refused naming it; each
  bound refused at its boundary, including a deeply nested schema; the mapping to
  `responseMimeType` + `responseSchema` is exact.
- **Unit** — **the routing interaction (§5.3)**: a fallback chain whose primary lacks the
  capability skips it; a chain where *no* candidate has it fails rather than answering in prose;
  a `model_route` decision to a model without the capability is refused. This is the test that
  justifies the design, and it must be shown to fail against a naive "check the requested model"
  implementation.
- **Integration** — against a real upstream, a schema request returns a document that parses and
  matches the requested shape.
- **Mutation** — the bounds actually bound; the capability check actually runs post-routing (the
  mutation moves it back to pre-routing and the test must go red).

**Acceptance**
- *Given* a schema for an array of objects, *when* a caller submits it, *then* the response body
  parses as JSON matching that shape and the audit row records the digest and not the schema.
- *Given* a use case whose fallback chain contains one model without `structured_output`, *when*
  the primary fails, *then* that candidate is skipped rather than answering in free text.

## 10a. What was built (2026-08-06)

`core/schema.py`: a recursive model over FR-2's vocabulary with `extra="forbid"`, so an unknown
field is an error **naming the field** rather than a silent drop; bounds on size, depth and total
property count, each counted across the whole tree. Forwarded, never executed.

Two mechanisms behind one flag, as §5.2 requires: Gemini gets `responseMimeType` +
`responseSchema` (always together — the schema alone is *ignored* by the API, which would return
prose to a caller expecting a document, produced by our own request body); Anthropic gets a forced
tool call whose `input_schema` is the caller's schema translated to JSON Schema. Only
`propertyOrdering` and `example` are dropped in that translation and neither constrains a value.

**§5.3 is the design this feature turns on**, and its test is written to fail against the naive
version: a chain whose primary declares the capability and whose fallback does not would, under a
"check the requested model" implementation, answer in prose — the failure surfaces as a parse error
in someone else's code, days later. `StructuredOutputSupported` runs per hop instead.

FR-6 is real rather than defensive: `check_structured_result` refuses an answer that did not finish
normally, and the mock truncates a document at the output cap so that path is exercised at all.

Not built: the audit **digest**. `ResponseSchema.digest()` exists and is tested; wiring it onto the
row needs a migration, and it was left out rather than half-done. Stated here so it is a task and
not an assumption.

**Verified against a real model (2026-08-06).** A schema request through the OpenAI dialect came
back `{ "colour": "red" }` with a normal finish reason — so the capability is *declared* for that
model on evidence rather than on a guess, which is the converse of `FRD-114` FR-7's rule.

Mutations **S1–S7**.

## 11. Dependencies & Risks

- **`FRD-114`** for the `structured_output` capability flag; **`FRD-300`** for the routing
  interaction.
- **Risk** — providers differ in how faithfully they honour a schema. FR-6 is the mitigation:
  surface an abnormal finish reason instead of returning a partial document as data.
- **Open** — whether any consumer streams a schema-constrained response. FR-5 keeps it working
  either way.

## 12. Rollout / Demo

The mock generates a deterministic document conforming to the submitted schema — enough to
demonstrate the whole path, including the routing interaction, with no cloud credentials.
