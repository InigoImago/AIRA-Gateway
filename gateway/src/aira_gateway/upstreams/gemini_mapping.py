"""Canonical ⇄ Google Gemini API mappers (FRD-304).

Pure functions (no I/O) that translate between the canonical schema and the real Gemini
request/response bodies. Unit-tested independently of the HTTP client.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
)


def canonical_to_gemini_request(request: CanonicalRequest) -> dict[str, Any]:
    """Build a Gemini ``generateContent`` request body from a canonical request."""
    contents: list[dict[str, Any]] = []
    system: dict[str, Any] | None = None
    for message in request.messages:
        if message.role is Role.SYSTEM:
            system = {"parts": [{"text": message.text}]}
        else:
            role = "model" if message.role is Role.MODEL else "user"
            contents.append({"role": role, "parts": [{"text": message.text}]})

    body: dict[str, Any] = {"contents": contents}
    if system is not None:
        body["systemInstruction"] = system

    generation_config: dict[str, Any] = {}
    if request.temperature is not None:
        generation_config["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        generation_config["maxOutputTokens"] = request.max_output_tokens
    if generation_config:
        body["generationConfig"] = generation_config
    return body


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
