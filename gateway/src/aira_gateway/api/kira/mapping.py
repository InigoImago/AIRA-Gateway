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
from aira_gateway.thinking import mode_from

#: What the predecessor puts between two text parts of one message.
#:
#: It joins them and sends **one** string; this surface kept them as separate canonical parts,
#: which every adapter then renders its own way — Gemini as several parts, the OpenAI dialect as a
#: concatenation with nothing between. So `["Hallo", "Welt"]` became `HalloWelt` on one provider
#: and a two-part message on another, where the predecessor sends `"Hallo\nWelt"`.
#:
#: That is the expensive kind of incompatibility: no error anywhere, a 200, and an answer to a
#: subtly different prompt. Found by comparing against the documented contract, which
#: is the only place it *could* be found — no test of ours would call a missing newline a failure,
#: because both sides of such a test would have come from the same idea of what the prompt is.
TEXT_PART_SEPARATOR = "\n"


def _parts(content: schemas.RequestContent, limits: Limits, offset: int) -> list[CanonicalPart]:
    """The predecessor's parts, with its own joining rule applied (`FRD-107` FR-2).

    Text parts are merged into one, in order, separated by a newline — and only *runs* of them, so
    a message that interleaves text and attachments keeps its order rather than having all its
    prose pulled to the front. An attachment ends a run, exactly as it would if the predecessor
    had them.
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
            # `str(...)` stood here and converted anything — a null into the word "None", a dict
            # into a Python repr. The type is checked where the request is parsed
            # (`RequestContent`), because a surface parses and the layer decides; this stays a
            # plain read so there is one place that can refuse rather than two that can disagree.
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

    An unknown mode is refused with the predecessor's own code rather than with a validation
    error about an enum, because a migrating client's error handling switches on that string —
    and it is refused by :func:`aira_gateway.thinking.mode_from`, which both surfaces read, so the
    two cannot come to disagree about what `" High"` means.
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

    History arrives oldest-first, as the contract specifies, and is placed before the current
    turn, which is the order every provider expects — reversing it would produce a coherent
    conversation about the wrong thing.
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
    """The predecessor's terminal SSE event (the predecessor's contract)."""
    return {"status": "completed", "data": to_chat_response(response).model_dump()}


def update_event(message: str) -> dict[str, Any]:
    return {"status": "update", "data": message}


def to_embedding(request: schemas.EmbeddingRequest, model: str) -> CanonicalEmbeddingRequest:
    """The predecessor's embedding request onto the canonical one.

    The predecessor's default task type is **the route's** to pass, not this mapper's: it applies
    only where the model declares that type, so it is a decision the validator makes with the
    declaration in hand. Filling it in blindly here would refuse every embedding against a model
    nobody has declared task types for — the compatibility default failing as though the caller
    had asked for something impossible.

    Dimensionality is not a field here — the predecessor makes it part of the model's *identity*
    (two ids for one model, differing only in width). Each id is its own catalog row, and the row's
    declared default is what the validator applies.
    """
    # **A list is one embedding, not many.** `FRD-113` §11 recorded this as an open question with
    # two readings — a list yields one vector per text, or a list is combined into a single vector
    # — assumed the first, and asked for it to be confirmed against the running predecessor. It
    # was confirmed on 2026-08-12, from the predecessor's own source: it sends the texts as
    # several **parts of one** embedding call and answers the documented singular `vector`.
    #
    # So the assumption was wrong, and the consequence was the worst kind: a caller sending five
    # chunks received five vectors where the predecessor gives one — not an error, *different
    # data*, in the right shape for a different question.
    #
    # Joined with nothing between them, which is not a guess either: measured against
    # `gemini-embedding-001` the same day, a multi-part content's vector is cosine **1.000000** to
    # the parts concatenated with no separator, 0.9936 with a space, and **0.9489** to their mean.
    # The provider concatenates; it does not build a centroid, which is the plausible reading and
    # the wrong one. A caller who wants a centroid computes it from n separate calls — their
    # arithmetic to choose (`ADR-0013`), and the Gemini surface's `batchEmbedContents` is where it
    # is available.
    text = request.text if isinstance(request.text, str) else "".join(request.text)
    return CanonicalEmbeddingRequest(model=model, texts=[text], task_type=request.task_type)
