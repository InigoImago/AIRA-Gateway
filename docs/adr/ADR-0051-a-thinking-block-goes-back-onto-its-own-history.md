# ADR-0051 — A thinking block goes back onto the history it was made on, on every turn

- **Status:** Accepted
- **Date:** 2026-11-02
- **Deciders:** AIRA maintainers; for the owner's review, not yet committed
- **Amends:** `ADR-0047` point 4, `FRD-152` FR-3, FR-5 and §5.2

## Context

`ADR-0047` put a Claude model's thinking back only in the turn in progress. Anthropic requires it
there and allows it out of earlier turns, and a block is valid only while everything before it is
unchanged, which OpenCode does not promise: it clears old tool results.

That decision looked at the check and not at the cache. A prompt cache is a prefix match over
consecutive requests. From Opus 4.5 and Sonnet 4.6 on, models keep earlier turns' thinking in context
and in the cache, and Anthropic's own page says a dropped block changes the cached prefix from its
position onward. So at each new message of the caller's, the request stopped matching the one before
at the first block of the last turn: on `cache_prefix`, the conversation from there on was written
again at 1.25 or 2 times the input price instead of read at a tenth or less, and the provider read
it anew before the first word. Past twenty positions, which a long tool loop passes, the provider
does not look back far enough to find what it wrote at the turn's start, and all of it went. On
300 000 tokens with Opus 5.5 that is about 1.50 dollars against 6 cents, for every message, and the
minutes of silence the owner's `499`s came from.

## Options considered

- **Keep it, and add a read point at the turn's start.** Saves everything before the last turn, risks
  nothing, leaves the last turn's rewrite on every message. Built as well: it is what helps a model
  that drops a finished turn's thinking itself, and a tool loop longer than the provider looks back.
- **Echo every turn's blocks, and let a refusal sort it out.** Right until a client edits its history.
  From then on, on an account Anthropic enforces this for, every request is refused once before it
  goes through, because the gateway keeps no state to remember the refusal in, and thinking made since
  is lost with the rest. Anthropic calls that the failure mode: an unrecorded strip fails every turn.
- **`prefix_mismatch_behavior: "drop_block"`.** Anthropic's own answer, behind a beta header on
  Vertex. Nothing here can test it against Claude, a rejected header would refuse every request, and
  its page warns against it as a standing setting.
- **State in the envelope what the blocks were made on.** A digest of the system prompt, the set of
  tools and the messages before them, worked out from what the caller sent. Stateless, decided before
  anything is sent, and it tells exactly which blocks a change invalidates.

## Decision

The last, with the read point of the first.

1. **An envelope says what its message was made on**: a digest over the system prompt, the tools as a
   set and the messages before it, without thinking blocks, markers, a text's outer whitespace, or the
   order of a JSON object's keys — Anthropic counts none of those as a change. Stricter than
   Anthropic's check, it costs a block that was still good; it is not weaker.
2. **A block goes back wherever its digest matches**, and stays out where it does not. Blocks made
   since a change were made without the ones it invalidated, so each still follows the block it was
   made after, which is the rest of what Anthropic checks.
3. **The turn in progress goes back as before, whatever its digest says.** Anthropic requires its
   blocks, and judges them itself; before a fallback to carrying nothing, it costs one refused request,
   as it did.
4. **A refusal gives up earlier turns' blocks first**, then the rest. Each attempt is a refused
   request, which Anthropic does not bill, and the second is what this gateway sent before this
   decision: what it adds can cost a request, never the conversation.
5. **An envelope that says nothing**, sealed before this, is treated as before: its own turn only. A
   conversation in progress across an upgrade goes on.

## Consequences

- Positive: a conversation reads what the request before it wrote, across messages of the caller's as
  across steps of a tool loop: the signature Anthropic calls a healthy loop.
- Positive: a client that changes its history refuses nothing, and a turn made since keeps its blocks.
- Positive: the model keeps its reasoning across turns, which Anthropic recommends.
- Negative / trade-offs:
  - On the models that keep them, earlier blocks count as input for the rest of the conversation:
    read from the cache at its rate, and against the context window. OpenCode compacts by what is
    reported, so somewhat earlier.
  - About 6 ms of the event loop per MiB of conversation, measured, once per request.
  - A client's own edit still ends the cache at that point, as it would anywhere.
- **Not verified against Claude.** No model is reachable from this environment. The rules are held in
  a stand-in that refuses what Anthropic documents it refuses, and point 4 bounds what a mistake
  costs. The first thing to read after a deployment is `cache_read_input_tokens` growing from one
  message to the next, and `carried_thinking_refused` in the log staying absent.
