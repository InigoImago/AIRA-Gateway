"""Canonical ⇄ the Google Gemini wire format (`FRD-304`).

Pure functions, no I/O, tested without HTTP. Shared by Google AI Studio and Vertex's Gemini adapter.
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
    Speech,
    TextPart,
    Thinking,
    ToolCallPart,
    ToolResultPart,
)
from aira_gateway.upstreams.base import DialectUnsupported

#: Google's `GenerationConfig` names all six sampling controls (`FRD-124`) — the one dialect that
#: can express everything the canonical request carries.
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


# == canonical → Gemini ===========================================================================


def _wire_parts(message: CanonicalMessage) -> list[dict[str, Any]]:
    """Canonical parts → Gemini parts, **in order**: "this image, then this question" and the
    reverse are different prompts."""
    wire: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            wire.append({"text": part.text})
            continue
        if isinstance(part, ToolCallPart):
            # Google matches a result to a call by name and carries no call id, so none is sent.
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
    """A tool result as the **object** ``functionResponse.response`` requires.

    The canonical model keeps a result as text; JSON is parsed back, anything else is wrapped —
    the API rejects a plain string, and wrapping is the only lossless answer.
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
    # Thinking is on for this request only where a setting says so: the caller's, or the model's
    # declared default. Google refuses `includeThoughts` whenever the model is not thinking — with
    # thinking switched off, and for a model that never switched it on, such as a speech model or
    # one that thinks only when given a budget.
    thinking_on = request.thinking is not None and request.thinking.mode != ThinkingMode.DISABLED
    if request.thinking is not None:
        generation_config["thinkingConfig"] = thinking_fields(request.thinking)
    # Only where the use case allows it (`FRD-135` FR-3).
    if request.include_reasoning and thinking_on:
        generation_config["thinkingConfig"]["includeThoughts"] = True
    if request.response_schema is not None:
        # Both fields, always together: the API ignores `responseSchema` without the MIME type
        # and would return prose to a caller expecting a document.
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = request.response_schema.to_wire()
    _add_sampling(generation_config, request)
    if request.speech is not None:
        # Both, always together (`FRD-624`): the modality without a voice is refused without a
        # reason, and a voice without the modality has nothing to speak.
        generation_config["responseModalities"] = ["AUDIO"]
        generation_config["speechConfig"] = speech_fields(request.speech)
    if generation_config:
        body["generationConfig"] = generation_config
    return body


def _add_sampling(config: dict[str, Any], request: CanonicalRequest) -> None:
    for name, wire in _SAMPLING_WIRE.items():
        value = getattr(request, name)
        if value is not None:
            config[wire] = value
    if request.stop_sequences:
        config["stopSequences"] = list(request.stop_sequences)


def thinking_fields(setting: Thinking) -> dict[str, Any]:
    """Google's thinking config: a **budget** for our three modes, a **level** for a level word.

    ``thinkingBudget`` is a token count (``0`` off, ``-1`` the model's choice); ``thinkingLevel`` is
    a word that Gemini 3 takes and 2.5 refuses, which is why it is declared per model rather than
    decided by a version check here. A level goes out as the word, never as an invented token
    ceiling (`ADR-0021`).
    """
    if setting.mode == ThinkingMode.DISABLED:
        return {"thinkingBudget": 0}
    if setting.mode == ThinkingMode.AUTO:
        return {"thinkingBudget": -1}
    if setting.mode == ThinkingMode.LIMITED:
        return {"thinkingBudget": setting.tokens or -1}
    return {"thinkingLevel": setting.mode}


def speech_fields(speech: Speech) -> dict[str, Any]:
    """Google's `speechConfig`, in the camelCase its API documents (`FRD-624`)."""

    def voice(name: str) -> dict[str, Any]:
        return {"prebuiltVoiceConfig": {"voiceName": name}}

    config: dict[str, Any] = (
        {
            "multiSpeakerVoiceConfig": {
                "speakerVoiceConfigs": [
                    {"speaker": entry.speaker, "voiceConfig": voice(entry.voice)}
                    for entry in speech.speakers
                ]
            }
        }
        if speech.speakers
        else {"voiceConfig": voice(speech.voice or "")}
    )
    if speech.language:
        config["languageCode"] = speech.language
    return config


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

    The order of ``requests`` is the order of the returned embeddings — the contract `FRD-113` FR-1
    makes to the caller — so this must never reorder or deduplicate.
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


