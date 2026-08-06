"""Canonical ⇄ Anthropic Messages API (FRD-119).

Pure functions, no I/O, tested without HTTP — the same shape as ``upstreams/gemini_mapping.py``,
deliberately, because the symmetry is what makes a third dialect a copy of a known pattern rather
than a new invention.

Every difference from Gemini is a mapping this file owns:

    roles          user/model            → user/assistant
    system prompt  a content             → a top-level parameter (concatenated when several)
    output cap     optional              → **required**
    usage          usageMetadata.*       → usage.input_tokens / output_tokens
    thinking       never requested       → **returned**, and dropped here (§5.4)
    stop reason    finishReason          → stop_reason, with `max_tokens` meaning truncation
"""

from __future__ import annotations

import base64
import json
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
    TextPart,
)
from aira_gateway.core.schema import to_json_schema

#: The finish reason for "the model answered, but not with the document that was asked for".
#: Its own value rather than an error, so it travels through the same path every other abnormal
#: finish takes and is refused in one place (`FRD-112` FR-6).
SCHEMA_UNSATISFIED = "schema_unsatisfied"

#: Vertex requires this in the body rather than as a header.
ANTHROPIC_VERSION = "vertex-2023-10-16"

#: Content-block types we read for the answer. `thinking` is deliberately **not** here: it is the
#: least reviewed text a model produces, it frequently restates the input, and it would land in a
#: response the gateway also persists. `FRD-111` §2 decided not to return chain-of-thought; with
#: Gemini that was free (we simply never ask), with Anthropic it is an active obligation.
_ANSWER_BLOCKS = frozenset({"text"})

_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "max_tokens",
    "tool_use": "tool_use",
    "refusal": "refusal",
}


def canonical_to_anthropic(request: CanonicalRequest, *, max_tokens: int) -> dict[str, Any]:
    """Build an Anthropic Messages body.

    ``max_tokens`` is a parameter rather than read off the request because it is **required** by
    the API and our canonical field is optional: the caller's value where they gave one, the
    model's declared default otherwise (`FRD-114` FR-2). Resolving it here would hide that the
    catalog is what makes the field always present.
    """
    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []

    for message in request.messages:
        if message.role is Role.SYSTEM:
            # Anthropic takes one system prompt and the canonical model permits several, so they
            # are concatenated rather than silently reduced to the last one.
            system_parts.append(message.text)
            continue
        role = "assistant" if message.role is Role.MODEL else "user"
        messages.append({"role": role, "content": _content_blocks(message)})

    body: dict[str, Any] = {
        "anthropic_version": ANTHROPIC_VERSION,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.thinking is not None and request.thinking.mode is not ThinkingMode.DISABLED:
        budget = request.thinking.tokens
        if budget is not None:
            # Anthropic draws thinking tokens from `max_tokens`, so a budget at or above it
            # describes a request that can never answer. `FRD-114`'s catalog validation refuses
            # that combination where it is authored; this is the backstop for a request whose cap
            # is lower than the declaration anticipated.
            if budget >= max_tokens:
                raise ValueError(
                    f"A thinking budget of {budget} does not fit inside a {max_tokens}-token "
                    "output allowance — the budget is drawn from it."
                )
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    if request.response_schema is not None:
        # Anthropic has **no schema parameter**. The equivalent is a forced tool call: one tool
        # whose input schema is the caller's, `tool_choice` pinned to it, and the model's tool
        # input read back as the document. One capability, three mechanisms (`ADR-0011` rule 3).
        body["tools"] = [
            {
                "name": STRUCTURED_TOOL,
                "description": "Return the answer as a document matching this schema.",
                "input_schema": to_json_schema(request.response_schema),
            }
        ]
        body["tool_choice"] = {"type": "tool", "name": STRUCTURED_TOOL}
    return body


#: The single tool a structured request pins the model to. Named rather than anonymous so the
#: response mapper can tell "the model used our tool" from "the model called something else".
STRUCTURED_TOOL = "aira_structured_output"

#: Anthropic distinguishes an image from a document, and the type is not interchangeable — an
#: image block carrying a PDF is refused by the API, not silently coerced.
_DOCUMENT_TYPES = frozenset({"application/pdf"})


def _content_blocks(message: CanonicalMessage) -> list[dict[str, Any]]:
    """Canonical parts → Anthropic content blocks, in order.

    Anthropic *is* a list of typed blocks, so the ordered-parts model maps onto it more directly
    than onto Gemini's — which is a small piece of evidence that the canonical shape is neutral
    rather than Google's renamed (`FRD-119` §5.2).
    """
    blocks: list[dict[str, Any]] = []
    for part in message.parts:
        if isinstance(part, TextPart):
            blocks.append({"type": "text", "text": part.text})
            continue
        kind = "document" if part.media_type in _DOCUMENT_TYPES else "image"
        blocks.append(
            {
                "type": kind,
                "source": {
                    "type": "base64",
                    "media_type": part.media_type,
                    "data": base64.b64encode(part.data).decode("ascii"),
                },
            }
        )
    return blocks


def answer_text(content: Any) -> str:
    """The answer, with every non-answer block dropped (§5.4).

    A mapper that concatenated *all* content blocks is the obvious implementation and the wrong
    one: with thinking enabled it would return the model's reasoning to the caller, into a response
    AIRA also persists, in a column redaction cannot process.
    """
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") in _ANSWER_BLOCKS
    )


