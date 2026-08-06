# FRD-121 — Document normalisation for models that cannot read documents

> Phase: 8 (KIRA parity) · Status: **Draft — optional, and probably should not be built first**
> Owner: Vadim Scheibe · Last updated: 2026-08-06
> Governed by `ADR-0012` §4. Requires `FRD-110` (attachments) and `FRD-114` (capabilities).

> Read §2 and §11 before scheduling this. It exists so the option is specified rather than
> improvised, and the honest recommendation is to ship `ADR-0012` §3's capability gating first, run
> with it, and build this only if a concrete use case is actually blocked by it.

## 1. Problem

`ADR-0012` §3 settles what happens when a request carries a PDF and the target model cannot read
one: the model is skipped, and if no candidate qualifies the request fails. That is correct and it
is not always sufficient.

Two situations where it bites:

- A use case wants a **cheap, fast, or self-hosted** model for document questions, and the
  document-capable models are neither.
- A vendor outage removes every document-capable model from a chain, and an answer from extracted
  text would have been better than no answer — a judgement only the use case's owner can make.

The wish is then: let the gateway turn the document into something any model can consume — extracted
text, or page images.

## 2. Goals & Non-Goals

**Goals**
- A use case may opt in to conversion for models that cannot take a document natively.
- Conversion is **recorded and visible** — never mistaken for native understanding.
- The parser runs **outside the gateway process**.

**Non-Goals**
- **Enabling this by default.** `ADR-0012` §4.
- **Converting when the model can read the document natively.** Native is better and cheaper in
  quality terms; conversion is a fallback, never an optimisation.
- Chunking, embedding, retrieval. That is RAG and belongs to the consumer.
- OCR quality guarantees.

## 3. User Stories
- As a **use-case administrator**, I want to decide that extracted text is acceptable for my use
  case, so that a cheaper model can answer document questions.
- As an **application developer**, I want to know from the response whether the model read my
  document or a text extraction of it, so that I can judge the answer.
- As **IT Security**, I want the component that parses untrusted PDFs to be somewhere other than
  the process holding our credentials.

## 4. Functional Requirements

- **FR-1 Opt-in per use case**, and per media type. Default off.
- **FR-2 Only when needed.** If the dispatched model declares the media type, the original is sent
  unchanged. Conversion happens only on the branch where the alternative is skipping the model.
- **FR-3 Out-of-process** (§5.2).
- **FR-4 Recorded.** The audit row records that conversion occurred, the method, and the resulting
  size. `FRD-110`'s stored description gains a `converted` marker.
- **FR-5 Declared to the caller.** The response carries a provenance marker — the model did not see
  the document. A caller who cannot see this cannot weigh the answer.
- **FR-6 Bounded.** Page count, output size and wall-clock, each configurable, each refused with a
  message naming the bound. A 1000-page PDF must not become an unbounded token bill.
- **FR-7 Failure is a skip, not a substitution.** If conversion fails, the model is skipped as
  though the capability were absent (`ADR-0012` §3). It never falls through to sending nothing.
- **FR-8 The budget sees the conversion.** Extracted text or rendered pages change the input token
  count by an order of magnitude relative to the original estimate; the reservation is recomputed
  after conversion and before dispatch, or `FRD-405`'s guarantee is void on exactly this path.

## 5. Design & Architecture

### 5.1 Where it sits

Between routing and dispatch, on the branch where the chosen model lacks the media type and the use
case has opted in. Everything before it — the intersection check, the chain, the reservation — is
unchanged; this adds one conditional step, not a new pipeline.

### 5.2 The parser does not run here

`FRD-110` §8 states the reason plainly: the gateway accepts arbitrary bytes from an authenticated
caller and deliberately does not parse them, which is what keeps that attack surface small. PDF and
office-format parsers are among the most reliably exploitable code in existence, and the gateway
process holds cloud credentials, database connections and every in-flight request.

Two acceptable placements, in order of preference:

1. **A managed document service** — Google Document AI or Azure Document Intelligence. The parser
   is then the cloud's problem, it is already in the same region as everything else (so residency is
   unchanged), and it is materially better at layout and tables than a library would be. Cost and an
   extra hop are the price.
2. **An isolated worker** — a separate container with no credentials, no network egress, a read-only
   filesystem, memory and CPU limits and a hard timeout, receiving bytes and returning text.

