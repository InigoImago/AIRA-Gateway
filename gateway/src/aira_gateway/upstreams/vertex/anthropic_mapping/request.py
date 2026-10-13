"""Canonical → an Anthropic Messages body."""

from __future__ import annotations

import base64
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    DataPart,
    Role,
    TextPart,
    ToolCallPart,
    ToolDeclaration,
    ToolResultPart,
    paired_tool_results,
)
from aira_gateway.upstreams.base import DialectUnsupported
from aira_gateway.upstreams.vertex.anthropic_mapping.schema import (
    schema_for_anthropic,
    tool_input_schema,
)

#: Vertex requires this in the body rather than as a header.
ANTHROPIC_VERSION = "vertex-2023-10-16"

#: What the Messages API can express (`FRD-124`). It has no `seed` and no penalties, so a request
#: that pins a seed is refused on a Claude candidate rather than answered non-reproducibly.
SAMPLING = frozenset({"top_p", "top_k", "stop_sequences"})

_UNSUPPORTED_SAMPLING = {
    "seed": "no seed parameter, so a request cannot be made reproducible here",
    "presence_penalty": "no presence penalty",
    "frequency_penalty": "no frequency penalty",
}

#: Anthropic distinguishes an image block from a document block, and refuses a PDF in an image.
_DOCUMENT_TYPES = frozenset({"application/pdf"})

#: The cache marker. Without a `ttl` it is the five-minute lifetime, the default and the cheap one
#: (a write costs 1.25x base input, against 2x for an hour).
_EPHEMERAL = {"type": "ephemeral"}


