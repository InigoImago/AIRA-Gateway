"""Canonical ⇄ Google Gemini API mappers (FRD-304).

Pure functions (no I/O) that translate between the canonical schema and the real Gemini
request/response bodies. Unit-tested independently of the HTTP client.
"""

from __future__ import annotations

import base64
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
    TextPart,
    Thinking,
)


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
        else:
            wire.append(
                {
                    "inlineData": {
                        "mimeType": part.media_type,
                        "data": base64.b64encode(part.data).decode("ascii"),
                    }
                }
            )
    return wire


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

    generation_config: dict[str, Any] = {}
    if request.temperature is not None:
        generation_config["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        generation_config["maxOutputTokens"] = request.max_output_tokens
    if request.thinking is not None:
        generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget(request.thinking)}
    if request.response_schema is not None:
        # Both fields, always together: `responseSchema` without `responseMimeType` is ignored by
        # the API, which would return prose to a caller expecting a document — the silent-wrong
        # answer this feature exists to prevent, produced by our own request body.
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = request.response_schema.to_wire()
    if generation_config:
        body["generationConfig"] = generation_config
    return body


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
    parts = candidate.get("content", {}).get("parts", [])
    return "".join(part.get("text", "") for part in parts)


def _usage_of(data: dict[str, Any]) -> CanonicalUsage:
    meta = data.get("usageMetadata") or {}
    return CanonicalUsage(
        prompt_tokens=int(meta.get("promptTokenCount", 0)),
        completion_tokens=int(meta.get("candidatesTokenCount", 0)),
    )


def gemini_response_to_canonical(data: dict[str, Any], model: str) -> CanonicalResponse:
    """Parse a Gemini ``generateContent`` response into a canonical response."""
    candidates = data.get("candidates") or []
    text = ""
    finish_reason = "stop"
    if candidates:
        text = _text_of(candidates[0])
        finish_reason = str(candidates[0].get("finishReason", "STOP")).lower()
    return CanonicalResponse(
        model=model, text=text, finish_reason=finish_reason, usage=_usage_of(data)
    )


def gemini_chunk_to_canonical(data: dict[str, Any]) -> CanonicalChunk:
    """Parse one Gemini stream chunk into a canonical chunk."""
    candidates = data.get("candidates") or []
    text = _text_of(candidates[0]) if candidates else ""
    finish = candidates[0].get("finishReason") if candidates else None
    usage = _usage_of(data) if data.get("usageMetadata") else None
    return CanonicalChunk(
        text_delta=text,
        finish_reason=str(finish).lower() if finish else None,
        usage=usage,
    )