What is not acceptable is a PDF library imported into the gateway. Convenience is not a reason to
put a parser next to the credentials.

### 5.3 Text or images — and why the choice is declared

Two methods, and they are not interchangeable:

- **Text extraction** — cheap, small, loses layout, tables and everything visual. Fine for prose,
  poor for a form or an invoice.
- **Page rendering to images** — preserves the visual, requires a model with image capability,
  multiplies token cost by page count.

The method is a per-use-case setting rather than a heuristic, because which is acceptable depends on
what the documents are, and a heuristic would silently pick differently for two similar requests.
Rendering additionally requires the target model to declare image support — a capability check, not
an assumption.

### 5.4 Provenance is the point

The single most important requirement is FR-5, and it is easy to under-build.

A converted request produces an answer that *looks* the same as a native one. Without a marker, a
caller comparing two answers cannot tell that one model read a table and the other read the table's
text run together into a paragraph. Over time that becomes "the quality got worse and nobody knows
why".

So the response carries the marker, the audit row carries it, and reporting can break down by it —
if conversion is happening far more often than expected, that is a routing or capacity problem
showing up as a quality problem, and it should be visible as a number.

## 6. Data Model

No new tables. `request_logs` gains a conversion marker in the payload description (`FRD-110` §5.4).
`FRD-114` needs no change: conversion is a use-case setting, not a model property.

## 7. API / Interface Contract

Use-case configuration: `document_conversion: {enabled, method, media_types, bounds}`.
Response: a provenance field naming the conversion; on the KIRA surface (`FRD-107`) it is an
additional field, since the predecessor has no equivalent and its clients ignore unknown keys.

## 8. Security & Privacy

- §5.2 is the requirement, not a preference.
- Converted content is still prompt content: it follows `store_payloads`, retention and — when it
  exists — redaction, unchanged.
- A conversion service in another region would break residency. It must be in the same region as
  the transport that will receive the result, and that is asserted at startup like every other
  region check (`FRD-115` §5.5).

## 9. Observability

`aira.document.converted`, the method, the page count and the duration. Conversion is the slowest
thing on the request path when it happens; without a span it will be blamed on the model.

## 10. Testing & Acceptance Criteria

- **Unit** — conversion runs only when the model lacks the capability and the use case opted in;
  a capable model receives the original bytes untouched; each bound refused at its boundary; a
  conversion failure **skips the model** rather than dispatching without the document (written to
  fail first against an implementation that falls through — that is the dangerous default).
- **Unit (budget)** — the reservation is recomputed after conversion; a request whose extracted text
  is ten times the estimate does not dispatch on the old reservation.
- **Unit (provenance)** — the marker is present on the response and the audit row whenever
  conversion occurred, and absent otherwise.
- **Integration** — a PDF request to a text-only model returns an answer marked as converted; the
  same request to a document-capable model is not converted.
- **Mutation** — the opt-in actually gates; the "only when needed" condition actually restricts;
  the failure path actually skips.

**Acceptance**
- *Given* a use case with conversion off, *when* a PDF request routes to a text-only model, *then*
  that model is skipped per `ADR-0012` §3 and nothing was converted.
- *Given* the same use case with conversion on, *when* the same request arrives, *then* the model
  answers, the response says the document was converted, and the audit row records the method.

## 11. Dependencies & Risks — and the recommendation

- `FRD-110`, `FRD-114`, and a conversion service or worker (§5.2).
- **Risk — this feature makes a quality regression invisible.** FR-5 and §5.4 are the entire
  mitigation, and they are the parts most likely to be trimmed under time pressure. If provenance is
  cut, do not ship the feature.
- **Risk — scope.** "Extract text from documents" is a product in its own right. Every hour spent on
  extraction quality is an hour not spent on the gateway.
- **Recommendation: do not build this first.** `ADR-0012` §3's capability gating is the correct
  default and is likely sufficient — Gemini and Claude both read documents natively and both sit on
  the same transport and credential, so a document-capable chain with a genuine fallback already
  exists without any conversion at all. Build this when a specific use case is demonstrably blocked,
  and let that use case decide between text and images.

## 12. Rollout / Demo

If built: the mock declares no document capability, so demo mode shows both branches — skipped
without the setting, converted and marked with it.