def canonical_to_anthropic(request: CanonicalRequest, *, max_tokens: int) -> dict[str, Any]:
    """Build an Anthropic Messages body.

    ``max_tokens`` is required by the API and optional on the canonical request, so the caller
    resolves it: the request's value, else the model's declared default (`FRD-114` FR-2).
    """
    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []

    # Results are paired with their calls by id here, and a Gemini-speaking client sends none.
    for message in paired_tool_results(request.messages):
        if message.role is Role.SYSTEM:
            # One system prompt here, several allowed canonically: concatenated, never reduced.
            system_parts.append(message.text)
            continue
        role = "assistant" if message.role is Role.MODEL else "user"
        messages.append({"role": role, "content": _content_blocks(message)})

    body: dict[str, Any] = {
        "anthropic_version": ANTHROPIC_VERSION,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if system_parts:
        joined = "\n\n".join(system_parts)
        # The block form only when caching is asked for: `cache_control` exists only there.
        body["system"] = (
            [{"type": "text", "text": joined, "cache_control": _cache_control(request)}]
            if request.cache_prefix
            else joined
        )
    if request.temperature is not None:
        body["temperature"] = request.temperature
    _add_sampling(body, request)
    if request.thinking is not None and request.thinking.mode == ThinkingMode.DISABLED:
        # **Said, not omitted**: a model that thinks by default thinks whenever the parameter is
        # absent — and bills for it — so "off" is a value on the wire, as on every other dialect.
        body["thinking"] = {"type": "disabled"}
    elif request.thinking is not None:
        body["thinking"] = _thinking(request, max_tokens)
    if request.tools:
        body["tools"] = [_tool(tool) for tool in request.tools]
        if request.cache_prefix:
            # On the last tool only: a breakpoint caches everything before it, and a request may
            # carry only four.
            body["tools"][-1]["cache_control"] = _cache_control(request)
        # No `tool_choice`: the model decides, and the surface accepts nothing else.
    if request.response_schema is not None:
        # A first-class parameter beside `tools`, so both travel together; `stop_reason` says
        # whether the model called a function or answered with the document.
        body["output_config"] = {
            "format": {
                "type": "json_schema",
                "schema": schema_for_anthropic(request.response_schema),
            }
        }
    return body


def _thinking(request: CanonicalRequest, max_tokens: int) -> dict[str, Any]:
    """The `thinking` block for a request that asks the model to think.

    Every refusal here is `DialectUnsupported`, a named refusal the dispatch chain skips the
    candidate for: each is a request this wire format cannot carry, which a fallback on another
    dialect may well serve.
    """
    assert request.thinking is not None
    budget = request.thinking.tokens
    if budget is None:
        # **Refused, not omitted**: this dialect says "think" only by naming a budget, and a
        # body without one is the `disabled` body — a 200 with no thinking and no word said.
        # Reached by a level word or `auto`; the fix is not offering that mode for this model,
        # never an invented budget (`ADR-0021`).
        raise DialectUnsupported(
            f"This model was asked to think in mode '{request.thinking.mode}', and the "
            "Anthropic dialect requests thinking only by naming a token budget: it has no "
            "field for a level and no way to say 'you decide'. Use the 'limited' mode with a "
            "token count, or do not offer this level for this model."
        )
    # Thinking tokens are drawn from `max_tokens`, so a budget at or above it can never answer.
    # The catalogue refuses that where it is authored; this is the backstop for a request whose own
    # cap — or the adapter's default, where neither caller nor catalogue named one — is lower.
    if budget >= max_tokens:
        raise DialectUnsupported(
            f"A thinking budget of {budget} tokens does not fit inside this request's "
            f"{max_tokens}-token output allowance: Anthropic draws the thinking from that "
            "allowance, so the budget must be below it. Raise maxOutputTokens or lower the budget."
        )
    if _continues_a_tool_call(request):
        # Anthropic requires the model's own `thinking` block, signature and all, in front of a
        # call it is continuing, and refuses the turn without one. This gateway never hands those
        # blocks to a caller — their text is the model's reasoning, withheld unless a use case
        # enables it — so it has none to send back.
        raise DialectUnsupported(
            "This turn continues a function call on a Claude model with thinking enabled. "
            "Anthropic requires the model's own thinking block in front of the call it continues, "
            "and this gateway does not carry thinking blocks to the caller, so it cannot send one "
            "back. Send this turn with thinking disabled, or use a model that does not think by "
            "default for tool loops."
        )
    return {"type": "enabled", "budget_tokens": budget}


def _continues_a_tool_call(request: CanonicalRequest) -> bool:
    """Whether the latest model turn asked for a function call — the turn Anthropic validates."""
    model_turns = [message for message in request.messages if message.role is Role.MODEL]
    return bool(model_turns and model_turns[-1].tool_calls)


def _tool(declaration: ToolDeclaration) -> dict[str, Any]:
    """One declared function. `strict` — the provider guaranteeing the arguments match — only
    where the schema can be sent strict without changing the tool (`tool_input_schema`)."""
    schema, strict = tool_input_schema(declaration.parameters)
    tool: dict[str, Any] = {
        "name": declaration.name,
        "description": declaration.description,
        "input_schema": schema,
    }
    if strict:
        tool["strict"] = True
    return tool


def _add_sampling(body: dict[str, Any], request: CanonicalRequest) -> None:
    if request.top_p is not None:
        body["top_p"] = request.top_p
    if request.top_k is not None:
        body["top_k"] = request.top_k
    if request.stop_sequences:
        body["stop_sequences"] = list(request.stop_sequences)
    for name, why in _UNSUPPORTED_SAMPLING.items():
        if getattr(request, name) is not None:
            # A backstop behind the dispatch chain, which skips this candidate first.
            # `DialectUnsupported` is a refusal (`serving.REFUSALS`): a named 400, not a 500.
            raise DialectUnsupported(
                f"The Anthropic Messages API has {why}; '{name}' cannot be honoured."
            )


def _content_blocks(message: CanonicalMessage) -> list[dict[str, Any]]:
    """Canonical parts → Anthropic content blocks, in order."""
    blocks: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            blocks.append({"type": "text", "text": part.text})
            continue
        if isinstance(part, ToolCallPart):
            blocks.append(
                {"type": "tool_use", "id": part.id, "name": part.name, "input": part.arguments}
            )
            continue
        if isinstance(part, ToolResultPart):
            # `tool_result` belongs to the **user** turn on this dialect and names the call by id.
            blocks.append(
                {"type": "tool_result", "tool_use_id": part.call_id, "content": part.content}
            )
            continue
        if not isinstance(part, DataPart):  # pragma: no cover - the union is closed above
            raise DialectUnsupported(f"Unsupported part {type(part).__name__}.")
        kind = "document" if part.media_type in _DOCUMENT_TYPES else "image"
        blocks.append(
            {
                "type": kind,
                "source": {
                    "type": "base64",
                    "media_type": part.media_type,
                    "data": base64.b64encode(part.data).decode("ascii"),
                },
            }
        )
    return blocks


def _cache_control(request: CanonicalRequest) -> dict[str, str]:
    """The cache marker, with a `ttl` only for the long lifetime (`FRD-133`): only the expensive
    option ever appears on the wire."""
    return {**_EPHEMERAL, "ttl": "1h"} if request.cache_ttl == "1h" else dict(_EPHEMERAL)
