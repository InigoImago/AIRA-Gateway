# FRD-128 — A request the caller abandoned is still a request that happened

> Phase: 8 (structural) · Status: **Done** · Owner: AIRA
> Related: `FRD-126` (the half before dispatch), `FRD-122` (audit), `FRD-405` (reservations),
> `FRD-110` (the shield), and the KIRA cancelled-stream fix cited below as `FRD-127` — a label from
> before that number went to [*Several gateway instances*](FRD-127-several-gateway-instances.md);
> the fix never had a document.

## 1. Summary

`FRD-126` gave the **pre**-dispatch order one owner. This is the other half, and it was prompted by
one question: *have all the paths been tested with a dropped connection?*

The answer was no. Streaming had been — Gemini's by closing the iterator and by a live client
walking away, KIRA's by cancellation during dispatch (the label `FRD-127`). **Every non-streaming
path had not, and all four of them lost the audit row.** A caller who went away while the model was
still answering made a request that reached the upstream, spent tokens and spent money disappear
from the record entirely.

Six paths — three verbs on each of two surfaces — each with its own hand-written copy of

    hold → dispatch → check → price → settle → record

and the guarantee is the *order*, exactly as before. Two of the six were right.

## 2. Goals & Non-Goals

**Goals**
- One owner for the post-dispatch order, used by every verb of every surface.
- A request that reached an upstream is recorded however it ended: served, refused, or abandoned.
- Nothing chargeable produced means the reservation is **released**, never settled.
- The shield is structural, not something each new path has to remember.

**Non-Goals**
- Moving the **refusal** row. `FRD-122` put it at each surface's exception boundary because the
  status code and the outcome vocabulary are the surface's own. That stays; the sequence
  deliberately does not write a second row for an exception on its way there.
- Changing what any step does. Evidence: 1193 hermetic and 316 live tests green.

## 3. Functional Requirements

- **FR-1** `accounting()` holds the reservation and, on exit, settles or releases and writes the
  row — as **one unit that runs to its end** (`shielding.uninterrupted`), so a cancelled task can
  neither lose the row nor resolve the reservation twice. A streamed response accounts for the two
  endings its body cannot see — cancelled while a frame was sent, or before the body began
  (`serving.streaming.AccountedStream`).
- **FR-2** A caller who abandons the request is recorded with status **499** and outcome
  `client_gone`. Nobody is sent that status; it exists so the audit can tell that case from a
  served one, and `client_gone` is its own outcome because "clients keep hanging up" is a
  different thing to investigate from "the provider keeps failing". That holds **after usage has
  arrived** too: the Gemini surface once read "some usage" as "answered" and recorded a caller who
  left after the first chunk as `200 served`. What the upstream reported is settled — it was spent
  and reached the caller — under `499`, on both surfaces alike. A report that came **before** the
  answer ended is the vendor's running total, short of what the answer went on to cost — a Claude
  stream says its input at the start and its output only at the end — so the row is marked
  `usage_basis = partial`, a floor.
- **FR-3** Nothing chargeable produced → released. Settling would still book one request, and a
  use case with a request limit would lose allowance to a caller who hung up. "Nothing produced"
  means nothing anybody can show was produced: a stream whose first chunk arrived has had its
  prompt read, and is counted (FR-6).
- **FR-4** An embedding produced vectors and reports **no tokens**, which is distinct from
  producing nothing — `Accounting.produced` carries that, and the batch settles as the many
  requests it is (`FRD-113` FR-6).
- **FR-5** A **refusal** on its way to the surface's boundary releases but does **not** record; the
  boundary writes that row. One request, one row. A refusal of an answer the upstream **already
  gave** — a document it did not finish — is the exception to the release: the provider billed it,
  so what was spent is settled, and the boundary's row carries its tokens and cost (outcome
  `upstream_error`). Released, schema requests that reliably truncate were spend no budget saw. **Any other exception** is this gateway failing,
  which no boundary records, so the sequence writes it: status `500`, outcome `internal_error`.
  It once wrote nothing for those too — found when a live round's embeddings failed with a 500 and
  left the audit trail without a trace of them.

