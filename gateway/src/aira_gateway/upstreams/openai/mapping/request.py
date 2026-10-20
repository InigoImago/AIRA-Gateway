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
    ToolCallPart,
    ToolMode,
    ToolResultPart,
    paired_tool_results,
)
from aira_gateway.core.schema import ResponseSchema, to_json_schema
from aira_gateway.upstreams.base import DialectUnsupported

_ROLE = {Role.SYSTEM: "system", Role.USER: "user", Role.MODEL: "assistant"}

#: The name the schema is registered under. OpenAI requires one; it is never shown to the caller.
SCHEMA_NAME = "aira_response"

#: The body field for the output cap where a platform does not name its own (`Routes`): the one
#: every OpenAI-compatible server accepts.
DEFAULT_OUTPUT_CAP_FIELD = "max_tokens"

#: Constraints this dialect's **strict** mode cannot express (OpenAI's structured outputs refuse
#: them by name). On a platform that validates strict schemas, a schema using one skips the
#: candidate by name rather than being sent with the constraint dropped (`ADR-0012` §3).
UNSUPPORTED_STRICT_FIELDS = frozenset(
    {"min_length", "max_length", "min_properties", "max_properties"}
)

#: What this dialect can express (`FRD-124`). `top_k` is absent from the chat API and therefore
#: **refused** on a candidate served this way, not dropped: a caller who pinned `top_k=1` and got
#: the default would receive a different answer with a 200 on it.
SAMPLING = frozenset({"top_p", "seed", "presence_penalty", "frequency_penalty", "stop_sequences"})


