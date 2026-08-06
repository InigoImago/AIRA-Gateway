"""Gemini ⇄ canonical mappers (FRD-100).

Kept free of FastAPI so they are trivially unit-testable and reusable.
"""

from __future__ import annotations

from aira_common.models import ThinkingMode
from aira_gateway.api.gemini import schemas
from aira_gateway.attachments import (
    AttachmentRejected,
    Limits,
    check_bounds,
    check_media_type,
    check_signature,
    decode,
)
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalMessage,
    CanonicalPart,
    CanonicalRequest,
    CanonicalResponse,
    DataPart,
    Role,
    TextPart,
    Thinking,
)
from aira_gateway.core.schema import SchemaBounds
from aira_gateway.core.schema import parse as parse_schema
from aira_gateway.thinking import INVALID_THINKING_MODE, ThinkingRejected
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
    model: str,
    request: schemas.GenerateContentRequest,
    limits: Limits | None = None,
    bounds: SchemaBounds | None = None,
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
        thinking=thinking_of(config.thinkingConfig) if config else None,
        response_schema=(
            parse_schema(config.responseSchema, bounds)
            if config and config.responseSchema is not None
            else None
        ),
    )


_BUDGET_MODES = {0: ThinkingMode.DISABLED, -1: ThinkingMode.AUTO}


def thinking_of(config: schemas.ThinkingConfig | None) -> Thinking | None:
    """Google's ``thinkingBudget`` or the canonical ``mode``/``tokens``, onto one concept.

    A numeric budget carries no mode, so the two sentinel values Google gives meaning to are read
    as the modes they *are*: ``0`` is off and ``-1`` is the model's own choice. Mapping them to
    ``limited`` with a budget of zero would ask a provider for zero thinking tokens, which is not
    the same request and not obviously legal.
    """
    if config is None:
        return None
    if config.thinkingBudget is not None:
        mode = _BUDGET_MODES.get(config.thinkingBudget, ThinkingMode.LIMITED)
        tokens = config.thinkingBudget if mode is ThinkingMode.LIMITED else None
        return Thinking(mode=mode, tokens=tokens)
    if config.mode is None:
        return None
    try:
        mode = ThinkingMode(config.mode.strip().lower())
    except ValueError as exc:
        raise ThinkingRejected(
            INVALID_THINKING_MODE,
            f"'{config.mode}' is not a thinking mode. "
            f"Known: {sorted(str(m) for m in ThinkingMode)}.",
        ) from exc
    return Thinking(mode=mode, tokens=config.tokens)


def gemini_to_embedding(
    model: str, requests: list[schemas.EmbedContentRequest]
) -> CanonicalEmbeddingRequest:
    """One or many Gemini embed requests onto one canonical batch.

    An attachment is refused rather than dropped: `FRD-113` is explicit that embedding a document
    means chunking it, which is the consumer's decision — and embedding the prompt while silently
    discarding the file would return a vector that is confidently about the wrong thing.

    A batch that mixes task types or dimensionalities is refused too. Google takes them per entry;
    we take one per call because that is what is metered, validated against the model and recorded.
    Serving a mixed batch would mean an audit row that names one task type for vectors built with
    several.
    """
    texts: list[str] = []
    task_types: set[str] = set()
    dimensions: set[int] = set()
    for index, entry in enumerate(requests):
        if any(part.inlineData is not None for part in entry.content.parts):
            raise AttachmentRejected(
                f"requests[{index}]: embedding takes text only; send the document to a generate "
                "verb instead."
            )
        texts.append("".join(part.text or "" for part in entry.content.parts))
        if entry.taskType is not None:
            task_types.add(entry.taskType)
        if entry.outputDimensionality is not None:
            dimensions.add(entry.outputDimensionality)

    if len(task_types) > 1 or len(dimensions) > 1:
        raise AttachmentRejected(
            "One embedding call carries one task type and one output dimensionality. Split the "
            "batch, or the vectors would be built to different specifications under one record."
        )
    return CanonicalEmbeddingRequest(
        model=model,
        texts=texts,
        task_type=next(iter(task_types), None),
        dimensions=next(iter(dimensions), None),
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
