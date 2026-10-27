# FRD-152 — Claude's thinking travels with the turn that continues it

> Phase: 8 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner asked whether the `499`s on Claude with large contexts (`ADR-0046`) had to do
> with the model's thinking. They did, in two ways, and the second was a defect this feature
> removes. Decision: `ADR-0047`. Related: `FRD-135` (reasoning returned), `FRD-151` (thinking in
> OpenCode), `FRD-119` (Anthropic on Vertex), `FRD-145` (signatures carried back).

## 1. Problem

**What the `499`s had to do with thinking.** A stream was silent until the model's first visible
word (`ADR-0046`). The newest Claude models think on every request and send their thinking blocks
empty unless asked for a summary; Opus 5.5, Sonnet 5.5 and Fable 5.1 also write their notes between
function calls into such blocks. So thinking was most of what made a stream silent, and the
keepalive is what ended the `499`s. `FRD-151` makes the thinking of a model with a declared default
visible as well, which fills part of the silence with reasoning.

**And why the silence was longer than it had to be.** When a model's function calls are answered,
Anthropic needs that model message back as it was produced — every thinking block complete,
unmodified and in its place — because the model continues the same response. This gateway reduced
a block to reasoning text, dropped its signature, and sent every later turn without it:

- **adaptively**, the model reasoned again from nothing at every step of a tool loop — billed twice,
  slower, and silent on the wire while it did;
- **under a budget** (Claude 4.6 and earlier), Anthropic stops the thinking for the rest of the turn
  without the block, and this gateway refused the continuation by name — so a thinking budget could
  not be used by an agent at all, and `FRD-151`'s advice to declare one as a default would have
  failed the second step of every OpenCode loop.

## 2. Goals & Non-Goals

**Goals**
- Within a tool loop, a Claude model gets its own thinking blocks back, unmodified and in place.
- No state on the gateway between requests.
- Nothing reaches the caller in the envelope that the use case does not return as reasoning.
- A mistake costs one refused request, not a conversation.

**Non-Goals**
- Thinking blocks of earlier turns, which Anthropic allows to be left out.
- A client that drops a function call's signature; its turns go on as before.
- Other dialects. Google needs its own signatures back, which `FRD-145` carries; the OpenAI dialect
  has no such rule.

## 3. User Stories
- As a **developer in OpenCode on Claude**, I want each step of a tool loop to continue the model's
  reasoning instead of starting it again, so it is faster, cheaper and better.
- As a **use-case administrator**, I want a thinking budget on a Claude 4 model to work in an agent.
- As **IT Security**, I want reasoning that a use case does not return to stay out of every field a
  caller receives, and out of the audit rows.

## 4. Functional Requirements

- **FR-1 Sealed into the first call.** Where a model message calls a function and holds thinking, the
  signature of its first call carries an envelope: the message's thinking blocks whole, its text and
  the order of both around the calls, base64 of a marked document only this dialect opens
  (`upstreams.carried`). Streamed and buffered answers alike.
- **FR-2 Only what may be disclosed.** A block's text is sealed only where the use case returns
  reasoning, or where it is empty. A message holding thinking text a use case does not return, a
  block without its signature, or a block this gateway does not send back carries nothing.
- **FR-3 Put back in the turn in progress.** The model messages after the caller's last message that
  answers no call go to Anthropic as the envelope describes them — thinking blocks, text, then the
  caller's own calls with the ids their results name. Earlier turns go without their thinking.
- **FR-4 A history that changed goes as sent.** An envelope whose calls or text no longer match the
  caller's message is ignored for that message.
- **FR-5 A refusal is sent once more without.** Anthropic answering `400` about a thinking block is
  sent the same request without the carried blocks, buffered and streamed.
- **FR-6 A budget continues a call its thinking came back with.** Without it, a budget the caller
  asked for is refused by name, as before; the model's declared default is not sent.
- **FR-7 Google is never sent the envelope** as its signature; another dialect's state is carried
  along unread.
- **FR-8 No stored payload keeps a signature**, the caller's history included.
- **FR-9 The mock signs** a call made while it is thinking, so every real client is tested on
  handing a call's signature back.

