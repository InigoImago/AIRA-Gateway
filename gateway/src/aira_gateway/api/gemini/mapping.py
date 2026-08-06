"""Gemini ⇄ canonical mappers (FRD-100).

Kept free of FastAPI so they are trivially unit-testable and reusable.
"""

from __future__ import annotations

from aira_gateway.api.gemini import schemas
from aira_gateway.attachments import (
    Limits,
    check_bounds,
    check_media_type,
    check_signature,
    decode,
)
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalPart,
    CanonicalRequest,
    CanonicalResponse,
    DataPart,
    Role,
    TextPart,
)
from aira_gateway.upstreams.base import UpstreamModel

_ROLE_FROM_GEMINI = {"user": Role.USER, "model": Role.MODEL, "system": Role.SYSTEM}


def _canonical_parts(content: schemas.Content, limits: Limits, offset: int) -> list[CanonicalPart]:
    """Map one content's parts, preserving their order.

    Order matters: "this image, then this question" and "this question, then this image" are
    different prompts, and a mapper that collected the text and appended the attachments would
    silently rewrite one into the other.

    Every check runs **here**, at the surface, before a canonical request exists — the same place
    the body ceiling already runs. Nothing downstream should ever hold a part that failed one, and
    no upstream adapter should have to repeat one.
    """
    parts: list[CanonicalPart] = []
    for offset_index, part in enumerate(content.parts):
        index = offset + offset_index
        if part.text is not None:
            parts.append(TextPart(text=part.text))
            continue
        assert part.inlineData is not None  # the schema validator guarantees one or the other
        media_type = part.inlineData.mimeType
        check_media_type(media_type, limits, index=index)
        data = decode(part.inlineData.data, index=index)
        check_signature(media_type, data, index=index)
        parts.append(DataPart(media_type=media_type, data=data))
    return parts


def gemini_to_canonical(
    model: str, request: schemas.GenerateContentRequest, limits: Limits | None = None
) -> CanonicalRequest:
    """Map a Gemini ``GenerateContentRequest`` to a canonical request."""
    limits = limits or Limits()
    messages: list[CanonicalMessage] = []
    counted = 0
    if request.systemInstruction is not None:
        parts = _canonical_parts(request.systemInstruction, limits, counted)
        counted += len(parts)
        messages.append(CanonicalMessage(role=Role.SYSTEM, parts=parts))
    for content in request.contents:
        role = _ROLE_FROM_GEMINI.get(content.role or "user", Role.USER)
        parts = _canonical_parts(content, limits, counted)
        counted += len(parts)
        messages.append(CanonicalMessage(role=role, parts=parts))

    # Counted across the whole request rather than per message: a caller splitting one large
    # document over five messages is sending one large request.
    check_bounds([part.size for message in messages for part in message.attachments], limits)

    config = request.generationConfig
    return CanonicalRequest(
        model=model,
        messages=messages,
        temperature=config.temperature if config else None,
        max_output_tokens=config.maxOutputTokens if config else None,
    )


def canonical_to_gemini(response: CanonicalResponse) -> schemas.GenerateContentResponse:
    """Map a canonical response back to a Gemini ``GenerateContentResponse``."""
    return schemas.GenerateContentResponse(
        candidates=[
            schemas.Candidate(
                content=schemas.Content(role="model", parts=[schemas.Part(text=response.text)]),
                finishReason=response.finish_reason.upper(),
                index=0,
            )
        ],
        usageMetadata=schemas.UsageMetadata(
            promptTokenCount=response.usage.prompt_tokens,
            candidatesTokenCount=response.usage.completion_tokens,
            totalTokenCount=response.usage.total_tokens,
        ),
        modelVersion=response.model,
    )


def upstream_model_to_gemini(model: UpstreamModel) -> schemas.GeminiModel:
    """Map upstream model metadata to a Gemini ``Model`` resource."""
    return schemas.GeminiModel(
        name=f"models/{model.name}",
        version=model.version,
        displayName=model.name,
        supportedGenerationMethods=list(model.supported_methods),
    )
