"""KIRA ⇄ canonical (FRD-107).

Pure functions, no FastAPI — the same shape as `api/gemini/mapping.py`, which is what makes a
third surface a copy of a known pattern rather than a new invention.

The one thing this file will not do is **approximate**. A field Stage A cannot honour is raised as
a refusal here, at the mapping boundary, rather than dropped on the way through. The difference
matters: a dropped field produces an answer that is wrong for a reason the caller cannot see, which
is the same failure documents have (`FRD-110`) one level up.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.api.kira import errors, schemas
from aira_gateway.attachments import Limits, check_media_type, check_signature, decode
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalPart,
    CanonicalRequest,
    CanonicalResponse,
    DataPart,
    Role,
    TextPart,
)


def _parts(content: schemas.RequestContent, limits: Limits, offset: int) -> list[CanonicalPart]:
    parts: list[CanonicalPart] = []
    for local, raw in enumerate(content.parts):
        index = offset + local
        if "text" in raw:
            parts.append(TextPart(text=str(raw["text"])))
            continue
        media_type = str(raw.get("mime_type") or raw.get("mimeType"))
        check_media_type(media_type, limits, index=index)
        data = decode(str(raw.get("data", "")), index=index)
        check_signature(media_type, data, index=index)
        parts.append(DataPart(media_type=media_type, data=data))
    return parts


def refuse_unsupported(request: schemas.ChatRequest, declaration: ModelDeclaration) -> None:
    """Refuse what Stage A cannot yet honour — by name, never by silence (`FRD-107` FR-2a).

    Two of these are fields the caller sent. The third is subtler and is the one the FRD singled
    out: the predecessor applies a model's **declared default thinking** when the caller sends
    none. A Stage A that sent no thinking at all would give a different answer for a reason nobody
    could see — the exact failure this surface exists to avoid. So a model whose declared default
    is anything other than *disabled* is not served here until `FRD-111` lands, and the refusal
    says so rather than quietly answering.

    A model with no thinking declaration, or one whose default is ``disabled``, is unaffected:
    sending nothing *is* what it asked for.
    """
    if request.thinking is not None:
        raise errors.KiraError(
            422,
            errors.NOT_YET_SUPPORTED,
            "'thinking' is not yet available on this gateway. It is refused rather than ignored, "
            "because an answer computed without it would differ for a reason you could not see.",
        )
    if request.response_schema is not None:
        raise errors.KiraError(
            422,
            errors.NOT_YET_SUPPORTED,
            "'responseSchema' is not yet available on this gateway. It is refused rather than "
            "ignored, because the response would not be the shape you asked for.",
        )

    default = (declaration.thinking or {}).get("default")
    mode = default.get("mode") if isinstance(default, dict) else None
    if mode is not None and mode != "disabled":
        raise errors.KiraError(
            422,
            errors.NOT_YET_SUPPORTED,
            f"Model '{declaration.name}' declares a default thinking mode of '{mode}', which this "
            "gateway cannot yet apply. Serving it would answer with no thinking at all and look "
            "identical to a correct answer.",
        )


def to_canonical(
    request: schemas.ChatRequest, model: str, limits: Limits | None = None
) -> CanonicalRequest:
    """Map a KIRA chat request onto the canonical one.

    History arrives oldest-first (`kira_api.md` §2.1) and is placed before the current turn, which
    is the order every provider expects — reversing it would produce a coherent conversation about
    the wrong thing.
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
    """The predecessor's terminal SSE event (`kira_api.md` §2.2)."""
    return {"status": "completed", "data": to_chat_response(response).model_dump()}


def update_event(message: str) -> dict[str, Any]:
    return {"status": "update", "data": message}