- **FR-6** A stream whose caller left **before its vendor reported any usage** is counted from
  facts, never estimated. Vertex's Gemini sends `usageMetadata` on every chunk, but with no count in
  it until the last one (`trafficType` only — measured on 2026-10-13); read as a report, every such
  stream was booked as having cost nothing, though the prompt had been read and an answer begun,
  both billed. In the exit, where the caller is gone and nobody waits:
  - the request **as it was sent** and the answer **as it was received** are counted by the
    vendor's own tokenizer at its free endpoint (`:countTokens`), in the region that answered —
    measured exact against the billed `promptTokenCount` and `candidatesTokenCount`;
  - that is booked and recorded with `usage_basis = counted`. The answer the vendor produced after
    the caller left and its thinking are in no count, so it is a **floor**; the row stores the two
    counts and leaves the cache split and the thinking unknown, never zero;
  - it is priced as a floor too: a prompt shorter than Google's smallest implicit-cache minimum
    (1024 tokens) at the ordinary rate, which is then exact, a longer one as cached, so the price
    never exceeds the bill;
  - reports count such rows (`partial_usage_requests`, `partial` rows included) and say the spend
    is a lower bound — on the reporting screen and in the exported file.
  A vendor with no counting endpoint, a count that fails or does not answer within 5 s, and
  `AIRA_COUNT_CANCELLED_STREAMS=false` leave the usage unknown and the reservation released, as
  before. Only Vertex's Gemini counts today: Anthropic reports its input at the start (FR-2), an
  OpenAI-compatible server offers no count, and Google AI Studio is unverified and `global`-only.
  Vertex's managed open models have the same count-less `usage` on every chunk of the OpenAI
  dialect and no count endpoint (`FRD-115` FR-2a): the dialect reads a `usage` with no count as no
  report, so such a stream is unknown rather than booked at zero.
  Counting sends the request and the received answer to the same vendor and region a second time;
  the privacy notice says so (`FRD-625`, edition 2026-10-13).

## 4. Design notes worth keeping

**The accounting runs inside `hold`, not around it.** Outside, `hold` sees an unresolved
reservation on the way out and gives it back — and then the settle books it again. One request,
settled once and released once. A test caught it within the minute.

**The exit resolves the reservation; `hold` is the backstop.** The exit settles what was produced
or releases it, inside the same uninterrupted unit as the row, and `hold` releases only what that
unit could not resolve. With `hold` doing the release, a level-triggered cancellation (anyio's, which
Starlette uses for a streamed body) carried control out of the exit while the settle still ran:
`hold` released a reservation the settle then corrected — resolved twice, and every abandoned stream
lowered the shared counter by the difference.

**The shield has a test now.** A hermetic test that cancels with one `task.cancel()` cannot tell a
shielded exit from an unshielded one, which is why this said "no mutation". Driving the application
the way uvicorn does — ASGI 2.3, `http.disconnect` from `receive`, the real ledger scripts —
does: `test_cancelled_requests.py` sees one adjust where an unshielded build makes two.

## 5. What it removed

    direct hold/settle/release/price calls in the surfaces:   12  →  0
    hand-written stream finishers:                             2  →  0
    record_request in a surface:                    the refusal boundary only, one per surface

## 6. Testing

- `gateway/tests/test_cancelled_requests.py` — the acceptance test, written **first** and red on
  four of four paths.
- `gateway/tests/test_kira_streaming_disconnect.py` — the streaming window.
- Mutations `Z17`–`Z20`; `Z17`/`Z18` re-anchored from the finisher this change deleted.
- FR-6: `gateway/tests/test_a_cancelled_stream_is_counted.py` (the count itself),
  `test_cancelled_requests.py` (booked once, priced at the floor, both surfaces, every way of
  leaving), `test_vertex.py` (where and what is counted), `test_gemini_upstream_mapping.py` (a usage
  object with no count is no report), `test_reporting.py`, `test_csv_export.py` and the reporting
  page's spec; mutations `COUNT-1`–`COUNT-14`. Measured against live Vertex: the count equals the
  billed figures exactly, for the request with and without tools and system text, and for answers.

Two existing tests had to be rewritten, and both for the same reason: they asserted on an
**intercepted call** rather than on the effect. One monkeypatched `routes.record_request`, which
silently stopped intercepting when the write moved; the other counted calls to `release` through a
delegating stand-in that `hold`'s internal `self.release(...)` never passes through. Both now read
the row and the counter. A test coupled to *where* something happens goes quiet when it moves,
instead of failing.

## 6a. Scope

The third surface that prompted this line of work (`FRD-106`) was **withdrawn by the owner on
2026-08-07**: it was a thought experiment, not a plan. What this change fixed is in the code that
already exists — four of six paths losing the audit row on a dropped connection. See `FRD-126`'s
scope decision.

## 7. Open Questions

- Whether the surfaces' refusal boundaries can share more than they do. They render different
  status codes and vocabularies, so probably not — but the *decision to record* could be one
  function even if the rendering is not.
