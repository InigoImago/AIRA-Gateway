"""Gemini ⇄ canonical mappers (FRD-100).

Kept free of FastAPI so they are trivially unit-testable and reusable.
"""

from __future__ import annotations

from aira_gateway.api.gemini import schemas
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    Role,
)
from aira_gateway.upstreams.base import UpstreamModel

_ROLE_FROM_GEMINI = {"user": Role.USER, "model": Role.MODEL, "system": Role.SYSTEM}


def _join_parts(content: schemas.Content) -> str:
    return "".join(part.text for part in content.parts)


def gemini_to_canonical(model: str, request: schemas.GenerateContentRequest) -> CanonicalRequest:
    """Map a Gemini ``GenerateContentRequest`` to a canonical request."""
    messages: list[CanonicalMessage] = []
    if request.systemInstruction is not None:
        messages.append(
            CanonicalMessage(role=Role.SYSTEM, text=_join_parts(request.systemInstruction))
        )
    for content in request.contents:
        role = _ROLE_FROM_GEMINI.get(content.role or "user", Role.USER)
        messages.append(CanonicalMessage(role=role, text=_join_parts(content)))

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
