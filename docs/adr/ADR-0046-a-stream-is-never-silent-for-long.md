# ADR-0046 — A stream is never silent for long, and its status waits for a model no longer than that

- **Status:** Accepted
- **Date:** 2026-10-27
- **Deciders:** the owner; AIRA maintainers
- **Amends:** `ADR-0032` §1 (the consequence "the response headers of a stream now wait for the
  first chunk")

## Context

Callers of Claude models with large contexts had their streams end as `499` after a fixed while,
with no message from the gateway at all. The gateway had sent them nothing:

- **A stream's status waited for the first chunk** (`ADR-0032` §1), so that a stream nothing can
  serve is a status and the chain can still fall back.
- **For the Anthropic dialect the first chunk is the first visible word.** `message_start`, `ping`,
  every thinking delta — empty under the newest models' default `display: omitted` — and every
  fragment of a function call's arguments produce no chunk; a turn that is only a function call
  produces its first chunk at `message_stop`. Reading a large context, thinking about it and writing
  a file through a tool takes minutes, and all of it was silence on the wire. The OpenAI dialect
  withholds reasoning the same way, which is how local thinking models met the same end.
- **Nothing filled the silence.** The KIRA surface's keepalive (`FRD-146` FR-19) started after the
  first chunk — after the silence it was written for — and the Gemini surface had none at all.

A proxy closes a connection that carried nothing for its idle timeout (nginx: 60 s), and a client
gives up on headers that do not come (Node's fetch: 300 s). The gateway noticed only when the
first chunk finally arrived, read the stream to its end for the vendor's figure (`ADR-0041`) and
recorded the caller as gone. `ADR-0032` had accepted the wait with the sentence "a caller waits for
the model either way" — true of the caller, not of what stands between the caller and the gateway.

## Options considered

- **Begin the response at once.** Nothing waits, and every refusal of a streamed request becomes a
  `200` whose body ends in an error — the indefensible case `ADR-0032` removed, for every refusal
  including the instant ones.
- **Let the Anthropic dialect's first chunk be `message_start`.** The status would go out when the
  vendor accepted the request. Dialect by dialect, and it gives up the fallback for a failure that
  arrives inside the stream before any text — Anthropic's in-stream `overloaded_error` is exactly
  that — while how long a very large context takes before `message_start` is not documented.
- **Wait for the chain no longer than a caller can be kept without a byte, then begin anyway.** A
  refusal that comes in time stays a status; the chain keeps falling back after the response has
  begun, because nothing of any model has reached the caller; and the stream sends what its clients
  skip whenever it has been quiet for that long.

## Decision

The third. **A streamed response is never silent for longer than `AIRA_STREAM_KEEPALIVE_SECONDS`
(15).**

1. **The status waits for the chain at most that long** (`api/serving/streaming.py`, `Opening`).
   The chain runs as a task of its own; what it decides in time is what it always was — a stream,
   or a refusal answered with its status. A chain still looking when the time is up goes on looking
   while the response begins: a candidate that fails before its first chunk is still passed over,
   and the next one may answer.
2. **A refusal the chain ends in after that is the stream's last element**, the same as a failure
   after the first chunk: Google's error element on the Gemini surface, which its SDKs raise, and a
   stream without `completed` on the KIRA one. Its row is the refusal's — the outcome, the status it
   would have had (`stream_failure_status`), the message, and every candidate passed over.
3. **A quiet stream sends a keepalive** whenever nothing went out for that long, before the first
   chunk and after it, in what the surface's clients skip:
   - KIRA: the predecessor's `: ping` comment, unchanged.
   - Gemini: **a bare line**. SSE defines a blank line as the end of an event, and with no event
     pending it dispatches nothing; inside the JSON array it is whitespace, as Google writes
     between its own elements. Not a comment: the `google-genai` SDK, every version from 1.0 to
     2.17, hands any line that is not `data:` to its JSON parser and raises. Its JavaScript twin
     skips a bare line from 1.21.0 (September 2025) and stalls on any keepalive before that; the AI
     SDK's parser follows the SSE specification in every major.
4. **The headers name no model on a response that began before one answered.** `X-AIRA-Model` says
   which model answered (`FRD-628` FR-2); when the headers go out, a fallback may yet answer. The
   row and the chunks name the one that did.
5. **`X-Accel-Buffering: no` on every stream**, Gemini's included: a proxy that buffers holds the
   keepalives back with the answer.
6. **`0` switches all of it off** — the status waits for the first chunk and a quiet stream stays
   quiet, as before — for an installation whose clients cannot take a keepalive.

## Consequences

- Positive: a proxy's idle timeout or a client's wait for headers no longer ends a stream whose
  model is still reading or thinking; the `499`s that were the gateway's own silence stop, and so
  does the drain that billed whole answers nobody received.
- Positive: the fallback chain covers a slow candidate as it covered a fast one.
- Negative / trade-offs:
  - **A slow stream that is then refused is a `200` with an error at its end**, not an error
    status. A client that retries on a status no longer sees one in that case; the `google-genai`
    SDKs raise the element as an `APIError` with its code, the AI SDK reports it as a stream error.
  - A client older than its keepalive — `@google/genai` before 1.21 — stalls on the first one.
    The setting's `0` is the way back for such an installation.
  - A stream is now held open for as long as the model works. The bound on it is the caller's own
    total timeout and `AIRA_DRAIN_LEFT_STREAMS_SECONDS` after a caller left — no longer a proxy's.
- Follow-ups: a **non-streamed** request cannot be kept alive this way — no byte can precede its
  status — so a long answer there still meets a proxy's timeout; streaming is the answer for long
  work, as Anthropic recommends for its own API.
