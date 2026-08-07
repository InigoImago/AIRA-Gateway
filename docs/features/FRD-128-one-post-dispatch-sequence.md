# FRD-128 — A request the caller abandoned is still a request that happened

> Phase: 8 (structural) · Status: **Done (2026-08-07)** · Owner: AIRA · Last updated: 2026-08-07
> Related: `FRD-126` (the half before dispatch), `FRD-122` (audit), `FRD-405` (reservations),
> `FRD-110` (the shield), `FRD-127`, `FRD-106` (the third surface)

## 1. Summary

`FRD-126` gave the **pre**-dispatch order one owner. This is the other half, and it was prompted by
one question: *have all the paths been tested with a dropped connection?*

The answer was no. Streaming had been — Gemini's by closing the iterator and by a live client
walking away, KIRA's by cancellation during dispatch (`FRD-127`). **Every non-streaming path had
not, and all four of them lost the audit row.** A caller who went away while the model was still
answering made a request that reached the upstream, spent tokens and spent money disappear from the
record entirely.

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
  row — shielded, so a cancelled task cannot lose it.
- **FR-2** A caller who abandons the request is recorded with status **499** and outcome
  `client_gone`. Nobody is sent that status; it exists so the audit can tell that case from a
  served one, and `client_gone` is its own outcome because "clients keep hanging up" is a
  different thing to investigate from "the provider keeps failing".
- **FR-3** Nothing chargeable produced → released. Settling would still book one request, and a
  use case with a request limit would lose allowance to a caller who hung up.
- **FR-4** An embedding produced vectors and reports **no tokens**, which is distinct from
  producing nothing — `Accounting.produced` carries that, and the batch settles as the many
  requests it is (`FRD-113` FR-6).
- **FR-5** An exception on its way to the surface's boundary releases but does **not** record; the
  boundary writes that row. One request, one row.

## 4. Design notes worth keeping

**The accounting runs inside `hold`, not around it.** Outside, `hold` sees an unresolved
reservation on the way out and gives it back — and then the settle books it again. One request,
settled once and released once. A test caught it within the minute.

**`hold` owns the release.** An explicit release in the sequence's exit counted the give-back
twice, for the same reason.

**The shield has no mutation, deliberately.** `FRD-110` established that a hermetic test cannot
distinguish in-process generator close from a real socket drop, and `FRD-127` re-verified it by
running its own disconnect test against an un-shielded build and watching it pass. A harness that
claimed to guard the shield would claim a proof nobody has. The integration layer checks it.

## 5. What it removed

    direct hold/settle/release/price calls in the surfaces:   12  →  0
    hand-written stream finishers:                             2  →  0
    record_request in a surface:                    the refusal boundary only, one per surface

## 6. Testing

- `gateway/tests/test_cancelled_requests.py` — the acceptance test, written **first** and red on
  four of four paths.
- `gateway/tests/test_kira_streaming_disconnect.py` — the streaming window.
- Mutations `Z17`–`Z20`; `Z17`/`Z18` re-anchored from the finisher this change deleted.

Two existing tests had to be rewritten, and both for the same reason: they asserted on an
**intercepted call** rather than on the effect. One monkeypatched `routes.record_request`, which
silently stopped intercepting when the write moved; the other counted calls to `release` through a
delegating stand-in that `hold`'s internal `self.release(...)` never passes through. Both now read
the row and the counter. A test coupled to *where* something happens goes quiet when it moves,
instead of failing.

## 7. Open Questions

- Whether the surfaces' refusal boundaries can share more than they do. They render different
  status codes and vocabularies, so probably not — but the *decision to record* could be one
  function even if the rendering is not.
