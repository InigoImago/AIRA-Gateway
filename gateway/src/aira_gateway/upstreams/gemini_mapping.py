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
    FinishReason,
    Role,
    Speech,
    TextPart,
    Thinking,
    ToolCallPart,
    ToolResultPart,
)
from aira_gateway.upstreams.base import DialectUnsupported, UpstreamError, stream_failure

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

#: Google's finish reasons in the canonical vocabulary. Every word for a safety or policy system
#: stopping the answer is one event, `REFUSAL` — recitation included: the answer was withheld by a
#: policy, not finished. Anything not listed (`OTHER`, `LANGUAGE`, a malformed call, a word Google
#: adds later) is `OTHER`: not evidence of a complete answer.
_FINISH_REASONS = {
    "STOP": FinishReason.STOP,
    "MAX_TOKENS": FinishReason.MAX_TOKENS,
    "SAFETY": FinishReason.REFUSAL,
    "RECITATION": FinishReason.REFUSAL,
    "BLOCKLIST": FinishReason.REFUSAL,
    "PROHIBITED_CONTENT": FinishReason.REFUSAL,
    "SPII": FinishReason.REFUSAL,
    "IMAGE_SAFETY": FinishReason.REFUSAL,
    "IMAGE_PROHIBITED_CONTENT": FinishReason.REFUSAL,
    "IMAGE_RECITATION": FinishReason.REFUSAL,
}

#: The fields of `usageMetadata` that count tokens; one of them is what makes it a report.
USAGE_COUNT_FIELDS = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")

#: Google's field for the opaque signature a thinking model puts on the part it called a function
#: from, and refuses a replayed call without (`ToolCallPart.signature`).
_SIGNATURE_FIELD = "thoughtSignature"


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
            call: dict[str, Any] = {"functionCall": {"name": part.name, "args": part.arguments}}
            if part.signature:
                # Back on the part it came from: the model validates it on the call it continues.
                call[_SIGNATURE_FIELD] = part.signature
            wire.append(call)
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
    # One `systemInstruction` here and several system messages canonically — the caller's own and
    # a `system` turn, say: every one is kept, in order. Keeping the last would discard an
    # instruction the caller gave, with nothing in the answer to show it.
    system_parts: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role is Role.SYSTEM:
            system_parts.extend(_wire_parts(message))
        else:
            role = "model" if message.role is Role.MODEL else "user"
            contents.append({"role": role, "parts": _wire_parts(message)})

    body: dict[str, Any] = {"contents": contents}
    if system_parts:
        body["systemInstruction"] = {"parts": system_parts}
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


#: What `:countTokens` counts of a generation's body: what it is billed for as input. The rest —
#: sampling, safety, the tool mode — shapes the answer and adds no input token.
_COUNTED_FIELDS = ("contents", "systemInstruction", "tools")


def count_tokens_body(body: dict[str, Any]) -> dict[str, Any]:
    """The `:countTokens` body for a generation's ``body``: the parts of it billed as input."""
    return {field: body[field] for field in _COUNTED_FIELDS if field in body}


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


def _reports_usage(data: dict[str, Any]) -> bool:
    """Whether a chunk says how many tokens were used. Vertex puts `usageMetadata` on every chunk
    of a stream, holding only `trafficType` until the last one: an object with no count in it is
    no report, and reading it as one books a stream the caller left as having cost nothing."""
    meta = data.get("usageMetadata")
    return isinstance(meta, dict) and any(field in meta for field in USAGE_COUNT_FIELDS)


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
        signature = part.get(_SIGNATURE_FIELD)
        calls.append(
            ToolCallPart(
                id=str(call.get("id") or f"{name}-{index}"),
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
                signature=signature if isinstance(signature, str) and signature else None,
            )
        )
    return tuple(calls)


def _finish_of(raw: Any, calls: tuple[ToolCallPart, ...]) -> FinishReason:
    """Google's word for how a candidate ended, in the canonical vocabulary.

    A function call ends a Gemini answer with `STOP`, and every other dialect with its own word for
    a call: it is the same event, so it is the same word here.
    """
    reason = _FINISH_REASONS.get(str(raw).upper(), FinishReason.OTHER)
    return FinishReason.TOOL_USE if reason is FinishReason.STOP and calls else reason


def _blocked(data: dict[str, Any]) -> bool:
    """Whether Google refused the **prompt**: then there is no candidate, only the reason."""
    feedback = data.get("promptFeedback")
    return isinstance(feedback, dict) and bool(feedback.get("blockReason"))


def gemini_response_to_canonical(data: dict[str, Any], model: str) -> CanonicalResponse:
    """Parse a Gemini ``generateContent`` response into a canonical response.

    A prompt Google blocked has no candidate and says why in `promptFeedback`: that is a refusal,
    never an empty answer that finished normally — which a structured request would take for a
    document. An answer with neither a candidate nor a blocked prompt is no answer at all, and the
    upstream's fault.
    """
    candidates = data.get("candidates") or []
    if not candidates:
        if not _blocked(data):
            raise UpstreamError("The upstream answered with no candidate and no reason.", 502)
        return CanonicalResponse(
            model=model, text="", finish_reason=FinishReason.REFUSAL, usage=_usage_of(data)
        )
    calls = _calls_of(candidates[0])
    return CanonicalResponse(
        model=model,
        text=_text_of(candidates[0]),
        reasoning=_reasoning_of(candidates[0]),
        finish_reason=_finish_of(candidates[0].get("finishReason", "STOP"), calls),
        usage=_usage_of(data),
        tool_calls=calls,
        audio=_audio_of(candidates[0]),
    )


def gemini_chunk_to_canonical(data: dict[str, Any]) -> CanonicalChunk:
    """Parse one Gemini stream chunk into a canonical chunk.

    Google sends a function call whole inside one chunk, so, unlike the OpenAI dialect, there is
    nothing to reassemble. An `error` payload is the upstream failing mid-stream and is raised
    (`stream_failure`); a blocked prompt ends the stream as a refusal.
    """
    if data.get("error") is not None:
        raise stream_failure(data["error"])
    candidates = data.get("candidates") or []
    usage = _usage_of(data) if _reports_usage(data) else None
    if not candidates:
        return CanonicalChunk(
            text_delta="",
            finish_reason=FinishReason.REFUSAL if _blocked(data) else None,
            usage=usage,
        )
    calls = _calls_of(candidates[0])
    finish = candidates[0].get("finishReason")
    return CanonicalChunk(
        text_delta=_text_of(candidates[0]),
        finish_reason=_finish_of(finish, calls) if finish else None,
        usage=usage,
        tool_calls=calls,
        audio_delta=_audio_of(candidates[0]),
    )