def usage_of(payload: Any) -> CanonicalUsage:
    """Token usage. Cache tokens are folded into the input count where the provider reports them
    separately — they *were* input, and leaving them out would understate what the request cost.
    """
    usage = payload if isinstance(payload, dict) else {}
    cached = int(usage.get("cache_read_input_tokens", 0) or 0)
    created = int(usage.get("cache_creation_input_tokens", 0) or 0)
    return CanonicalUsage(
        prompt_tokens=int(usage.get("input_tokens", 0) or 0) + cached + created,
        completion_tokens=int(usage.get("output_tokens", 0) or 0),
    )


def finish_reason(stop_reason: Any) -> str:
    """Mapped so `FRD-112` FR-6 can tell a complete document from a truncated one."""
    return _STOP_REASONS.get(str(stop_reason), "stop")


def structured_document(content: Any) -> str | None:
    """The document the forced tool call carries, or ``None`` if the model did not call it.

    ``None`` is a real path with this mechanism rather than a defensive one: a model that answers
    with prose instead of calling the tool **has not satisfied the schema**, and returning its
    prose as though it were the document is exactly the failure `FRD-112` FR-6 is about.
    """
    if not isinstance(content, list):
        return None
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and (block.get("name") == STRUCTURED_TOOL)
        ):
            return json.dumps(block.get("input"), separators=(",", ":"))
    return None


def anthropic_to_canonical(
    data: dict[str, Any], model: str, *, structured: bool = False
) -> CanonicalResponse:
    text = answer_text(data.get("content"))
    reason = finish_reason(data.get("stop_reason"))

    if structured:
        document = structured_document(data.get("content"))
        if document is None:
            # Surfaced as an abnormal finish reason rather than as text, so the surface refuses it
            # instead of handing the caller prose that will fail to parse in their code.
            return CanonicalResponse(
                model=model,
                text="",
                finish_reason=SCHEMA_UNSATISFIED,
                usage=usage_of(data.get("usage")),
            )
        text = document
        # `tool_use` is the *success* stop reason for this mechanism; reporting it as-is would
        # make every structured answer look abnormal to FR-6's check.
        reason = "stop" if reason == "tool_use" else reason

    return CanonicalResponse(
        model=model, text=text, finish_reason=reason, usage=usage_of(data.get("usage"))
    )


class StreamAssembler:
    """Turns Anthropic's typed SSE events into canonical chunks.

    Usage arrives in **two** events — ``message_start`` carries the input count and
    ``message_delta`` the output count — where Gemini puts everything in the last chunk. A
    last-event-wins implementation would silently report zero input tokens for every streamed
    Anthropic request, so the counts are accumulated rather than replaced.
    """

    def __init__(self) -> None:
        self._prompt = 0
        self._completion = 0
        self._finish: str | None = None

    def feed(self, event: dict[str, Any]) -> CanonicalChunk | None:
        kind = event.get("type")

        if kind == "message_start":
            usage = usage_of((event.get("message") or {}).get("usage"))
            self._prompt = usage.prompt_tokens
            self._completion = usage.completion_tokens
            return None

        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            # `thinking_delta` is discarded here for the same reason `answer_text` drops the block.
            if delta.get("type") in ("text_delta", "input_json_delta"):
                text = str(delta.get("text") or delta.get("partial_json") or "")
                return CanonicalChunk(text_delta=text) if text else None
            return None

        if kind == "message_delta":
            usage = usage_of(event.get("usage"))
            self._prompt += usage.prompt_tokens
            self._completion += usage.completion_tokens
            self._finish = finish_reason((event.get("delta") or {}).get("stop_reason"))
            return None

        if kind == "message_stop":
            return CanonicalChunk(
                text_delta="",
                finish_reason=self._finish or "stop",
                usage=CanonicalUsage(
                    prompt_tokens=self._prompt, completion_tokens=self._completion
                ),
            )
        return None