# == Gemini → canonical ===========================================================================


def embedding_values(data: dict[str, Any]) -> list[list[float]]:
    """Read vectors from either shape Google answers with."""
    if "embeddings" in data:
        return [
            [float(value) for value in entry.get("values", [])]
            for entry in data.get("embeddings") or []
        ]
    return [[float(value) for value in (data.get("embedding") or {}).get("values", [])]]


def _text_of(candidate: dict[str, Any]) -> str:
    """The answer, **without** the reasoning: Google flags thoughts as `thought: true` text parts in
    the same array, and joining everything would glue them to the answer (`FRD-135` §5)."""
    parts = candidate.get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts if not part.get("thought"))


def _reasoning_of(candidate: dict[str, Any]) -> str:
    """What the model thought, where it was asked for and returned."""
    parts = candidate.get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts if part.get("thought"))


def _audio_of(candidate: dict[str, Any]) -> DataPart | None:
    """The spoken answer (`FRD-624`): every audio part, decoded and joined in order."""
    pieces: list[bytes] = []
    media_type = ""
    for part in candidate.get("content", {}).get("parts", []) or []:
        inline = part.get("inlineData") or {}
        mime = str(inline.get("mimeType") or "")
        if not mime.startswith("audio/"):
            continue
        media_type = media_type or mime
        pieces.append(base64.b64decode(inline.get("data") or ""))
    return DataPart(media_type=media_type, data=b"".join(pieces)) if pieces else None


def _usage_of(data: dict[str, Any]) -> CanonicalUsage:
    meta = data.get("usageMetadata") or {}
    # Thinking is output and billed as output (`FRD-135` FR-1); `candidatesTokenCount` counts only
    # the visible answer, so the thoughts are added to make `completion_tokens` the full cost.
    thoughts = int(meta.get("thoughtsTokenCount", 0) or 0)
    return CanonicalUsage(
        prompt_tokens=int(meta.get("promptTokenCount", 0)),
        completion_tokens=int(meta.get("candidatesTokenCount", 0)) + thoughts,
        reasoning_tokens=thoughts,
        # Implicit caching needs nothing sent; this count is its only evidence (`FRD-133` §4a).
        # A subset of `promptTokenCount`, never an addition.
        cached_input_tokens=int(meta.get("cachedContentTokenCount", 0) or 0),
    )


def _calls_of(candidate: dict[str, Any]) -> tuple[ToolCallPart, ...]:
    """The function calls in one candidate, in order.

    Google sends no id, so one is derived from name and position — deterministically, so a caller
    echoing it back still matches, and the other dialects get the id they require.
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
    audio: DataPart | None = None
    if candidates:
        text = _text_of(candidates[0])
        reasoning = _reasoning_of(candidates[0])
        audio = _audio_of(candidates[0])
        finish_reason = str(candidates[0].get("finishReason", "STOP")).lower()
        calls = _calls_of(candidates[0])
    return CanonicalResponse(
        model=model,
        text=text,
        reasoning=reasoning,
        finish_reason=finish_reason,
        usage=_usage_of(data),
        tool_calls=calls,
        audio=audio,
    )


def gemini_chunk_to_canonical(data: dict[str, Any]) -> CanonicalChunk:
    """Parse one Gemini stream chunk into a canonical chunk.

    Google sends a function call whole inside one chunk, so, unlike the OpenAI dialect, there is
    nothing to reassemble.
    """
    candidates = data.get("candidates") or []
    text = _text_of(candidates[0]) if candidates else ""
    finish = candidates[0].get("finishReason") if candidates else None
    usage = _usage_of(data) if data.get("usageMetadata") else None
    calls = _calls_of(candidates[0]) if candidates else ()
    return CanonicalChunk(
        text_delta=text,
        finish_reason=str(finish).lower() if finish else None,
        usage=usage,
        tool_calls=calls,
        audio_delta=_audio_of(candidates[0]) if candidates else None,
    )
