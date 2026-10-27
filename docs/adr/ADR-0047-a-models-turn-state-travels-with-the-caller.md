# ADR-0047 — A model's turn state travels with the caller, sealed into a function call's signature

- **Status:** Accepted
- **Date:** 2026-10-27
- **Deciders:** the owner; AIRA maintainers
- **Amends:** `FRD-135` FR-9 (thought parts a caller replays are still left out; the thinking a
  Claude turn needs travels elsewhere), `FRD-119` (the refusal of a budget that continues a call)

## Context

Anthropic needs a model message back as it was produced when its function calls are answered:
every `thinking` and `redacted_thinking` block, complete and unmodified, in its place. The model
continues the same response, and its reasoning so far is part of it. Under a thinking budget it
stops thinking for the rest of the turn without them; adaptively it reasons again from nothing at
every step of a tool loop — billed twice, and silent on the wire while it does, which on large
contexts is the silence that ended streams as `499`s (`ADR-0046`). Claude Opus 5.5, Sonnet 5.5 and
Fable 5.1 also write their notes between calls into thinking blocks.

This gateway never carried those blocks. A Gemini-speaking client has no field for one; the gateway
reduced a block to reasoning text, dropped its signature, and sent every later turn without it.
For a budget it refused the continuation by name, and so a thinking budget could not be used by any
agent.

## Options considered

- **Keep the blocks on the gateway**, keyed by the call they preceded. State between requests: it
  needs a store every instance shares (`FRD-127`), a retention rule of its own for what is the
  model's reasoning, and it is lost on a restart or a second region.
- **Return the blocks as thought parts** and read them back from the caller's history. The clients
  merge consecutive thoughts into one and drop a thought with no text — every block of the newest
  models, whose thinking arrives empty — so the blocks do not come back whole.
- **Seal them into the signature of the message's first function call.** The field is opaque to
  every client and comes back on that call: Google's thinking models put theirs there, and the
  clients hand it back unread — measured with OpenCode 1 and 2, Google's SDK and the AI SDK from
  its second major.

## Decision

The third.

1. **The dialect seals what its vendor needs** (`upstreams.carried`): base64 of a marked JSON
   document naming the dialect. A dialect opens only its own envelopes; another dialect carries one
   along unread and never sends it to its vendor as that vendor's signature.
2. **What Claude's envelope holds**: the message's layout — its thinking blocks whole, its text,
   and the order of both around its calls (`anthropic_mapping.thinking_blocks`). Only where the
   message calls a function, which is where Anthropic needs the blocks back.
3. **Withheld where it would disclose.** A thinking block's text is reasoning; it is sealed only
   where the use case returns reasoning, or where the text is empty, as the newest models send it.
   Otherwise the message carries nothing and goes back as before.
4. **Put back only in the turn in progress** — the messages after the caller's last message that
   answers no call. Anthropic requires the blocks there and allows them out of earlier turns, and
   leaving earlier turns out the same way every time keeps what came before each block as it was
   when the block was made, which Anthropic checks the block against. A history that no longer
   matches the envelope — a call added, the text edited — goes as the caller sent it.
5. **A refusal of the carried blocks is not the end of a conversation.** Anthropic answering `400`
   about a thinking block is sent the request once more without them, and the turn goes on as it
   did before this decision.
6. **A default budget that cannot continue is left out.** Where a turn continues a call whose
   thinking did not come back, a budget the caller asked for is refused by name, as before; the
   model's declared default is not sent, as Anthropic itself degrades such a turn.
7. **No row keeps a signature.** It is model state, not anything said, and a Claude envelope would
   hold the reasoning a second time: left out of every stored payload, requests and answers alike.

## Consequences

- Positive: a tool loop keeps the model's reasoning from step to step, and a thinking budget can
  continue a call — what an agent needs from a Claude model.
- Positive: the gateway stays without state between requests; any instance answers any turn.
- Negative / trade-offs:
  - A client that drops a call's signature — the AI SDK's first major — gets no carried thinking;
    its turns go on as before, and a budget it asked for is refused on a continued call.
  - The caller's history grows by each envelope: the thinking text where the use case returns it,
    once more inside the signature.
  - Not verified against Anthropic in this round: the layout is held to Anthropic's documented
    rules by tests, and point 5 bounds what a mistake costs to one refused request.