def _content(message: CanonicalMessage) -> str | list[dict[str, Any]]:
    """Canonical parts → OpenAI content.

    A text-only message stays a plain string: the common case, and several implementations of this
    API are fussier about the array form. Tool parts are not content here: `_wire_messages` carries
    them as messages of their own.
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
    # Results are paired with their calls by id here, and a Gemini-speaking client sends none.
    for message in paired_tool_results(request.messages):
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
            if result.attachments:
                # A tool message is text in this dialect; media sent there is refused, and dropped
                # it would leave the model describing an image it never saw.
                raise DialectUnsupported(
                    f"The result of '{result.name}' carries "
                    f"{', '.join(sorted({media.media_type for media in result.attachments}))}, "
                    "and this dialect's tool messages take text only."
                )
            out.append({"role": "tool", "tool_call_id": result.call_id, "content": result.content})
        said = [
            part for part in message.parts if not isinstance(part, ToolCallPart | ToolResultPart)
        ]
        if results and said:
            # What the turn says beside its results — "now also translate it" — follows them as a
            # message of its own: the results must come straight after the calls they answer, and
            # dropping the rest would answer a different question with a 200.
            out.append(
                {
                    "role": _ROLE[message.role],
                    "content": _content(message.model_copy(update={"parts": said})),
                }
            )
    return out


def _wire_tools(request: CanonicalRequest) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": (
                    # JSON Schema is this dialect's own vocabulary (`FRD-143`).
                    tool.parameters_json_schema.to_wire()
                    if tool.parameters_json_schema is not None
                    else to_json_schema(tool.parameters)
                    if tool.parameters is not None
                    # Several implementations reject a function declared without a schema.
                    else {"type": "object", "properties": {}}
                ),
            },
        }
        for tool in request.tools
        # A required call may name only the allowed functions; this dialect has no list of them
        # beside `tools`, so offering only those is the same instruction.
        if _offered(tool.name, request)
    ]


def _offered(name: str, request: CanonicalRequest) -> bool:
    choice = request.tool_choice
    if choice is None or choice.mode is not ToolMode.ANY or not choice.allowed:
        return True
    return name in choice.allowed


def _tool_choice(request: CanonicalRequest) -> str | dict[str, Any] | None:
    """Google's function-calling mode as this dialect's `tool_choice`, or nothing for `auto`."""
    choice = request.tool_choice
    if choice is None or choice.mode is ToolMode.AUTO:
        return None
    if choice.mode is ToolMode.NONE:
        return "none"
    if choice.mode is ToolMode.VALIDATED:
        # A `strict` function is the guarantee here, and strict mode refuses most schemas a client
        # writes; answering `auto` without it would drop what was asked.
        raise DialectUnsupported(
            "'VALIDATED' asks the provider to guarantee that a call's arguments match the "
            "declaration, and this dialect is not sent strict functions. Use 'AUTO', or a model "
            "that can."
        )
    if len(choice.allowed) == 1:
        return {"type": "function", "function": {"name": choice.allowed[0]}}
    return "required"


def canonical_to_openai(
    request: CanonicalRequest,
    *,
    stream: bool = False,
    output_cap_field: str = DEFAULT_OUTPUT_CAP_FIELD,
) -> dict[str, Any]:
    """Build a `/v1/chat/completions` body. The system prompt is simply the first message.

    ``output_cap_field`` is the platform's name for the output cap (`Routes.output_cap_field`): a
    platform whose reasoning deployments refuse `max_tokens` takes `max_completion_tokens`.
    """
    body: dict[str, Any] = {
        "model": request.model,
        "messages": _wire_messages(request),
    }
    if request.tools:
        body["tools"] = _wire_tools(request)
        choice = _tool_choice(request)
        if choice is not None:
            body["tool_choice"] = choice
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        body[output_cap_field] = request.max_output_tokens
    if request.thinking is not None:
        body["reasoning_effort"] = _reasoning_effort(request.thinking)
    if request.response_schema is not None:
        body["response_format"] = _response_format(request.response_schema)
    elif request.response_json_schema is not None:
        # As written and **not strict** (`FRD-143`): strict mode refuses most schemas a client
        # writes by hand, and rewriting the caller's JSON Schema to suit it would change it.
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": SCHEMA_NAME,
                "strict": False,
                "schema": request.response_json_schema.to_wire(),
            },
        }
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
        "json_schema": {"name": SCHEMA_NAME, "strict": True, "schema": strict_schema(schema)},
    }


def strict_schema(schema: ResponseSchema) -> dict[str, Any]:
    """Our schema as strict mode accepts it: every object closed, every property required.

    Strict mode refuses an object that allows other keys or leaves a property out of `required`,
    and a Gemini-shaped schema does both by default. Requiring every property keeps the answer
    valid against the schema the caller sent — a document may always carry more than it must —
    where the alternative, a nullable stand-in for an optional property, would put a `null` where
    the caller's schema allows none.
    """
    strict = _strict(to_json_schema(schema))
    assert isinstance(strict, dict)  # a schema's root is an object by construction
    return strict


def _strict(node: Any) -> Any:
    if isinstance(node, list):
        # `anyOf` keeps its variants in a list; an object among them is closed like any other.
        return [_strict(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {key: _strict(value) for key, value in node.items()}
    node_type = out.get("type")
    if node_type == "object" or (isinstance(node_type, list) and "object" in node_type):
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties") or {})
    return out


def schema_refusal(schema: ResponseSchema) -> str | None:
    """Why strict mode cannot express ``schema``, or ``None``.

    Asked by the dispatch chain (`ADR-0012` §3) on a platform that validates strict schemas, so an
    inexpressible one skips the candidate by name instead of failing it with the vendor's 400.
    """
    used = sorted(
        field for field in UNSUPPORTED_STRICT_FIELDS if getattr(schema, field, None) is not None
    )
    if schema.additional_properties is True:
        used.append("additionalProperties: true")
    nested = list((schema.properties or {}).values())
    if schema.items is not None:
        nested.append(schema.items)
    nested.extend(schema.any_of or ())
    for child in nested:
        deeper = schema_refusal(child)
        if deeper is not None:
            return deeper
    if not used:
        return None
    return (
        f"this platform's strict structured output does not support {', '.join(used)}, and a "
        "schema sent without them would be satisfied by an answer the caller's constraint excludes"
    )


def canonical_to_openai_embedding(request: CanonicalEmbeddingRequest) -> dict[str, Any]:
    """`/v1/embeddings` takes the whole batch in one ``input`` array.

    This format has no task type; `FRD-113` refuses an undeclared one before dispatch.
    """
    body: dict[str, Any] = {"model": request.model, "input": list(request.texts)}
    if request.dimensions is not None:
        body["dimensions"] = request.dimensions
    return body
