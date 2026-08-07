# FRD-126 — A surface parses; the layer decides

> Phase: 8 (structural) · Status: **Done (2026-08-07)** · Owner: AIRA · Last updated: 2026-08-07
> Related: `ADR-0010` (a second surface), `FRD-107`, `FRD-405`, `FRD-125`, `FRD-106` (the third)

## 1. Summary

`api/serving.py` was extracted so that two API surfaces could share everything below the wire
format, and its own docstring states the rule: a surface owns *"parsing its own wire format,
rendering its own error envelope, and its own routes"*. It shared the **steps**. It did not share
the **order** — and both surfaces went on writing the same six calls out by hand, the KIRA one
spread across four functions.

That is not untidiness. **Every guarantee this layer makes is a guarantee about the order:**

    rate limit before the pipeline   or a refused request pays for a classifier call
    declaration after routing        or a cap is checked against a model that never serves it
    thinking after routing           or a budget is validated against the wrong model
    reservation last                 or it is made against the model the caller *named*

None of those is expressible in a function that only knows its own step, which is why the same gap
kept reappearing under different names: `:embedContent` bypassing the pre-dispatch gate (`FRD-405`
B3), then the KIRA surface losing its rate limiting entirely the moment one take moved one function
over (`FRD-125c`). Both were caught. The question that produced this FRD was the right one to ask
next: *if a third surface is added, does it write those six steps a third time?* It did.

## 2. Goals & Non-Goals

**Goals**
- One function owns the pre-dispatch order; a surface calls it and cannot get the order wrong.
- A surface module contains none of the individual steps, and a test says so.
- `FRD-106`'s OpenAI surface inherits the sequence instead of reproducing it.

**Non-Goals**
- Changing what any of the steps do. This is a move, and the test suite is the evidence: **887
  gateway tests passed without a single test being modified.**
- Merging the surfaces' parsing or error envelopes. Those are exactly what a surface is *for*.

## 3. Functional Requirements

- **FR-1** `prepare_for_dispatch` performs, in order: empty check → weigh → rate limit and
  budget-exhausted check → pipeline → provider re-resolution after routing → declaration check →
  thinking resolution → embedding validation → reservation.
- **FR-2** It returns everything the caller then needs (`Prepared`), so a surface never
  reconstructs an intermediate.
- **FR-3** A surface module calls none of the steps directly. Enforced by
  `test_surface_layering.py`, not by review.
- **FR-4** Reaching a reservation without the sequence's gate raises, as a backstop for code that
  bypasses the function entirely.
- **FR-5** Surface-specific behaviour is a **parameter**, not a fork: the KIRA surface's default
  embedding task type is passed in.

## 4. Design

### 4.1 What moved and what stayed

Moved into the sequence: the six shared steps, plus the provider re-resolution after routing that
both surfaces had written for themselves.

Stayed in each surface: parsing its wire shapes, resolving its own model identifier (the KIRA
surface addresses models by the predecessor's integer ids), refusing what its own contract does not
serve, and rendering its own error envelope. A shared error raised from the sequence — a model no
provider serves — is rendered by whichever surface caught it, which was already the pattern.

### 4.2 Why an assertion and not a convention

The same shape as `test_no_code_above_the_adapters_knows_the_vendor`. A layering rule that only a
reviewer enforces is a rule the *next* surface breaks, and the whole point of this change is that
the next surface is the one nobody is watching yet.

### 4.3 What it is worth

The KIRA surface went from six of these calls to **zero**. The evidence that nothing else changed
is that no test changed: 887 hermetic and 316 live, all green, before and after.

## 5. Testing

- `gateway/tests/test_surface_layering.py` — parses each surface and fails on a direct call to any
  step; and, for a surface that dispatches, requires that it prepares through the sequence, so a
  surface cannot pass by doing nothing.
- Mutations `Z15` (thinking resolved after routing) and `Z16` (the sequence is the entry point).
- **Four existing mutations came back `STALE`** — the harness distinguishes that from "survived",
  and all four pointed at lines this change moved into the sequence. Re-anchored there. A fifth,
  `Z13`, was **removed**: it claimed the compatibility surface takes the same early gate, which was
  a distinct property only because each surface took the gate for itself. Its anchor and `Z11`s are
  now the same line, and two mutations on one line measure one thing twice — the call `X3` got.

## 6. Rollout

No behaviour change and no migration. The observable difference is that the third surface is
cheaper and cannot be wrong in this particular way.

## 7. The other half, which is not done

Assessing what a third surface would now cost turned up the honest limit of this change: **the
pre-dispatch order is shared and the post-dispatch order is not.**

    hold → dispatch → check result → price → settle → record

is written out **six times** — three verbs in each surface. That is the same shape as the problem
this FRD fixed, one step later in the request, and it has already cost a defect:

> Gemini's streaming path wraps its accounting in `finally` + `asyncio.shield`, with a comment
> explaining why: a client dropping a real socket **cancels** the response task, and a bare `await`
> in the `finally` loses the settle and the audit row. It was found by the integration layer as a
> 1-in-8 flake.
>
> **The KIRA streaming path has neither.** The surface written second did not get the fix the first
> one earned. Its exposure is narrower — that "stream" delivers one terminal event, so the window
> is between dispatch returning and the settle — but it is the same defect, in the place
> duplication always leaves it.

So the answer to *"would a third surface generalise easily?"* is: **half of it would.** The
recommended order is

1. close the KIRA streaming gap (small, standalone) — **done, `FRD-127`**,
2. consolidate the post-dispatch sequence the way this FRD consolidated the pre-dispatch one — **done, `FRD-128`**,
3. then `FRD-106`.

Doing `FRD-106` first would mean a seventh copy of that sequence, and a third chance to make the
same mistake.

## 8. Open Questions

- Whether the post-dispatch consolidation can cover *streaming* as cleanly as the non-streaming
  path. The generator's lifetime is the hard part, and it is exactly the part that has been got
  wrong before — which argues for doing it deliberately rather than as a side effect of adding a
  surface.