## 5. Design & Architecture

### 5.1 Why the signature of a function call

Measured with the mock signing its calls, a tool loop through each client: OpenCode 1.19.3 and
2.0.32, `google-genai`'s chat, and the AI SDK's Google provider in majors 2, 3 and 4 handed the
signature back on the call, character for character; the first major dropped it. The other
candidates fail on the clients: a thought part is merged with its neighbours and dropped when it
has no text, and every block of the newest models arrives without text.

### 5.2 Why only the turn in progress

Anthropic's rules, as documented: within a tool-use turn the thinking blocks are required; across
turns they are recommended and may be left out; and from Claude Fable 5.1 on, a block is valid only
while everything sent before it is unchanged. Sending the turn in progress with its blocks and every
earlier turn without them — the same way on every request — keeps what precedes each block exactly
as it was when the block was made. Echoing every turn would make each block depend on the caller
keeping every earlier turn untouched, which OpenCode does not: it clears old tool results.

### 5.3 Why a second attempt

The blocks go back exactly as they came; what this gateway cannot see is a client that changed
something before them. Anthropic answers that with a `400` naming the thinking block and does not
bill it. Sent again without the blocks, the turn goes on as it did before this feature — so a
mistake in carrying them is never worse than not carrying them.

## 6. Data Model

None. `CanonicalRequest.thinking_from_default` marks a thinking setting that is the model's
declared default, set where the request is fitted to each model.

## 7. API / Interface Contract

The Gemini surface's `thoughtSignature` on a `functionCall` part may now carry this gateway's
envelope where a Claude model answered. Opaque, as Google's own: a client hands it back on the call
it came with. The KIRA surface carries it in the call's opaque metadata, as before.

## 8. Security & Privacy

- The envelope holds reasoning only where the use case returns reasoning to the caller anyway; the
  reasoning reaches nobody it did not reach before.
- No stored payload keeps a signature (FR-8): Google's were kept in the stored request until now,
  opaque to every reader, and a Claude envelope would have held the reasoning a second time. The
  register is unchanged — what is stored is less, not different.
- An envelope coming back is read as untrusted: checked field by field, used only where it matches
  the caller's message, and what it holds is only ever sent to the model that produced it.

## 9. Observability

`carried_thinking_refused` is logged with the model when Anthropic refuses the carried blocks and
the request is sent again without them.

## 10. Testing & Acceptance Criteria

- `test_claudes_thinking_travels_with_the_turn.py`: sealed and opened only by its dialect; Google's
  SDK round trip; carried on the first call with text and order; an empty block whatever the use
  case says, a text block only where it returns reasoning; nothing that could not go back whole;
  the stream's envelope; put back in place in the turn in progress and not in earlier turns; a
  changed history as sent; a budget continues; a default without thinking goes on; a caller's
  budget is refused; Google is not sent it; a refusal is sent again, buffered and streamed, and
  any other refusal stands.
- `test_a_model_that_thinks_unasked.py`: a default is marked as the installation's.
- `test_log_writer.py`: no stored payload keeps a signature.
- `test_real_gemini_clients.py`: each AI SDK major, `google-genai`'s chat and both OpenCode
  generations hand a call's signature back — all but the AI SDK's first major.
- Mutations `CT-1`–`CT-19`.

## 11. Dependencies & Risks

- **Not verified against Anthropic.** No Claude model is reachable from this environment. The
  layout follows Anthropic's documented rules, and FR-5 bounds a mistake to one refused request.
- A long Claude turn's envelopes grow the caller's history by its thinking text, where the use case
  returns reasoning, and by each block's signature, which holds the full thinking encrypted.
- **Claude 4.6 and earlier, with reasoning off, carry nothing.** Those models summarise their
  thinking unasked, and text a use case does not return cannot travel. Asking them to leave it out
  (`display: "omitted"`, valid in both modes by Anthropic's reference) would let the blocks travel
  with only their signatures; it is not sent until it has been checked against those models, since
  a refusal of it would fail every such request rather than only the carried blocks.

## 12. Rollout

Nothing to configure. A conversation in progress carries its thinking from its next answer on.
