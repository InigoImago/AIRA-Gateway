"""Claude's thinking blocks, carried through the caller to the turn that continues them (`FRD-152`).

When a model's function calls are answered, Anthropic needs that model message back as it was
produced — every `thinking` and `redacted_thinking` block complete and unmodified, in its place:
the model goes on with the same response, and its reasoning so far is part of it. Without them a
model with a thinking budget stops thinking for the rest of the turn, and an adaptive one reasons
again from nothing at every step of a tool loop — paid for twice, and nothing on the wire while it
does. The caller's history cannot hold them: a Gemini-speaking client has no field for a thinking
block, and the gateway keeps no state between requests.

So each model message that calls a function carries its own layout — its thinking blocks whole, its
text, and the order of both around the calls — sealed into the signature of its first call
(`upstreams.carried`). The **turn in progress** always gets them back: Anthropic requires them
there.

**An earlier turn gets them back too, wherever nothing before them changed** (`ADR-0051`). Models
from Claude Opus 4.5 and Sonnet 4.6 on keep earlier turns' thinking in context and in the cache, so
a turn that goes back without its blocks is a different prefix from the first block on: each new
message of the caller's would write the conversation again at the write price, and have it read
anew before the first word. But a block is valid only while everything before it is unchanged, and
a client may change that — OpenCode clears old tool results. So an envelope states what its blocks
were made on,
as a digest (:func:`prefixes`), and a block goes back only onto that same history. A block made on a
history that has since changed stays out, which Anthropic allows and which refuses nothing.

**Withheld where it would disclose.** A block's text is the model's reasoning, and is sealed only
where the use case returns reasoning or the text is empty — a model that hides its thinking
(`display: "omitted"`) still sends the block, with its signature. Otherwise the message carries
nothing, and the next turn is sent without its blocks, as before.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from aira_gateway.core.canonical import (
    CanonicalMessage,
    DataPart,
    Role,
    TextPart,
    ToolCallPart,
    ToolResultPart,
)
from aira_gateway.upstreams.carried import opened, seal

#: The dialect name an envelope is sealed under; nothing else opens it.
DIALECT = "anthropic"

#: The block types that are the model's thinking, both of which go back unchanged.
THINKING = "thinking"
REDACTED = "redacted_thinking"

#: How much of a digest an envelope keeps: it tells one history from another, and guards nothing a
#: caller could gain by forging it — a forged one costs them a refused request.
_DIGEST_LENGTH = 32


def prefixes(
    conversation: Sequence[CanonicalMessage], *, system: str, tools: Sequence[dict[str, Any]]
) -> list[str]:
    """A digest of what comes before each message, and, last, of what an answer to it is made on.

    What Anthropic checks a block against: the system prompt, the set of tools and every message
    before it — and neither thinking blocks, nor cache markers, nor a text's outer whitespace, nor
    the order of a JSON object's keys, nor the order of the tools. Worked out from what the caller
    sent, so the same history gives the same digest whichever blocks go with it.
    """
    seen = hashlib.sha256(
        _encoded({"system": system.strip(), "tools": sorted(tools, key=lambda t: t["name"])})
    )
    digests: list[str] = []
    for message in conversation:
        digests.append(seen.hexdigest()[:_DIGEST_LENGTH])
        if message.role is not Role.SYSTEM:
            seen.update(_encoded([message.role.value, _said(message)]))
    digests.append(seen.hexdigest()[:_DIGEST_LENGTH])
    return digests


def _said(message: CanonicalMessage) -> list[Any]:
    """One message as :func:`prefixes` compares it, an attachment by its hash."""
    said: list[Any] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            if part.text.strip():
                said.append(["text", part.text.strip()])
        elif isinstance(part, ToolCallPart):
            said.append(["call", part.id, part.name, part.arguments])
        elif isinstance(part, ToolResultPart):
            media = [_hashed(item) for item in part.attachments]
            said.append(["result", part.call_id, part.content, media])
        elif isinstance(part, DataPart):  # pragma: no branch - the union is closed above
            said.append(_hashed(part))
    return said


def _hashed(part: DataPart) -> list[str]:
    return ["data", part.media_type, hashlib.sha256(part.data).hexdigest()]


def _encoded(value: Any) -> bytes:
    """``value`` in one spelling: JSON key order is no change to Anthropic, so none to this.
    ASCII, so a lone surrogate from a client's JSON has an encoding too."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("ascii")


