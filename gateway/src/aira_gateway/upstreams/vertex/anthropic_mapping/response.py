"""Anthropic Messages answers and streamed events → canonical.

Chain of thought arrives as `thinking` blocks whenever a budget was set, and is returned only where
the use case asked for it (`FRD-135` FR-3): otherwise it would land in a response the gateway also
persists.
"""

from __future__ import annotations

import json
from typing import Any

from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalResponse,
    CanonicalUsage,
    ToolCallPart,
)

#: The finish reason for "the model answered, but not with the document asked for". A value rather
#: than an error, so it takes the path every abnormal finish takes and is refused in one place
#: (`FRD-112` FR-6).
SCHEMA_UNSATISFIED = "schema_unsatisfied"

#: Content-block types read for the answer. `thinking` is deliberately **not** one of them.
_ANSWER_BLOCKS = frozenset({"text"})

#: The block type this vendor puts its chain of thought in.
_THINKING_BLOCK = "thinking"

_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "max_tokens",
    "tool_use": "tool_use",
    "refusal": "refusal",
}


def answer_text(content: Any) -> str:
    """The answer, with every non-answer block dropped (§5.4)."""
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") in _ANSWER_BLOCKS
    )


def reasoning_text(content: Any, *, wanted: bool) -> str:
    """What the model thought, where it was asked for and returned.

    Two independent conditions: the vendor sends `thinking` blocks only when a budget was set, and
    the use case must have turned reasoning on.
    """
    if not wanted or not isinstance(content, list):
        return ""
    return "".join(
        str(block.get(_THINKING_BLOCK, ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == _THINKING_BLOCK
    )


def usage_of(payload: Any) -> CanonicalUsage:
    """Token usage, with cache reads and writes folded into the input count (`FRD-133`).

    `input_tokens` counts only what fell after the last cache breakpoint, so the three together are
    everything the request was charged input for; the parts ride along beside the total.
    """
    usage = payload if isinstance(payload, dict) else {}
    cached = int(usage.get("cache_read_input_tokens", 0) or 0)
    created = int(usage.get("cache_creation_input_tokens", 0) or 0)
    return CanonicalUsage(
        prompt_tokens=int(usage.get("input_tokens", 0) or 0) + cached + created,
        cached_input_tokens=cached,
        cache_write_tokens=created,
        completion_tokens=int(usage.get("output_tokens", 0) or 0),
    )


def finish_reason(stop_reason: Any) -> str:
    """Mapped so `FRD-112` FR-6 can tell a complete document from a truncated one."""
    return _STOP_REASONS.get(str(stop_reason), "stop")


def structured_document(content: Any) -> str | None:
    """The requested document: the answer text, **if it parses as JSON**.

    Prose and a document arrive through the same text block, and returning prose as the document is
    what `FRD-112` FR-6 exists to prevent. The provider's JSON guarantee is checked, not trusted.
    """
    text = answer_text(content).strip()
    if not text:
        return None
    try:
        json.loads(text)
    except ValueError:
        return None
    return text


def tool_calls_of(content: Any) -> tuple[ToolCallPart, ...]:
    """The caller's tool calls in a response: every `tool_use` block, in order."""
    if not isinstance(content, list):
        return ()
    calls: list[ToolCallPart] = []
    for index, block in enumerate(content):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = str(block.get("name") or "")
        if not name:
            continue
        arguments = block.get("input")
        calls.append(
            ToolCallPart(
                id=str(block.get("id") or f"{name}-{index}"),
                name=name,
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)


def anthropic_to_canonical(
    data: dict[str, Any], model: str, *, structured: bool = False, include_reasoning: bool = False
) -> CanonicalResponse:
    """This vendor's answer → canonical.

    ``include_reasoning`` carries the use case's switch (`FRD-135` FR-3). The argument defaults to
    **off**, so a call site that forgets it withholds rather than discloses.
    """
    text = answer_text(data.get("content"))
    reasoning = reasoning_text(data.get("content"), wanted=include_reasoning)
    reason = finish_reason(data.get("stop_reason"))

    if structured:
        document = structured_document(data.get("content"))
        if document is None:
            calls = tool_calls_of(data.get("content"))
            if calls:
                # With a schema and tools, the model may call a function instead of answering: a
                # normal agent turn, not an unsatisfied schema.
                return CanonicalResponse(
                    model=model,
                    text="",
                    reasoning=reasoning,
                    finish_reason=reason,
                    usage=usage_of(data.get("usage")),
                    tool_calls=calls,
                )
            # An abnormal finish rather than text, so the surface refuses it.
            return CanonicalResponse(
                model=model,
                text="",
                reasoning=reasoning,
                finish_reason=SCHEMA_UNSATISFIED,
                usage=usage_of(data.get("usage")),
            )
        text = document
        # A `tool_use` stop beside a complete document is reported as `stop`, so FR-6's check
        # does not refuse the document.
        reason = "stop" if reason == "tool_use" else reason

    return CanonicalResponse(
        model=model,
        text=text,
        reasoning=reasoning,
        finish_reason=reason,
        usage=usage_of(data.get("usage")),
        tool_calls=tool_calls_of(data.get("content")),
    )


class StreamAssembler:
    """Turns Anthropic's typed SSE events into canonical chunks.

    Usage arrives in **two** events — ``message_start`` carries the input count, ``message_delta``
    the output — so the counts are accumulated; last-event-wins would report zero input tokens for
    every streamed request. A tool call's arguments arrive as `input_json_delta` fragments and are
    accumulated, never emitted as text, until the call is whole (`FRD-131` FR-6).
    """

    def __init__(self) -> None:
        self._prompt = 0
        self._completion = 0
        self._finish: str | None = None
        #: The open `tool_use` block, if any: its id, its name, and the argument fragments so far.
        self._open_call: dict[str, str] | None = None
        self._calls: list[ToolCallPart] = []

    def feed(self, event: dict[str, Any]) -> CanonicalChunk | None:
        kind = event.get("type")

        if kind == "message_start":
            usage = usage_of((event.get("message") or {}).get("usage"))
            self._prompt = usage.prompt_tokens
            self._completion = usage.completion_tokens
            return None

        if kind == "content_block_start":
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                self._open_call = {
                    "id": str(block.get("id") or ""),
                    "name": str(block.get("name") or ""),
                    "arguments": "",
                }
            return None

        if kind == "content_block_stop":
            self._finish_call()
            return None

        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            # `thinking_delta` is discarded, as `answer_text` drops the block.
            if delta.get("type") == "input_json_delta" and self._open_call is not None:
                self._open_call["arguments"] += str(delta.get("partial_json") or "")
                return None
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
            # A call still open is finished first: a message ending without a closing event would
            # otherwise drop it.
            self._finish_call()
            return CanonicalChunk(
                text_delta="",
                finish_reason=self._finish or "stop",
                usage=CanonicalUsage(
                    prompt_tokens=self._prompt, completion_tokens=self._completion
                ),
                # Whole, on the chunk that ends the message.
                tool_calls=tuple(self._calls),
            )
        return None

    def _finish_call(self) -> None:
        """Close the open `tool_use` block, if the fragments amount to a usable call."""
        open_call = self._open_call
        self._open_call = None
        if open_call is None or not open_call["name"]:
            return
        try:
            arguments = json.loads(open_call["arguments"]) if open_call["arguments"] else {}
        except TypeError, ValueError:
            # The name is kept, so the caller sees that the model asked; a dropped call would hide
            # the model's mistake behind ours.
            arguments = {}
        self._calls.append(
            ToolCallPart(
                id=open_call["id"] or open_call["name"],
                name=open_call["name"],
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
