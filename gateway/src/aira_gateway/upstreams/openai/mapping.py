"""Canonical ⇄ the OpenAI wire format (FRD-123, and FRD-120 later).

The third dialect, and the one with the widest reach: Azure OpenAI speaks it, Model Garden's
self-deploy side serves it, Ollama exposes it, and `FRD-106`'s deferred *surface* becomes cheap
once it exists. Built here for a local model because that is the honest way to get a real one into
the stack, and it pays for Foundry at the same time.

Pure functions, no I/O — the same shape as ``gemini_mapping.py`` and ``vertex/anthropic_mapping``,
deliberately, because the symmetry is what makes the fourth dialect a copy of a known pattern.

Every difference from the other two is a mapping this file owns:

    roles          system/user/model     → system/user/assistant (a flat list, no split-out system)
    output cap     maxOutputTokens       → max_tokens (optional, unlike Anthropic's)
    attachments    inlineData            → an image_url part carrying a data: URI, **images only**
    usage          usageMetadata.*       → usage.prompt_tokens / completion_tokens
    schema         responseSchema        → response_format.json_schema, with a required name
    thinking       a token budget        → **reasoning_effort, an abstract level with no budget**
    stream         one final chunk       → a `[DONE]` sentinel and usage only if asked for
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
    Role,
    TextPart,
    Thinking,
)
from aira_gateway.core.schema import ResponseSchema, to_json_schema

_ROLE = {Role.SYSTEM: "system", Role.USER: "user", Role.MODEL: "assistant"}

_FINISH_REASONS = {
    "stop": "stop",
    "length": "max_tokens",
    "content_filter": "refusal",
    "tool_calls": "tool_use",
}

#: The name the schema is registered under. OpenAI requires one; it is never shown to the caller.
SCHEMA_NAME = "aira_response"

#: `mode` → `reasoning_effort`. The vendor takes an **abstract level and no token budget at all**,
#: which is why `FRD-111` §5.2 said `limited` has no equivalent here and must be refused by
#: capability rather than approximated: silently rounding a caller's 20 000-token budget to "high"
#: would spend a different amount of money than they asked for, and nothing would say so.
_REASONING_EFFORT = {
    ThinkingMode.MINIMAL: "minimal",
    ThinkingMode.LOW: "low",
    ThinkingMode.MEDIUM: "medium",
    ThinkingMode.HIGH: "high",
    ThinkingMode.AUTO: "medium",
}


class DialectUnsupported(Exception):
    """The request asks for something this wire format cannot express faithfully.

    Raised at mapping time rather than dropped. It should be unreachable in practice — a model that
    cannot do a thing does not declare the capability, and `FRD-114` refuses the request before
    dispatch — so reaching it means a catalog entry claims something its dialect cannot deliver,
    which is exactly the state that must not fail quietly.
    """


def _content(message: CanonicalMessage) -> str | list[dict[str, Any]]:
    """Canonical parts → OpenAI content.

    A text-only message stays a plain string: it is the overwhelmingly common case, several
    implementations of this API are fussier about the array form, and a wire body full of
    single-element arrays is harder to read in a log for no gain.
    """
    if not message.attachments:
        return message.text

    parts: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            parts.append({"type": "text", "text": part.text})
            continue
        if not part.media_type.startswith("image/"):
            # `ADR-0012`'s central case: GPT-shaped models read images and **not** documents. The
            # chain is supposed to have skipped this candidate already (`MediaTypesSupported`), so
            # arriving here means a catalog declared a PDF-reading capability this dialect cannot
            # deliver. Failing is the only correct answer — sending the prompt without the document
            # produces a confident wrong answer with a 200 on it.
            raise DialectUnsupported(
                f"This dialect carries images, not '{part.media_type}'. A model declaring it "
                "cannot serve the request through an OpenAI-compatible endpoint."
            )
        encoded = base64.b64encode(part.data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{part.media_type};base64,{encoded}"},
            }
        )
    return parts


def canonical_to_openai(request: CanonicalRequest, *, stream: bool = False) -> dict[str, Any]:
    """Build a `/v1/chat/completions` body.

    Unlike Anthropic there is no separate system parameter — the system prompt is simply the first
    message — which is the one place this dialect is *simpler* than the other two.
    """
    body: dict[str, Any] = {
        "model": request.model,
        "messages": [
            {"role": _ROLE[message.role], "content": _content(message)}
            for message in request.messages
        ],
    }
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        body["max_tokens"] = request.max_output_tokens
    if request.thinking is not None:
        effort = _reasoning_effort(request.thinking)
        if effort is not None:
            body["reasoning_effort"] = effort
    if request.response_schema is not None:
        body["response_format"] = _response_format(request.response_schema)
    if stream:
        body["stream"] = True
        # Usage is **not** reported on a streamed response unless it is asked for, and a stream
        # that reports none is released rather than settled (`FRD-405`) — so forgetting this would
        # make every streamed request free.
        body["stream_options"] = {"include_usage": True}
    return body


def _reasoning_effort(setting: Thinking) -> str | None:
    if setting.mode is ThinkingMode.DISABLED:
        # There is no "off" value; the absence of the parameter is off, as with Anthropic.
        return None
    if setting.mode is ThinkingMode.LIMITED:
        raise DialectUnsupported(
            "This dialect takes an effort level, not a token budget, so a 'limited' thinking "
            "budget cannot be honoured exactly. It is refused rather than rounded: rounding "
            "would spend a different amount than was asked for, and nothing about the answer "
            "would show it."
        )
    return _REASONING_EFFORT[setting.mode]


def _response_format(schema: ResponseSchema) -> dict[str, Any]:
    """The third mechanism behind one capability flag (`ADR-0011` rule 3).

    Gemini takes a schema parameter, a forced tool call is the second mechanism, and this is the
    third: a named ``json_schema`` with ``strict``. The JSON Schema translation lives in the
    canonical core rather than in whichever dialect needed it first — two copies drift in
    whichever one is not under test, and a dialect importing from another dialect is how "the
    canonical core is provider-agnostic" quietly stops being true.
    """
    return {
        "type": "json_schema",
        "json_schema": {"name": SCHEMA_NAME, "strict": True, "schema": to_json_schema(schema)},
    }


def _usage_of(payload: Any) -> CanonicalUsage:
    usage = payload if isinstance(payload, dict) else {}
    return CanonicalUsage(
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
    )


def finish_reason(value: Any) -> str:
    return _FINISH_REASONS.get(str(value), "stop")


def openai_to_canonical(data: dict[str, Any], model: str) -> CanonicalResponse:
    choices = data.get("choices") or []
    first = choices[0] if choices else {}
    message = first.get("message") or {}
    return CanonicalResponse(
        model=model,
        text=str(message.get("content") or ""),
        finish_reason=finish_reason(first.get("finish_reason")),
        usage=_usage_of(data.get("usage")),
    )


def openai_chunk_to_canonical(data: dict[str, Any]) -> CanonicalChunk | None:
    """One SSE payload → a canonical chunk, or ``None`` for one that carries nothing.

    The final chunk of a stream has an empty ``choices`` array and only ``usage``, so a mapper that
    indexed ``choices[0]`` unconditionally would lose the token counts of every streamed request —
    which is the same class of defect as Anthropic's two-event usage, arriving by a different door.
    """
    choices = data.get("choices") or []
    usage = _usage_of(data["usage"]) if data.get("usage") else None

    if not choices:
        return CanonicalChunk(text_delta="", finish_reason=None, usage=usage) if usage else None

    first = choices[0]
    delta = first.get("delta") or {}
    reason = first.get("finish_reason")
    return CanonicalChunk(
        text_delta=str(delta.get("content") or ""),
        finish_reason=finish_reason(reason) if reason else None,
        usage=usage,
    )


def canonical_to_openai_embedding(request: CanonicalEmbeddingRequest) -> dict[str, Any]:
    """`/v1/embeddings` takes the whole batch in one ``input`` array.

    There is no task type in this format at all. `FRD-113` refuses an undeclared one before we get
    here, which is what keeps "the model does not offer it" and "the wire format cannot say it"
    from becoming the same silent outcome.
    """
    body: dict[str, Any] = {"model": request.model, "input": list(request.texts)}
    if request.dimensions is not None:
        body["dimensions"] = request.dimensions
    return body


def embedding_values(data: dict[str, Any]) -> list[list[float]]:
    """Vectors in the order submitted.

    Sorted by ``index`` rather than trusted to arrive in order: the field exists precisely because
    the API does not promise one, and "the order submitted" is the contract `FRD-113` FR-1 makes.
    """
    entries = [entry for entry in (data.get("data") or []) if isinstance(entry, dict)]
    entries.sort(key=lambda entry: int(entry.get("index", 0)))
    return [[float(value) for value in entry.get("embedding") or []] for entry in entries]


def parse_sse_line(line: str) -> dict[str, Any] | None:
    """A `data:` line → its payload, or ``None`` for the `[DONE]` sentinel and for keep-alives."""
    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    parsed = json.loads(payload)
    return parsed if isinstance(parsed, dict) else None
