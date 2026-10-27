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
(`upstreams.carried`). Echoed only within the **turn in progress**: Anthropic requires them there
and allows them to be left out of earlier turns, and leaving earlier turns out the same way every
time keeps what came before each block exactly as it was when the block was made, which is what
Anthropic checks a block against.

**Withheld where it would disclose.** A block's text is the model's reasoning, and is sealed only
where the use case returns reasoning or the text is empty — a model that hides its thinking
(`display: "omitted"`) still sends the block, with its signature. Otherwise the message carries
nothing, and the next turn is sent without its blocks, as before.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.core.canonical import CanonicalMessage, TextPart, ToolCallPart
from aira_gateway.upstreams.carried import opened, seal

#: The dialect name an envelope is sealed under; nothing else opens it.
DIALECT = "anthropic"

#: The block types that are the model's thinking, both of which go back unchanged.
THINKING = "thinking"
REDACTED = "redacted_thinking"


def carried(content: Any, *, include_reasoning: bool) -> str | None:
    """The envelope for one model message's content blocks, or ``None`` where there is nothing to
    carry, nothing to carry it on, or a block the next turn could not send back whole."""
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
    return seal(DIALECT, layout) if thinks and calls else None


def with_carried(
    calls: tuple[ToolCallPart, ...], content: Any, *, include_reasoning: bool
) -> tuple[ToolCallPart, ...]:
    """``calls``, the first one carrying its message's envelope where there is one."""
    envelope = carried(content, include_reasoning=include_reasoning) if calls else None
    if envelope is None:
        return calls
    return (calls[0].model_copy(update={"signature": envelope}), *calls[1:])


def blocks_for(message: CanonicalMessage) -> list[dict[str, Any]] | None:
    """The model message's content as Anthropic produced it, or ``None`` where the caller's history
    no longer matches what was sealed — a call added or removed, the text edited — so that the
    message goes as the caller sent it rather than as something it never was."""
    calls = message.tool_calls
    if not calls or any(not isinstance(part, TextPart | ToolCallPart) for part in message.parts):
        return None
    layout = _layout(opened(calls[0].signature, DIALECT))
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
