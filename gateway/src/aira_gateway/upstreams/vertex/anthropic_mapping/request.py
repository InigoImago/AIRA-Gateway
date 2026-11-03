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
    ToolMode,
    ToolResultPart,
    paired_tool_results,
)
from aira_gateway.upstreams.base import DialectUnsupported
from aira_gateway.upstreams.vertex.anthropic_mapping.schema import (
    schema_for_anthropic,
    tool_input_schema,
)
from aira_gateway.upstreams.vertex.anthropic_mapping.thinking_blocks import (
    REDACTED,
    THINKING,
    blocks_for,
    prefixes,
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

#: The image formats an `image` block takes. Anything else sent as one is the provider's 400, so it
#: is refused here by name — a chain moves on to a model that reads it.
_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

#: Plain text goes as a document whose source is the text itself, not base64.
_TEXT_DOCUMENT = "text/plain"

#: Anthropic's floor for a `budget_tokens`, on every model that takes one. The catalogue's declared
#: minimum is the first line; this is the dialect's own, for a declaration that states none.
MIN_THINKING_BUDGET = 1024

#: How a thinking block's text comes back. The newest models default to `omitted` — the block
#: arrives empty — so a use case that asks for the reasoning has to ask for the summary by name.
_SHOWN = "summarized"

#: The cache marker. Without a `ttl` it is the five-minute lifetime, the default and the cheap one
#: (a write costs 1.25x base input, against 2x for an hour).
_EPHEMERAL = {"type": "ephemeral"}


def canonical_to_anthropic(
    request: CanonicalRequest,
    *,
    max_tokens: int,
    strict_tools: bool = False,
    carry_thinking: bool = True,
    carry_earlier: bool = True,
) -> dict[str, Any]:
    """An Anthropic Messages body (:func:`anthropic_body`), for a caller that keeps no answer."""
    return anthropic_body(
        request,
        max_tokens=max_tokens,
        strict_tools=strict_tools,
        carry_thinking=carry_thinking,
        carry_earlier=carry_earlier,
    )[0]


def anthropic_body(
    request: CanonicalRequest,
    *,
    max_tokens: int,
    strict_tools: bool = False,
    carry_thinking: bool = True,
    carry_earlier: bool = True,
) -> tuple[dict[str, Any], str]:
    """Build an Anthropic Messages body, and say what an answer to it is made on: the digest its
    thinking blocks may go back onto (`thinking_blocks.prefixes`).

    ``max_tokens`` is required by the API and optional on the canonical request, so the caller
    resolves it: the request's value, else the model's declared default (`FRD-114` FR-2).
    ``strict_tools`` is the installation's choice (`AIRA_VERTEX_PARTNER_STRICT_TOOLS`, `FRD-143`).
    ``carry_thinking`` sends the model's messages with the thinking blocks their calls carried back
    (`FRD-152`), ``carry_earlier`` those of earlier turns among them (`ADR-0051`); each off is an
    attempt of the adapter's where Anthropic refused the blocks.
    """
    messages: list[dict[str, Any]] = []
    #: Where in ``messages`` a turn of the caller's begins, for `_mark_conversation`.
    turn_starts: list[int] = []

    # Results are paired with their calls by id here, and a Gemini-speaking client sends none.
    conversation = paired_tool_results(request.messages)
    in_progress = _turn_in_progress(conversation)
    system = _system_text(conversation)
    tools = _wire_tools(request, strict_tools=strict_tools)
    made_on = prefixes(conversation, system=system, tools=tools)
    #: Whether the model turn a budget would continue goes with its thinking block (`_budget`).
    carried = False
    for index, message in enumerate(conversation):
        if message.role is Role.SYSTEM:
            continue
        role = "assistant" if message.role is Role.MODEL else "user"
        # A model message goes back as the model produced it, thinking blocks in their places:
        # always in the turn in progress, and in an earlier one onto the history it was made on.
        # Left out there, every new message of the caller's would change the prefix from the first
        # block on, and the conversation would be written to the cache again (`ADR-0051`).
        sealed = (
            blocks_for(
                message,
                in_turn=index >= in_progress,
                prefix=made_on[index] if carry_earlier else None,
            )
            if carry_thinking and message.role is Role.MODEL
            else None
        )
        if message.role is Role.MODEL:
            carried = sealed is not None
        blocks = sealed if sealed is not None else _content_blocks(message)
        if not blocks:
            # A turn with nothing left to say — a model turn that was only its thoughts, which no
            # surface carries in — is refused by Anthropic as empty content. Left out, the turns
            # either side of it are joined, which is how Anthropic reads two turns of one role.
            continue
        if message.role is Role.USER and not message.tool_results:
            turn_starts.append(len(messages))
        messages.append({"role": role, "content": blocks})

    if request.cache_prefix and messages:
        _mark_conversation(messages, turn_starts, request)

    body: dict[str, Any] = {
        "anthropic_version": ANTHROPIC_VERSION,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if system.strip():
        # Blank, it is left out: as a block empty text is refused, and only a request that opted
        # into caching is sent the block form — the one place `cache_control` exists.
        body["system"] = (
            [{"type": "text", "text": system, "cache_control": _cache_control(request)}]
            if request.cache_prefix
            else system
        )
    if request.temperature is not None:
        body["temperature"] = request.temperature
    _add_sampling(body, request)
    output_config: dict[str, Any] = {}
    if request.thinking is not None and request.thinking.mode == ThinkingMode.DISABLED:
        # **Said, not omitted**: a model that thinks by default thinks whenever the parameter is
        # absent — and bills for it — so "off" is a value on the wire, as on every other dialect.
        body["thinking"] = {"type": "disabled"}
    elif request.thinking is not None:
        thinking, effort = _thinking(request, max_tokens, carried=carried)
        if thinking is not None:
            body["thinking"] = thinking
        if effort is not None:
            output_config["effort"] = effort
    if request.tools:
        body["tools"] = tools
        if request.cache_prefix and tools:
            # On the last tool only: a breakpoint caches everything before it, and a request may
            # carry only four.
            body["tools"][-1]["cache_control"] = _cache_control(request)
        choice = _tool_choice(request)
        if choice is not None:
            body["tool_choice"] = choice
    if request.response_schema is not None or request.response_json_schema is not None:
        # A first-class parameter beside `tools`, so both travel together; `stop_reason` says
        # whether the model called a function or answered with the document. A JSON Schema
        # document goes as written (`FRD-143`); a schema this dialect cannot take is the
        # provider's 400, with its reason.
        output_config["format"] = {
            "type": "json_schema",
            "schema": (
                schema_for_anthropic(request.response_schema)
                if request.response_schema is not None
                else request.response_json_schema.to_wire()  # type: ignore[union-attr]
            ),
        }
    if output_config:
        # One object for both: the effort a level word names and the document a schema asks for.
        body["output_config"] = output_config
    return body, made_on[-1]


def _system_text(conversation: list[CanonicalMessage]) -> str:
    """One system prompt here, several allowed canonically: concatenated, never reduced."""
    return "\n\n".join(
        message.text for message in conversation if message.role is Role.SYSTEM and message.text
    )


def _wire_tools(request: CanonicalRequest, *, strict_tools: bool) -> list[dict[str, Any]]:
    return [_tool(tool, strict_tools=strict_tools) for tool in _offered(request)]


def _mark_conversation(
    messages: list[dict[str, Any]], turn_starts: list[int], request: CanonicalRequest
) -> None:
    """The two breakpoints in the conversation, of the four a request may carry.

    **The conversation so far**, on its last block. A coding assistant's context grows turn by
    turn: the files it read, the results of its tools. Each turn resends all of it and appends to
    the end, so everything up to this block is the prefix of the next request. Unmarked, every turn
    paid the whole conversation at full price, and the provider read it anew before the first word
    — minutes, on a large context. Marked, the next turn reads it at a tenth and writes only what
    it added.

    **The caller's last message before that**, as a read point of its own. The provider looks back
    only twenty positions from a breakpoint for what an earlier request wrote, and a long tool loop
    is further than that. Models up to Sonnet 4.5 and Haiku 4.5 also drop the thinking of a finished
    turn themselves, which ends the cached prefix there. Without this marker a new message writes
    the whole conversation again; with it, only the turn before it. A marker costs nothing.
    """
    _mark_last_block(messages, request)
    earlier = [start for start in turn_starts if start < len(messages) - 1]
    if earlier:
        _mark_last_block(messages[: earlier[-1] + 1], request)


def _mark_last_block(messages: list[dict[str, Any]], request: CanonicalRequest) -> None:
    """Mark the last block that can carry a marker, as the provider's own automatic placement does:
    a thinking block is refused one, and a marker on an empty result marks nothing."""
    for message in reversed(messages):
        for block in reversed(message["content"]):
            if _can_be_marked(block):
                block["cache_control"] = _cache_control(request)
                return


def _can_be_marked(block: dict[str, Any]) -> bool:
    if block["type"] in (THINKING, REDACTED):
        return False
    content = block.get("content")  # only a function's result has any
    return not isinstance(content, str) or bool(content.strip())


def _thinking(
    request: CanonicalRequest, max_tokens: int, *, carried: bool
) -> tuple[dict[str, Any] | None, str | None]:
    """The `thinking` block for a request that asks the model to think, and the effort to send.

    Two shapes, by what the catalogue offers for the model (`ADR-0021`):

        limited      {"type": "enabled", "budget_tokens": N}       the models that take a budget
        auto         {"type": "adaptive"}                          the model decides
        a level      {"type": "adaptive"} + output_config.effort   the vendor's own word

    Claude Opus 4.7 and every later model refuse `enabled` and take only `adaptive`; Claude 4.5
    and earlier refuse `adaptive`. Which a model accepts is the catalogue's declaration — a budget
    range or level words — so a new model is a catalogue entry, not a branch here.

    Every refusal here is `DialectUnsupported`, a named refusal the dispatch chain skips the
    candidate for: each is a request this wire format cannot carry, which a fallback on another
    dialect may well serve.
    """
    assert request.thinking is not None
    mode = request.thinking.mode
    if mode == ThinkingMode.LIMITED:
        return _budget(request, max_tokens, carried=carried), None
    block: dict[str, Any] = {"type": "adaptive"}
    if request.include_reasoning:
        # Without it the newest models return their thinking blocks empty, and a use case that
        # turned reasoning on would get none.
        block["display"] = _SHOWN
    return block, (None if mode == ThinkingMode.AUTO else str(mode))


def _budget(request: CanonicalRequest, max_tokens: int, *, carried: bool) -> dict[str, Any] | None:
    """`enabled` with a token budget, refused where Anthropic would refuse it — or ``None``, no
    thinking, for a default the turn cannot continue with."""
    assert request.thinking is not None
    budget = request.thinking.tokens
    if budget is None:
        # `thinking.resolve` refuses `limited` without a count first; a request built past it
        # would otherwise be sent as a body Anthropic refuses.
        raise DialectUnsupported("The 'limited' thinking mode needs a token budget.")
    if budget < MIN_THINKING_BUDGET:
        raise DialectUnsupported(
            f"A thinking budget of {budget} tokens is below the {MIN_THINKING_BUDGET} Anthropic "
            "accepts. Raise the budget, or declare that minimum for this model in the catalogue."
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
    if _continues_a_tool_call(request) and not carried:
        # With a budget, Anthropic requires the model's own `thinking` block, signature and all, in
        # front of a call it is continuing, and thinks no further in the turn without one.
        # `adaptive` has no such rule. The block travels with the call (`thinking_blocks`); a
        # history that did not bring it back — a client that drops a call's signature, or a use
        # case whose reasoning the block would have disclosed — cannot continue with a budget.
        if request.thinking_from_default:
            # The installation's default, not the caller's request: the turn goes on without
            # thinking, as Anthropic itself would carry it on, rather than refusing a request that
            # asked for nothing.
            return None
        raise DialectUnsupported(
            "This turn continues a function call on a Claude model with a thinking budget. "
            "Anthropic requires the model's own thinking block in front of the call it continues, "
            "and this request's history does not carry it back — the call's signature is missing, "
            "or this use case does not return reasoning. Send this turn with thinking disabled, "
            "or use a level word or 'auto' on a model that thinks adaptively."
        )
    block: dict[str, Any] = {"type": "enabled", "budget_tokens": budget}
    return block


def _offered(request: CanonicalRequest) -> tuple[ToolDeclaration, ...]:
    """The functions to declare: all of them, unless a required call may name only some.

    Anthropic has no list of allowed names. A call it is made to make can only name a function it
    was offered, so offering only those is the same instruction.
    """
    choice = request.tool_choice
    if choice is None or choice.mode is not ToolMode.ANY or not choice.allowed:
        return request.tools
    return tuple(tool for tool in request.tools if tool.name in choice.allowed)


def _tool_choice(request: CanonicalRequest) -> dict[str, Any] | None:
    """Google's function-calling mode as Anthropic's `tool_choice`, or nothing for `auto`."""
    choice = request.tool_choice
    if choice is None or choice.mode is ToolMode.AUTO:
        return None
    if choice.mode is ToolMode.NONE:
        return {"type": "none"}
    if choice.mode is ToolMode.VALIDATED:
        # The guarantee is a strict tool here, which the installation decides and an organisation
        # policy may forbid; answering `auto` without it would drop what was asked.
        raise DialectUnsupported(
            "'VALIDATED' asks the provider to guarantee that a call's arguments match the "
            "declaration. Anthropic gives that guarantee only for strict tools, which this request "
            "is not sent with. Use 'AUTO', or a model that can."
        )
    if request.thinking is not None and request.thinking.mode == ThinkingMode.LIMITED:
        raise DialectUnsupported(
            "'ANY' requires a function call, and Anthropic refuses a required call with a "
            "thinking budget. Use a level word or 'auto' for thinking, or 'AUTO' for the call."
        )
    if len(choice.allowed) == 1:
        return {"type": "tool", "name": choice.allowed[0]}
    return {"type": "any"}


def _turn_in_progress(messages: list[CanonicalMessage]) -> int:
    """Where the turn in progress begins: after the caller's last message that answers no call.

    A tool loop is one turn to Anthropic — the model's calls, their results and its answer — and it
    requires the model's thinking back only there.
    """
    start = 0
    for index, message in enumerate(messages):
        if message.role is Role.USER and not message.tool_results:
            start = index + 1
    return start


def _continues_a_tool_call(request: CanonicalRequest) -> bool:
    """Whether the latest model turn asked for a function call — the turn Anthropic validates."""
    model_turns = [message for message in request.messages if message.role is Role.MODEL]
    return bool(model_turns and model_turns[-1].tool_calls)


def _tool(declaration: ToolDeclaration, *, strict_tools: bool) -> dict[str, Any]:
    """One declared function.

    A JSON Schema declaration goes as written (`FRD-143`). `strict` — the provider guaranteeing the
    arguments match — is sent only where the installation asked for it and the schema can be sent
    strict without changing the tool (`tool_input_schema`). **Off by default**: the caller asked for
    no such guarantee, and on Vertex a strict tool is the partner-model feature
    `structured_outputs`, which an organisation policy
    (`constraints/vertexai.allowedPartnerModelFeatures`) forbids by default, which would refuse
    every request carrying a tool.
    """
    if declaration.parameters_json_schema is not None:
        schema, strict = declaration.parameters_json_schema.to_wire(), False
    else:
        schema, strict = tool_input_schema(declaration.parameters)
    tool: dict[str, Any] = {
        "name": declaration.name,
        "description": declaration.description,
        "input_schema": schema,
    }
    if strict and strict_tools:
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
            if part.text.strip():
                # An empty or whitespace text block is refused outright ("text content blocks
                # must contain non-whitespace text"), and says nothing.
                blocks.append({"type": "text", "text": part.text})
            continue
        if isinstance(part, ToolCallPart):
            blocks.append(
                {"type": "tool_use", "id": part.id, "name": part.name, "input": part.arguments}
            )
            continue
        if isinstance(part, ToolResultPart):
            # `tool_result` belongs to the **user** turn on this dialect and names the call by id.
            # Media goes inside it as blocks beside the text, which is where the model reads it.
            content: str | list[dict[str, Any]] = (
                [{"type": "text", "text": part.content}, *map(_attachment, part.attachments)]
                if part.attachments
                else part.content
            )
            blocks.append({"type": "tool_result", "tool_use_id": part.call_id, "content": content})
            continue
        if not isinstance(part, DataPart):  # pragma: no cover - the union is closed above
            raise DialectUnsupported(f"Unsupported part {type(part).__name__}.")
        blocks.append(_attachment(part))
    return blocks


def _attachment(part: DataPart) -> dict[str, Any]:
    """An attachment as the block its media type needs: an image, a PDF, or plain text."""
    if part.media_type == _TEXT_DOCUMENT:
        try:
            text = part.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DialectUnsupported(
                "A text/plain attachment that is not UTF-8 cannot be sent to a Claude model."
            ) from exc
        return {
            "type": "document",
            "source": {"type": "text", "media_type": _TEXT_DOCUMENT, "data": text},
        }
    if part.media_type in _DOCUMENT_TYPES:
        kind = "document"
    elif part.media_type in _IMAGE_TYPES:
        kind = "image"
    else:
        raise DialectUnsupported(
            f"A Claude model reads images ({', '.join(sorted(_IMAGE_TYPES))}), PDF and plain "
            f"text; an attachment of type {part.media_type} has no block to go in."
        )
    return {
        "type": kind,
        "source": {
            "type": "base64",
            "media_type": part.media_type,
            "data": base64.b64encode(part.data).decode("ascii"),
        },
    }


def _cache_control(request: CanonicalRequest) -> dict[str, str]:
    """The cache marker, with a `ttl` only for the long lifetime (`FRD-133`): only the expensive
    option ever appears on the wire."""
    return {**_EPHEMERAL, "ttl": "1h"} if request.cache_ttl == "1h" else dict(_EPHEMERAL)
