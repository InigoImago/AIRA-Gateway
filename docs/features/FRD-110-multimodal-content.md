# FRD-110 — Documents and images in a request

> Phase: 8 (KIRA parity) · Status: **Done** · Owner: Vadim Scheibe
> Origin: the predecessor's contract (`RequestContent`, `DocumentMimeTypeEnum`), programme: `ADR-0010`.
> Multi-vendor consequences governed by **`ADR-0012`** (documents are not uniformly supported).
> Touches: `FRD-100` (Gemini surface), `FRD-103` (persistence), `FRD-300` (pipeline),
> `FRD-401`/`FRD-405` (budgets), `FRD-404` (retention), `FRD-406` (redaction, deferred).

## 1. Problem

The predecessor accepts a prompt made of ordered **parts**: text, and binary documents in fourteen
MIME types — PDF, plain text, HTML, Markdown, CSV, XML, RTF, JavaScript, and PNG/JPEG/WebP/HEIC
images. That is not a convenience feature there; "here is a PDF, answer questions about it" is a
substantial share of what the API is used for.

AIRA cannot express it at all. `CanonicalMessage` is:

```python
class CanonicalMessage(BaseModel):
    role: Role
    text: str
```

and the Gemini surface's `Part` requires `text`, so a request carrying `inlineData` is **rejected
with a 400** rather than silently losing the attachment. That is the better of the two failure
modes, and it is still a failure: no consumer that sends a document can migrate.

This is the widest gap in the parity programme and the one every other capability sits on top of —
a thinking budget or a response schema is meaningless if the prompt cannot carry the document the
question is about.

## 2. Goals & Non-Goals

**Goals**
- A canonical request carries **ordered parts**: text and inline binary data with a declared media
  type, in the order the caller wrote them.
- The Gemini surface accepts Google's `inlineData` shape; the upstream adapters forward it.
- The accepted media types are an **allow-list**, and what a part actually contains is checked
  against what it claims to be.
- Every existing control keeps working with a document in the request: attribution, budgets, rate
  limits, the pipeline, persistence, retention. Where a control is now weaker, it says so here
  rather than degrading quietly.

**Non-Goals**
- **Files by reference** (a URL, a GCS URI, the Gemini Files API). Inline data only, which is what
  the predecessor does. A reference-based upload changes the security model — the gateway would
  fetch a caller-supplied URL — and deserves its own decision.
- **Content extraction.** The gateway does not parse a PDF; it forwards it — deliberately, because
  a PDF parser is a large attack surface on caller-supplied bytes and this process holds the cloud
  credentials. Everything downstream that wants to read inside a document (see FR-9) is out of scope
  here, and the opt-in conversion path (`FRD-121`) keeps the parser out of this process too.
- **Audio and video.** Not in the predecessor's list; the allow-list is easy to extend later.
- **Multimodal *output*.** Models answer with text.

## 3. User Stories
- As an **application developer**, I want to send a PDF with my question so that the model can
  answer about a document my users uploaded.
- As a **use-case administrator**, I want a document-carrying request to count against my budget
  as honestly as a text one, so that the limit I set means what I think it means.
- As **IT Security**, I want to know exactly which of AIRA's controls can and cannot see inside an
  attachment, so that the residual risk is a decision and not a surprise.

## 4. Functional Requirements

- **FR-1 Ordered parts.** A message is a list of parts, each either text or inline data. Order is
  preserved end to end: "this image, then this question" and "this question, then this image" are
  different prompts.
- **FR-2 Inline data.** A data part carries a declared media type and bytes. On the wire the bytes
  are base64; in the canonical model they are `bytes`. Invalid base64 is a **400**, not a
  truncated forward.
- **FR-3 Media-type allow-list, intersected with the model.** The fourteen types from
  the media types the contract lists are what *AIRA* accepts; what the **target model** accepts is declared in
  `FRD-114`, and Anthropic's set is a subset of Google's. A part is admitted only if both allow it,
  and a refusal names which of the two refused — "AIRA does not accept HEIC" and "this model does
  not accept HEIC" call for different actions. Checked against the model actually dispatched to,
  for the same reason `FRD-112` §5.3 checks its capability after routing: with a fallback chain,
  the model that answers is not the model that was asked for.
- **FR-4 The declared type is verified, not trusted.** A part claiming `application/pdf` whose
  bytes do not begin with a PDF signature is refused. The check is a magic-byte sniff, not a full
  parse: it is there to catch a mislabelled upload and a trivially disguised payload, and it is
  documented as exactly that much.