def carried(content: Any, *, include_reasoning: bool, prefix: str | None = None) -> str | None:
    """The envelope for one model message's content blocks, or ``None`` where there is nothing to
    carry, nothing to carry it on, or a block the next turn could not send back whole.

    ``prefix`` is what the message was made on (:func:`prefixes`). Without one the blocks go back
    only in the turn in progress.
    """
    if not isinstance(content, list):
        return None
    layout: list[dict[str, str]] = []
    for block in content:
        if not isinstance(block, dict):
            return None
        kind = block.get("type")
        if kind == THINKING:
            text, signature = str(block.get("thinking") or ""), str(block.get("signature") or "")
            if not signature or (text and not include_reasoning):
                return None
            layout.append({"type": THINKING, "thinking": text, "signature": signature})
        elif kind == REDACTED:
            data = str(block.get("data") or "")
            if not data:
                return None
            layout.append({"type": REDACTED, "data": data})
        elif kind == "text":
            layout.append({"type": "text", "text": str(block.get("text") or "")})
        elif kind == "tool_use":
            layout.append({"type": "tool_use", "name": str(block.get("name") or "")})
        else:
            # A block this gateway does not send back; half a message would be refused.
            return None
    thinks = any(entry["type"] in (THINKING, REDACTED) for entry in layout)
    calls = any(entry["type"] == "tool_use" for entry in layout)
    if not (thinks and calls):
        return None
    return seal(DIALECT, layout if prefix is None else {"prefix": prefix, "layout": layout})


def with_carried(
    calls: tuple[ToolCallPart, ...],
    content: Any,
    *,
    include_reasoning: bool,
    prefix: str | None = None,
) -> tuple[ToolCallPart, ...]:
    """``calls``, the first one carrying its message's envelope where there is one."""
    envelope = (
        carried(content, include_reasoning=include_reasoning, prefix=prefix) if calls else None
    )
    if envelope is None:
        return calls
    return (calls[0].model_copy(update={"signature": envelope}), *calls[1:])


def blocks_for(
    message: CanonicalMessage, *, in_turn: bool = True, prefix: str | None = None
) -> list[dict[str, Any]] | None:
    """The model message's content as Anthropic produced it, or ``None`` where the caller's history
    no longer matches what was sealed — a call added or removed, the text edited — so that the
    message goes as the caller sent it rather than as something it never was.

    ``in_turn`` says whether the message belongs to the turn in progress, where its blocks go back
    whatever else happened: Anthropic requires them, and judges them itself. Any other message's go
    back only onto the history they were made on, ``prefix`` — and never without one: an envelope
    that does not say cannot be told from one whose history changed.
    """
    calls = message.tool_calls
    if not calls or any(not isinstance(part, TextPart | ToolCallPart) for part in message.parts):
        return None
    state = opened(calls[0].signature, DIALECT)
    made_on = state.get("prefix") if isinstance(state, dict) else None
    if not in_turn and (made_on is None or made_on != prefix):
        return None
    layout = _layout(state.get("layout") if isinstance(state, dict) else state)
    if layout is None:
        return None
    if [entry["name"] for entry in layout if entry["type"] == "tool_use"] != [
        call.name for call in calls
    ]:
        return None
    sealed_text = "".join(entry["text"] for entry in layout if entry["type"] == "text")
    if sealed_text.strip() != message.text.strip():
        return None
    remaining = iter(calls)
    blocks: list[dict[str, Any]] = []
    for entry in layout:
        if entry["type"] == "tool_use":
            # The caller's call — its id is the one its result names — in the sealed place.
            call = next(remaining)
            blocks.append(
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            )
        elif entry["type"] == "text":
            # As `_content_blocks` sends text: a whitespace-only block is refused, and says nothing.
            if entry["text"].strip():
                blocks.append({"type": "text", "text": entry["text"]})
        else:
            blocks.append(dict(entry))
    return blocks


def _layout(state: Any) -> list[dict[str, str]] | None:
    """A sealed layout, checked field by field: an envelope is the caller's to send back, and what
    comes back is read as untrusted."""
    if not isinstance(state, list) or not state:
        return None
    wanted = {
        THINKING: ("thinking", "signature"),
        REDACTED: ("data",),
        "text": ("text",),
        "tool_use": ("name",),
    }
    layout: list[dict[str, str]] = []
    for entry in state:
        if not isinstance(entry, dict) or entry.get("type") not in wanted:
            return None
        fields = wanted[entry["type"]]
        if any(not isinstance(entry.get(field), str) for field in fields):
            return None
        if entry["type"] in (THINKING, REDACTED) and not entry[fields[-1]]:
            # A thinking block without its signature, or redacted thinking without its data, is
            # refused by Anthropic for the whole request.
            return None
        layout.append({"type": entry["type"], **{field: entry[field] for field in fields}})
    return layout
