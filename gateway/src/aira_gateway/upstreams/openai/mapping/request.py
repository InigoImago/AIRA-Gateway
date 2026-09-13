"""Canonical → the OpenAI wire format: chat and embedding request bodies."""

from __future__ import annotations

import base64
import json
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalRequest,
    DataPart,
    Role,
    TextPart,
    Thinking,
)
from aira_gateway.core.schema import ResponseSchema, to_json_schema
from aira_gateway.upstreams.base import DialectUnsupported

_ROLE = {Role.SYSTEM: "system", Role.USER: "user", Role.MODEL: "assistant"}

#: The name the schema is registered under. OpenAI requires one; it is never shown to the caller.
SCHEMA_NAME = "aira_response"

#: What this dialect can express (`FRD-124`). `top_k` is absent from the chat API and therefore
#: **refused** on a candidate served this way, not dropped: a caller who pinned `top_k=1` and got
#: the default would receive a different answer with a 200 on it.
SAMPLING = frozenset({"top_p", "seed", "presence_penalty", "frequency_penalty", "stop_sequences"})


def _content(message: CanonicalMessage) -> str | list[dict[str, Any]]:
    """Canonical parts → OpenAI content.

    A text-only message stays a plain string: the common case, and several implementations of this
    API are fussier about the array form.
    """
    if not message.attachments:
        return message.text

    parts: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            parts.append({"type": "text", "text": part.text})
            continue
        if not isinstance(part, DataPart):
            # Tool parts are carried by `_wire_messages` and never reach here.
            continue
        if not part.media_type.startswith("image/"):
            # This dialect reads images and **not** documents (`ADR-0012`). The chain should have
            # skipped this candidate (`MediaTypesSupported`); sending the prompt without the
            # document would produce a confident wrong answer with a 200 on it.
            raise DialectUnsupported(
                f"This dialect carries images, not '{part.media_type}'. A model declaring it "
                "cannot serve the request through an OpenAI-compatible endpoint."
            )
        encoded = base64.b64encode(part.data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{part.media_type};base64,{encoded}"},
            }
        )
    return parts


def _wire_messages(request: CanonicalRequest) -> list[dict[str, Any]]:
    """Canonical messages → this dialect's message list, which is **not** one-to-one.

    A tool call is a field on an assistant message, and each tool *result* is a `role: "tool"`
    message of its own — so one canonical turn holding two results becomes two wire messages, in
    order.
    """
    out: list[dict[str, Any]] = []
    for message in request.messages:
        calls = message.tool_calls
        results = message.tool_results
        if not calls and not results:
            out.append({"role": _ROLE[message.role], "content": _content(message)})
            continue
        if calls:
            entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    # Arguments travel as a **JSON string** in this dialect, not as an object.
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in calls
            ]
            out.append(entry)
        for result in results:
            out.append({"role": "tool", "tool_call_id": result.call_id, "content": result.content})
    return out


def _wire_tools(request: CanonicalRequest) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": (
                    to_json_schema(tool.parameters)
                    if tool.parameters is not None
                    # Several implementations reject a function declared without a schema.
                    else {"type": "object", "properties": {}}
                ),
            },
        }
        for tool in request.tools
    ]


def canonical_to_openai(request: CanonicalRequest, *, stream: bool = False) -> dict[str, Any]:
    """Build a `/v1/chat/completions` body. The system prompt is simply the first message."""
    body: dict[str, Any] = {
        "model": request.model,
        "messages": _wire_messages(request),
    }
    if request.tools:
        body["tools"] = _wire_tools(request)
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        body["max_tokens"] = request.max_output_tokens
    if request.thinking is not None:
        body["reasoning_effort"] = _reasoning_effort(request.thinking)
    if request.response_schema is not None:
        body["response_format"] = _response_format(request.response_schema)
    _add_sampling(body, request)
    if stream:
        body["stream"] = True
        # A stream reports no usage unless asked, and one without usage is released rather than
        # settled (`FRD-405`) — forgetting this would make every streamed request free.
        body["stream_options"] = {"include_usage": True}
    return body


def _add_sampling(body: dict[str, Any], request: CanonicalRequest) -> None:
    if request.top_p is not None:
        body["top_p"] = request.top_p
    if request.seed is not None:
        body["seed"] = request.seed
    if request.presence_penalty is not None:
        body["presence_penalty"] = request.presence_penalty
    if request.frequency_penalty is not None:
        body["frequency_penalty"] = request.frequency_penalty
    if request.stop_sequences:
        body["stop"] = list(request.stop_sequences)
    if request.top_k is not None:
        # A backstop: the dispatch chain skips this candidate before it gets here.
        raise DialectUnsupported(
            "This dialect has no 'top_k'. It is refused rather than dropped: a caller who set it "
            "and silently received the model's default would get a different answer with a 200 on "
            "it."
        )


def _reasoning_effort(setting: Thinking) -> str:
    """The caller's level word, and an explicit `"none"` for off; `limited` and `auto` refused.

    A level passes through untranslated: the catalog declares the words this model accepts and
    `thinking.py` has already refused any other, so a table here could only guess.

    `disabled` is sent as `"none"`, **not** as an absent field: a reasoning model sent no
    `reasoning_effort` thinks anyway, by its own default, and bills for it.

    `limited` names a number this dialect cannot spend and `auto` names none, so an approximation
    of either would spend the caller's money at a rate nobody chose.
    """
    if setting.mode == ThinkingMode.LIMITED:
        raise DialectUnsupported(
            "This dialect takes an effort level, not a token budget, so a 'limited' thinking "
            "budget cannot be honoured exactly. It is refused rather than rounded: rounding "
            "would spend a different amount than was asked for, and nothing about the answer "
            "would show it."
        )
    if setting.mode == ThinkingMode.AUTO:
        raise DialectUnsupported(
            "This dialect has no way to say 'the model decides': `reasoning_effort` is always a "
            "level. It is refused rather than guessed at — picking one on the caller's behalf "
            "would spend their money at a rate nobody chose, and the answer would not say so."
        )
    if setting.mode == ThinkingMode.DISABLED:
        return "none"
    return setting.mode


def _response_format(schema: ResponseSchema) -> dict[str, Any]:
    """A named ``json_schema`` with ``strict`` — this dialect's mechanism behind the structured
    output capability (`ADR-0011` rule 3). The JSON Schema translation lives in the canonical core,
    so no dialect imports it from another."""
    return {
        "type": "json_schema",
        "json_schema": {"name": SCHEMA_NAME, "strict": True, "schema": to_json_schema(schema)},
    }


def canonical_to_openai_embedding(request: CanonicalEmbeddingRequest) -> dict[str, Any]:
    """`/v1/embeddings` takes the whole batch in one ``input`` array.

    This format has no task type; `FRD-113` refuses an undeclared one before dispatch.
    """
    body: dict[str, Any] = {"model": request.model, "input": list(request.texts)}
    if request.dimensions is not None:
        body["dimensions"] = request.dimensions
    return body