- **FR-5 Bounds.** A per-part size limit, a per-request total, and a maximum number of parts. The
  existing body ceiling (`AIRA_MAX_REQUEST_BYTES`, 8 MiB) still applies and is now the outer bound
  of several: base64 inflates by a third, so an 8 MiB body carries at most ~6 MiB of document.
- **FR-6 Budget estimation accounts for attachments.** See §5.3 — this is the requirement most
  likely to be got wrong, and the one with money attached.
- **FR-7 Persistence stores a description, not the bytes.** See §5.4.
- **FR-8 Every verb behaves the same.** `generateContent`, `streamGenerateContent` and the
  pipeline's dry-run all accept the same parts. `embedContent` does not — embedding a document is
  a different operation and is not in scope (`FRD-113`).
- **FR-8a A chain may not degrade a document request silently** (`ADR-0012` §3). A fallback
  candidate that cannot read the attachment is **skipped**, never used with the attachment dropped;
  if no candidate qualifies, the request **fails**. Falling back to a text-only model produces a
  fluent, confident answer about a document the model never saw, returned with a 200 and
  indistinguishable from a correct one. Failing is recoverable; being quietly wrong is not. An
  opt-in conversion path exists (`FRD-121`) and is never implicit.
- **FR-9 The text-only controls declare their blind spot.** The injection filter and the routing
  classifier read text. A prompt injection inside a PDF is invisible to them. The gateway does not
  pretend otherwise: the limitation is documented in the pipeline builder's inline help, and the
  request log records that a request carried attachments so an audit can find them.

## 5. Design & Architecture

### 5.1 The canonical model changes shape

This is the one breaking change in the programme, and it is better made once, deliberately:

```python
class TextPart(BaseModel):
    text: str


class DataPart(BaseModel):
    media_type: str  # from the allow-list
    data: bytes  # decoded; base64 is a wire concern


CanonicalPart = TextPart | DataPart


class CanonicalMessage(BaseModel):
    role: Role
    parts: list[CanonicalPart]
```

`CanonicalMessage.text` disappears as a field and comes back as a **derived property** — the
concatenation of the text parts — because a great deal of existing code legitimately wants "what
does this message say": the injection filter, the routing classifier, `last_user_text()`, the mock
provider, the dry-run. Those call sites keep working unchanged and keep their current semantics,
which is exactly what FR-9 is about: they see the text and not the document.

Two consequences worth naming now. `text` becomes lossy where it was total, so anything that
*persists* or *decides* on it must be reviewed rather than merely compiled — §5.4 and FR-9 are
those reviews. And `CanonicalRequest.messages[*].text` is used in the pipeline's config-driven
steps, whose behaviour must not change for a text-only request: that is a test obligation, not a
comment (`§10`).

### 5.2 Surfaces and upstreams

- **Gemini surface** (`api/gemini/schemas.py`): `Part` becomes a union of `{text}` and
  `{inlineData: {mimeType, data}}`, matching Google's wire format. A part with neither, or both,
  is a 400.
- **Gemini upstream** (`upstreams/gemini_mapping.py`): canonical parts map straight back to
  `inlineData` — the two formats agree, which is why this direction is cheap.
- **Anthropic upstream** (`FRD-119`): the same parts map to `image` / `document` content blocks.
  This is the mapping that tests whether the ordered-parts model is genuinely neutral or merely
  Gemini's shape renamed — see `FRD-119` §5.2.
