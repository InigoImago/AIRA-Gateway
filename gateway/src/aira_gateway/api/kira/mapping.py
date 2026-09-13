"""KIRA ⇄ canonical (FRD-107). Pure functions, free of FastAPI, like `api/gemini/mapping.py`.

Nothing is approximated: a field that cannot be honoured is refused here rather than dropped,
because a dropped field produces an answer that is wrong for a reason the caller cannot see.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.api.kira import schemas
from aira_gateway.attachments import Limits, check_media_type, check_signature, decode
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
from aira_gateway.embedding import EMPTY_EMBEDDING_INPUT, EmbeddingRejected
from aira_gateway.thinking import mode_from

#: What the predecessor puts between two text parts of one message: it sends them as one string.
#: Kept as separate parts, each adapter would render them differently ("HalloWelt" on one).
TEXT_PART_SEPARATOR = "\n"


def _parts(content: schemas.RequestContent, limits: Limits, offset: int) -> list[CanonicalPart]:
    """The predecessor's parts, with its joining rule applied (`FRD-107` FR-2).

    Each *run* of text parts becomes one text; an attachment ends a run, so a message that
    interleaves text and attachments keeps its order.
    """
    parts: list[CanonicalPart] = []
    pending: list[str] = []

    def _flush() -> None:
        if pending:
            parts.append(TextPart(text=TEXT_PART_SEPARATOR.join(pending)))
            pending.clear()

    for local, raw in enumerate(content.parts):
        index = offset + local
        if "text" in raw:
            # The type was checked when the request was parsed (`RequestContent`).
            pending.append(raw["text"])
            continue
        _flush()
        media_type = str(raw.get("mime_type") or raw.get("mimeType"))
        check_media_type(media_type, limits, index=index)
        data = decode(str(raw.get("data", "")), index=index)
        check_signature(media_type, data, index=index)
        parts.append(DataPart(media_type=media_type, data=data))
    _flush()
    return parts


def thinking_of(setting: schemas.ThinkingSetting | None) -> Thinking | None:
    """The predecessor's ``{mode, tokens}`` onto the canonical one (`FRD-111` §5.1).

    An unknown mode is refused by `thinking.mode_from` with the contract's own code — the same
    function the Gemini surface uses, so the two cannot disagree about a mode.
    """
    if setting is None:
        return None
    return Thinking(mode=mode_from(setting.mode), tokens=setting.tokens)


def to_canonical(
    request: schemas.ChatRequest,
    model: str,
    limits: Limits | None = None,
    bounds: SchemaBounds | None = None,
) -> CanonicalRequest:
    """Map a KIRA chat request onto the canonical one.

    History arrives oldest-first and is placed before the current turn — the order every provider
    expects.
    """
    limits = limits or Limits()
    messages: list[CanonicalMessage] = []
    counted = 0

    if request.system_instruction is not None:
        parts = _parts(request.system_instruction, limits, counted)
        counted += len(parts)
        messages.append(CanonicalMessage(role=Role.SYSTEM, parts=parts))

    for turn in request.conversation_history or []:
        parts = _parts(turn.content, limits, counted)
        counted += len(parts)
        role = Role.MODEL if turn.role == "model" else Role.USER
        messages.append(CanonicalMessage(role=role, parts=parts))

    parts = _parts(request.request, limits, counted)
    messages.append(CanonicalMessage(role=Role.USER, parts=parts))

    return CanonicalRequest(
        model=model,
        messages=messages,
        temperature=request.temperature,
        max_output_tokens=request.max_tokens,
        thinking=thinking_of(request.thinking),
        response_schema=(
            parse_schema(request.response_schema, bounds)
            if request.response_schema is not None
            else None
        ),
    )


def to_chat_response(response: CanonicalResponse) -> schemas.ChatResponse:
    return schemas.ChatResponse(
        parts=[schemas.TextPart(text=response.text)],
        usage_data=schemas.UsageDataDto(
            token_input=response.usage.prompt_tokens,
            token_output=response.usage.completion_tokens,
        ),
    )


def completed_event(response: CanonicalResponse) -> dict[str, Any]:
    """The predecessor's terminal SSE event."""
    return {"status": "completed", "data": to_chat_response(response).model_dump()}


def update_event(message: str) -> dict[str, Any]:
    return {"status": "update", "data": message}


def to_embedding(request: schemas.EmbeddingRequest, model: str) -> CanonicalEmbeddingRequest:
    """The predecessor's embedding request onto the canonical one.

    - **A list is one embedding**, as in the predecessor (confirmed from its source, `FRD-113`
      §11). Its texts are joined with nothing between them, which is how the provider combines a
      multi-part content; `TEXT_PART_SEPARATOR` is the *chat* rule and would change every vector.
    - **A blank entry is refused** with the contract's `EMPTY_EMBEDDING_INPUT` code, because the
      join would otherwise absorb it without trace.
    - The default task type is the route's to pass (it applies only where the model declares it),
      and dimensionality is part of a model's identity in the predecessor (one catalogue row per
      width) — so neither is set here.
    """
    entries = [request.text] if isinstance(request.text, str) else list(request.text)

    if not entries or any(not entry.strip() for entry in entries):
        blanks = [index for index, entry in enumerate(entries) if not entry.strip()]
        where = f" at position(s) {', '.join(str(i) for i in blanks)}" if blanks else ""
        raise EmbeddingRejected(
            EMPTY_EMBEDDING_INPUT,
            f"Embedding input must be a non-empty text, or a list of them{where}.",
        )

    return CanonicalEmbeddingRequest(
        model=model, texts=["".join(entries)], task_type=request.task_type
    )
