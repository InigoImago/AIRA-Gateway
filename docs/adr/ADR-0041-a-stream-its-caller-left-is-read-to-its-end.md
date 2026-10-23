# ADR-0041 — A stream its caller left is read to its end

- **Status:** Accepted
- **Date:** 2026-10-22
- **Deciders:** the owner; AIRA maintainers

## Context
A streamed answer whose caller hangs up before the last chunk was closed with the caller. Several
dialects report what an answer used only in its **last** chunk: an OpenAI-compatible server (Azure,
Foundry, the open models on Vertex, any self-hosted one) sends `usage` after the finish, and
Anthropic says its input at the start and its output only at the end. Closed early, such a stream
had nothing to book: Vertex's Gemini could still be counted at its free `:countTokens` endpoint
(`FRD-128` FR-6), Anthropic booked its input alone, and every other dialect handed the reservation
back and booked **nothing** — tokens, cost and the request itself.

The 2026-10-22 security check found that this can be done on purpose: read the answer, leave
before the last chunk, and no budget ever sees the request. It also happens without intent, all
day: a coding assistant cancels its stream whenever its user presses Esc or sends the next
message. A budget that cannot see these requests is not a limit on what is spent.

## Options considered
- **Close with the caller, and book the reservation** where nothing was reported. Simple and on
  the safe side, but the reservation is an upper bound — the model's whole output allowance — so
  every early cancel books far more than the vendor billed, and a reader of the reports cannot
  tell a booked guess from a vendor's figure.
- **Close with the caller, and estimate** from the characters received. Booked figures stop being
  facts, which `FRD-128` FR-6 refused for the counted floor and which the reports cannot label
  apart from real ones.
- **Read the stream to its end** after the caller left, discarding what nobody receives, and book
  the vendor's own final report. The figure is the vendor's, on every dialect.

## Decision
**The upstream is read to its end** — the owner's choice, for real figures. The upstream's chunks
are read by one task of their own (`api/serving/pump.py`): every chunk is shown to the accounting
as it arrives, whether or not it is ever handed to the caller, and a stream whose caller left is
read on until the vendor's end, then booked at the vendor's last report and recorded as `499` /
`client_gone`. What the caller did not receive is not stored; the row keeps what was delivered.

It is bounded twice. `AIRA_DRAIN_LEFT_STREAMS_SECONDS` (600) caps how long one stream is read on —
`0` closes it with the caller as before. And a stopping server cuts every read short: the
application's shutdown sets a signal each read waits on, and waits a few seconds for those requests
to book what they know and write their rows before the audit writer closes. Cut short or never
reported, a stream falls back to what was there before: the count where the vendor has one, the
running total where it reported one, otherwise unknown and the reservation returned.

## Consequences
- Positive: what a left stream cost is the vendor's own figure, on every dialect; hanging up no
  longer makes a request free, and a budget sees an agent client's cancelled streams.
- Negative / trade-offs:
  - **A finished answer is billed whole.** A vendor that stops generating when its client goes
    away bills only what it produced; read to its end, the answer is produced and billed in full.
    For a caller who cancels early in a long answer this costs more than closing would have — the
    price of a figure that is true. The setting turns it off.
  - A left stream holds its upstream connection and its reservation until the vendor finishes —
    against the adapter's connection pool (httpx: 100 per adapter, 20 kept alive) like a stream
    whose caller stayed.
  - A server stops only once its requests are done. uvicorn waits for running requests before the
    application's shutdown begins, so the image passes `--timeout-graceful-shutdown`: past it,
    uvicorn proceeds, the reads are cut short, and the rows are still written.
- Follow-ups: a vendor-side cancel that also reports usage — none of the dialects in use offers one
  today — would make the trade-off unnecessary.