- **Mock provider**: must accept attachments and describe them deterministically ("1 attachment:
  application/pdf, 12 KiB"), so the demo path and every hermetic test can exercise the feature
  without a real upstream. A mock that ignores attachments would let every test pass while the
  real path was broken.

### 5.3 Budgets: an attachment costs tokens no character count predicts

`_estimate` (FRD-405) reserves before dispatch from `maxOutputTokens` or a configured default,
priced at the output rate. It has never estimated *input*, and for text that was defensible: the
output rate dominates and the reservation is settled against the real figure moments later.

An image or a PDF breaks that. Google bills an image at a fixed token count per tile and a PDF per
page — hundreds to thousands of input tokens that no property of the request body predicts except
its size and type. A request whose real input is 20 000 tokens would reserve as if it were a
sentence. Under concurrency that is precisely the race `FRD-405` closed for text and would reopen
for documents.

So the estimate gains an **input component for attachments**: a per-media-type token estimate
(configurable, defaulting to conservative published figures), multiplied by size where the model
prices by area or page. The figures differ per vendor — Google and Anthropic tokenise images and
PDFs differently — so the estimate is declared per model in `FRD-114`, not global. It will be wrong — deliberately wrong **high**, the same direction the
output estimate already errs, and corrected by `settle` the moment the response returns with real
`usageMetadata`.

What must not happen is the estimate silently staying at zero for attachments. That is the
"unknown is not zero" rule this codebase already applies to unpriced models, and the same argument
applies: a reservation that ignores the expensive half of the request is not a limit.

### 5.4 Persistence: store what it was, not what it contained

`request_logs.request_payload` is `JSONB`. Writing a base64 PDF into it makes each row megabytes,
multiplies the table by an order of magnitude, and puts binary the gateway never inspected into a
column that retention deletes on a schedule and that redaction (`FRD-406`) is supposed to mask —
and cannot, because it cannot read inside it.

So the stored payload carries a **description** of each data part and never its bytes:

```json
{"kind": "data", "media_type": "application/pdf", "bytes": 124309,
 "sha256": "9f2c…", "index": 2}
```

That keeps everything an audit actually asks — that a document was sent, of what type, how large,
in which position, and whether two requests carried the same one — at a constant cost per row and
with no unreadable binary sitting inside the retention and redaction boundary. The digest is the
part that earns its place: it links repeated submissions of the same file without storing it once.

`AIRA_STORE_PAYLOADS` and the per-use-case `store_payloads` (FRD-404) still govern whether even
this much is written.

> A deployment that genuinely needs the original bytes for audit needs object storage with its own
> lifecycle and its own access control, not a JSONB column. That is a separate decision and is
> deliberately not made here.

### 5.5 Where the checks run

Decoding, the allow-list, the magic-byte check and the bounds all run **at the surface**, before
the canonical request exists — the same place the body ceiling already runs. Nothing downstream
should ever hold a part that failed a check, and no upstream adapter should have to repeat one.

## 6. Data Model

No schema migration. `request_payload`/`response_payload` are `JSONB` and gain the part-description
shape above; existing rows stay valid.

## 7. API / Interface Contract

Request (Gemini surface, unchanged shape — the `Part` union is the addition):

```json
{ "contents": [ { "role": "user", "parts": [
      { "text": "Welche Zutaten brauche ich?" },
      { "inlineData": { "mimeType": "application/pdf", "data": "<base64>" } } ] } ] }
```

New errors, all `400 INVALID_ARGUMENT` on the Gemini surface (`FRD-107` maps them to KIRA's
vocabulary if that surface is built):

| Condition | Message names |
|---|---|
| `data` is not valid base64 | the part index |
| media type outside the allow-list | the type and the allowed set |
| content does not match the declared type | the declared type |
| a part, the total, or the part count exceeds its bound | which bound and its value |

## 8. Security & Privacy

- **The attack surface grows.** The gateway now accepts arbitrary bytes from an authenticated
  caller and forwards them to a third party. It deliberately does **not** parse them, which is what
  keeps that surface small: the magic-byte check reads a handful of bytes and makes no attempt to
  be a content scanner (FR-4 says so plainly, so nobody later mistakes it for one).
- **Malware scanning is not in scope and its absence is a stated risk.** A deployment that needs it
  needs a scanner in front of the gateway or a hook at the surface; the hook point is §5.5.
- **Injection inside a document is not detected** (FR-9). This is the residual risk IT Security
  most needs stated, because the pipeline's injection filter reads as though it covers the whole
  prompt and now does not.
- **Attachments are never written to logs, spans or the request log's payload column** (§5.4).
  Their description is; the digest is a one-way function of content and is safe to store.
- The existing per-use-case `store_payloads` and retention clocks apply unchanged.

## 9. Observability

- `aira.request.parts` and `aira.request.attachment_bytes` as span attributes — enough to see that
  a slow request was slow because it carried 6 MiB, without recording anything about the content.
- The audit row records the same, per §5.4.

**Built on 2026-08-31, not with the feature.** Both were named here and set by nothing, which is
the shape `LESSONS.md` §7 records for an Observability section: a claim no test can reach is a
claim that will be wrong. They are set in `prepare_for_dispatch` — the one function both surfaces
and every verb pass through (`FRD-126`) — rather than at a surface, because a shape attribute
written at one surface is one the next surface does not write. `attachment_bytes` is **absent**
rather than zero on a request that carries none, for the reason the tool figures are
(`persistence/recorder._tool_attributes`): a zero on every ordinary request turns *"which traffic
carries documents"* from an existence check into a comparison.

## 10. Testing & Acceptance Criteria

- **Unit** — the surface: valid base64 decoded; invalid rejected; each allow-listed type accepted;
  one non-allow-listed type rejected; a mislabelled PDF rejected; each bound enforced at its
  boundary; part order preserved through both mappers.
- **Unit** — the canonical change: `text` still returns exactly what it used to for a text-only
  message; the pipeline steps, `last_user_text()` and the dry-run behave identically for a
  text-only request **before and after** the change. This is the regression that would otherwise be
  found in production.
- **Unit** — budgets: a request with an attachment reserves materially more than the same request
  without one; the reservation is settled to the real usage afterwards.
- **Unit** — persistence: a stored payload contains the description and, asserted explicitly,
  **does not contain the base64 anywhere in the row**.
- **Integration** — a document-carrying request round-trips through the live stack; the audit row
  holds the description; the row size stays bounded.
- **e2e** — the pipeline builder's dry-run accepts an attachment and states that the text-only
  steps did not read it.
- **Mutation** — at least: the allow-list actually restricts (not merely present); the magic-byte
  check actually compares; the attachment estimate is non-zero; the payload writer strips bytes.
  Each entry added to `tools/mutation_check.py` and verified to be caught.

**Acceptance**
- *Given* a use case with a budget, *when* a caller sends a 2 MiB PDF with a question, *then* the
  model answers about the document, the reservation reflected the attachment, the settled figure
  matches the upstream's `usageMetadata`, and the audit row records the type, size and digest and
  no bytes.
- *Given* a request declaring `application/pdf` and carrying a PNG, *when* it is submitted, *then*
  it is refused with a message naming the declared type, and nothing was forwarded upstream.

## 10a. What was actually built (2026-08-06)

`CanonicalMessage` carries ordered parts, `attachments.py` holds the checks, the Gemini surface
takes `inlineData`, both dialects map it, the mock *sees* it, the reservation counts it, and the
audit row keeps a description.

**The requirement the owner stated is the one everything serves**: a model that cannot read the
document is refused, by name, with the types it lacks. Sending the prompt without the attachment
would produce no error — it would produce a fluent, confident answer about a document the model
never saw, with a 200, and the caller would report that the model is hallucinating and look for the
fault everywhere except where it is.

Four decisions worth keeping visible:

- **`text=` still constructs a message and `.text` still reads one.** That is what made this a
  change to one file rather than twenty — the whole existing suite passed unmodified against the
  reshaped model. It is also the thing to be careful about: `.text` was total and is now lossy, so
  the pipeline's blind spot (FR-9) is a *property with a test*, not a comment.
- **Stripping is not redaction.** Attachment bytes are removed before the redactor runs and
  unconditionally, because a deployment that swaps the redactor must not be able to turn it off.
- **The mock describes what it was sent.** A mock that ignored attachments would let every hermetic
  test pass while the real path was broken, and the feature would be exercised only against a cloud
  nobody has in CI.
- **Embedding refuses an attachment** rather than embedding the prompt without it — the same rule
  one level down.

Coverage: 24 hermetic tests, 4 integration tests, mutations **F1–F11**, each verified to be caught.

### The defect the integration layer found

Running the suite repeatedly to check for flakiness turned up a failure at roughly one run in
eight: a client that dropped the socket mid-stream sometimes **vanished from the audit log**.

The cause had nothing to do with documents and everything to do with what a hermetic test can see.
Closing a generator from inside the process raises `GeneratorExit`, and awaits in a `finally` run
normally — which is why `test_a_client_that_disconnects_mid_stream_does_not_leak_the_reservation`
passes deterministically. A real socket dropping **cancels the response task**, and a bare `await`
in that `finally` re-raises `CancelledError` at its first suspension point: the settle and the row
were lost. `FRD-405` B4 had promised this path is accounted for; it was, in-process.

The accounting is now `asyncio.shield`ed. Deliberately **not** given a mutation entry: no hermetic
test can distinguish the two versions, so an entry would be a false claim — and a harness that
makes one is worse than no harness. The guard lives in the integration suite and the test says so.

## 11. Dependencies & Risks

- **Risk — the canonical change reaches everything.** Every surface, both upstreams, the mock, the
  pipeline, persistence and the dry-run touch `CanonicalMessage`. Mitigated by keeping `text` as a
  derived property and by the before/after equivalence tests above, which are the actual safety
  net.
- **Risk — the attachment token estimate is a guess.** It is, and it is a guess in the safe
  direction that is corrected within milliseconds. The alternative (no estimate) is the one that
  loses money.
- **Open** — whether any consumer needs a media type outside the fourteen. The allow-list is
  configurable, so this is not blocking.

## 12. Rollout / Demo

The mock provider describes attachments deterministically, so demo mode gains a working
document-question flow with no cloud credentials. Seed data adds a small PDF fixture.
