"""Canonical ⇄ Google Gemini API mappers (FRD-304).

Pure functions (no I/O) that translate between the canonical schema and the real Gemini
request/response bodies. Unit-tested independently of the HTTP client.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    DataPart,
    Role,
    TextPart,
    Thinking,
    ToolCallPart,
    ToolResultPart,
)
from aira_gateway.upstreams.base import DialectUnsupported


def _wire_parts(message: CanonicalMessage) -> list[dict[str, Any]]:
    """Canonical parts → Gemini parts, in order.

    The two formats agree, which is why this direction is cheap — and preserving the order is the
    part that is not merely cosmetic: "this image, then this question" and "this question, then
    this image" are different prompts.
    """
    wire: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            wire.append({"text": part.text})
            continue
        if isinstance(part, ToolCallPart):
            # Google carries no call id and matches a result to a call by **name**, so the id the
            # canonical model holds is simply not sent. It is not lost: it came from here in the
            # first place, or was generated at the surface precisely so the other dialects have one.
            wire.append({"functionCall": {"name": part.name, "args": part.arguments}})
            continue
        if isinstance(part, ToolResultPart):
            wire.append(
                {"functionResponse": {"name": part.name, "response": _result_object(part.content)}}
            )
            continue
        if not isinstance(part, DataPart):  # pragma: no cover - the union is closed above
            raise DialectUnsupported(f"Unsupported part {type(part).__name__}.")
        wire.append(
            {
                "inlineData": {
                    "mimeType": part.media_type,
                    "data": base64.b64encode(part.data).decode("ascii"),
                }
            }
        )
    return wire


def _result_object(content: str) -> dict[str, Any]:
    """Google's ``functionResponse.response`` is an **object**, not a string.

    The canonical model keeps a tool result as text because two of the three dialects want one.
    Here it is parsed back if it is JSON, and wrapped otherwise — a plain string sent where an
    object is expected is rejected by the API, and wrapping is the only lossless answer.
    """
    try:
        parsed = json.loads(content)
    except TypeError, ValueError:
        return {"result": content}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def canonical_to_gemini_request(request: CanonicalRequest) -> dict[str, Any]:
    """Build a Gemini ``generateContent`` request body from a canonical request."""
    contents: list[dict[str, Any]] = []
    system: dict[str, Any] | None = None
    for message in request.messages:
        if message.role is Role.SYSTEM:
            system = {"parts": _wire_parts(message)}
        else:
            role = "model" if message.role is Role.MODEL else "user"
            contents.append({"role": role, "parts": _wire_parts(message)})

    body: dict[str, Any] = {"contents": contents}
    if system is not None:
        body["systemInstruction"] = system
    if request.tools:
        body["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        **(
                            {"parameters": tool.parameters.to_wire()}
                            if tool.parameters is not None
                            else {}
                        ),
                    }
                    for tool in request.tools
                ]
            }
        ]

    generation_config: dict[str, Any] = {}
    if request.temperature is not None:
        generation_config["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        generation_config["maxOutputTokens"] = request.max_output_tokens
    if request.thinking is not None or request.include_reasoning:
        thinking_config: dict[str, Any] = {}
        if request.thinking is not None:
            thinking_config["thinkingBudget"] = thinking_budget(request.thinking)
        if request.include_reasoning:
            # Asked for only where the **use case** allows it (`FRD-135` FR-3). Google returns
            # nothing extra without this, so a use case that turned reasoning on and never saw any
            # would be looking at a switch that changed nothing — the shape `FRD-125` is named for.
            thinking_config["includeThoughts"] = True
        generation_config["thinkingConfig"] = thinking_config
    if request.response_schema is not None:
        # Both fields, always together: `responseSchema` without `responseMimeType` is ignored by
        # the API, which would return prose to a caller expecting a document — the silent-wrong
        # answer this feature exists to prevent, produced by our own request body.
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = request.response_schema.to_wire()
    _add_sampling(generation_config, request)
    if generation_config:
        body["generationConfig"] = generation_config
    return body


#: Google's `GenerationConfig` names all six (`FRD-124`). This is the one dialect that can express
#: everything the canonical request carries, which is exactly why the others must say so when they
#: cannot: the difference is invisible in the response.
SAMPLING = frozenset(
    {"top_p", "top_k", "seed", "presence_penalty", "frequency_penalty", "stop_sequences"}
)

_SAMPLING_WIRE = {
    "top_p": "topP",
    "top_k": "topK",
    "seed": "seed",
    "presence_penalty": "presencePenalty",
    "frequency_penalty": "frequencyPenalty",
}


def _add_sampling(config: dict[str, Any], request: CanonicalRequest) -> None:
    for name, wire in _SAMPLING_WIRE.items():
        value = getattr(request, name)
        if value is not None:
            config[wire] = value
    if request.stop_sequences:
        config["stopSequences"] = list(request.stop_sequences)


def thinking_budget(setting: Thinking) -> int:
    """Google's ``thinkingBudget``: ``0`` off, ``-1`` model's choice, otherwise a token count.

    The abstract levels never reach here as levels — :mod:`aira_gateway.thinking` has already
    turned them into the budget the model's own catalog entry attaches to them, because
    "high" means nothing to an HTTP call and the mapping differs per model.
    """
    if setting.mode is ThinkingMode.DISABLED:
        return 0
    if setting.mode is ThinkingMode.AUTO:
        return -1
    return setting.tokens or -1


def canonical_to_gemini_embedding(request: CanonicalEmbeddingRequest) -> dict[str, Any]:
    """One text → an ``embedContent`` body. Batches wrap these in ``requests``."""
    body: dict[str, Any] = {"content": {"parts": [{"text": request.texts[0]}]}}
    if request.task_type is not None:
        body["taskType"] = request.task_type
    if request.dimensions is not None:
        body["outputDimensionality"] = request.dimensions
    return body


def batch_embedding_body(request: CanonicalEmbeddingRequest, model: str) -> dict[str, Any]:
    """A ``batchEmbedContents`` body: one entry per text, each naming the model as Google requires.

    The order of ``requests`` is the order of the returned embeddings, which is the contract
    `FRD-113` FR-1 makes to the caller — so this must never reorder or deduplicate.
    """
    return {
        "requests": [
            {
                "model": f"models/{model}",
                **canonical_to_gemini_embedding(request.model_copy(update={"texts": [text]})),
            }
            for text in request.texts
        ]
    }


def embedding_values(data: dict[str, Any]) -> list[list[float]]:
    """Read vectors from either shape Google answers with."""
    if "embeddings" in data:
        return [
            [float(value) for value in entry.get("values", [])]
            for entry in data.get("embeddings") or []
        ]
    return [[float(value) for value in (data.get("embedding") or {}).get("values", [])]]


def _text_of(candidate: dict[str, Any]) -> str:
    """The answer, **without** the reasoning.

    Google returns thoughts as ordinary text parts flagged `thought: true`, in the same array. This
    used to join everything, which is exactly why `includeThoughts` was refused: asking for
    reasoning would have delivered it glued to the front of the answer, and a caller could not tell
    which was which (`FRD-135` §5).
    """
    parts = candidate.get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts if not part.get("thought"))


def _reasoning_of(candidate: dict[str, Any]) -> str:
    """What the model thought, where it was asked for and returned."""
    parts = candidate.get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts if part.get("thought"))


def _usage_of(data: dict[str, Any]) -> CanonicalUsage:
    meta = data.get("usageMetadata") or {}
    # **Thinking is output, and Google bills it as output** (`FRD-135` FR-1). `candidatesTokenCount`
    # counts only the visible answer, so adding thoughts here is what makes `completion_tokens` mean
    # "what this response cost" rather than "what of it was printed". Read from nowhere until
    # 2026-08-17: a measured request counted 143 thought tokens against 1 candidate token, and 85%
    # of what the provider charged for was invisible to every budget and every report.
    thoughts = int(meta.get("thoughtsTokenCount", 0) or 0)
    return CanonicalUsage(
        prompt_tokens=int(meta.get("promptTokenCount", 0)),
        completion_tokens=int(meta.get("candidatesTokenCount", 0)) + thoughts,
        reasoning_tokens=thoughts,
        # Implicit caching is on by default from Gemini 2.5 and needs nothing sent; this count is
        # the only evidence it happened (`FRD-133` §4a). `promptTokenCount` already includes it,
        # so this is a subset and not an addition — the same invariant every dialect keeps.
        cached_input_tokens=int(meta.get("cachedContentTokenCount", 0) or 0),
    )


def _calls_of(candidate: dict[str, Any]) -> tuple[ToolCallPart, ...]:
    """The function calls in one candidate, in order.

    Google sends no id, so one is generated from the name and position — deterministically, so a
    caller that echoes it back in the next turn still matches, and so the other two dialects have
    the id they require.
    """
    calls: list[ToolCallPart] = []
    for index, part in enumerate(candidate.get("content", {}).get("parts", []) or []):
        call = part.get("functionCall")
        if not call:
            continue
        name = str(call.get("name") or "")
        if not name:
            continue
        arguments = call.get("args")
        calls.append(
            ToolCallPart(
                id=str(call.get("id") or f"{name}-{index}"),
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)


def gemini_response_to_canonical(data: dict[str, Any], model: str) -> CanonicalResponse:
    """Parse a Gemini ``generateContent`` response into a canonical response."""
    candidates = data.get("candidates") or []
    text = ""
    finish_reason = "stop"
    calls: tuple[ToolCallPart, ...] = ()
    reasoning = ""
    if candidates:
        text = _text_of(candidates[0])
        reasoning = _reasoning_of(candidates[0])
        finish_reason = str(candidates[0].get("finishReason", "STOP")).lower()
        calls = _calls_of(candidates[0])
    return CanonicalResponse(
        model=model,
        text=text,
        reasoning=reasoning,
        finish_reason=finish_reason,
        usage=_usage_of(data),
        tool_calls=calls,
    )


def gemini_chunk_to_canonical(data: dict[str, Any]) -> CanonicalChunk:
    """Parse one Gemini stream chunk into a canonical chunk."""
    candidates = data.get("candidates") or []
    text = _text_of(candidates[0]) if candidates else ""
    finish = candidates[0].get("finishReason") if candidates else None
    usage = _usage_of(data) if data.get("usageMetadata") else None
    # **Whole, never in pieces.** Unlike the OpenAI dialect, Google sends a function call complete
    # inside one chunk — there is nothing to reassemble here, and writing an accumulator anyway
    # would be a mechanism defending against a problem this wire format does not have.
    calls = _calls_of(candidates[0]) if candidates else ()
    return CanonicalChunk(
        text_delta=text,
        finish_reason=str(finish).lower() if finish else None,
        usage=usage,
        tool_calls=calls,
    )
