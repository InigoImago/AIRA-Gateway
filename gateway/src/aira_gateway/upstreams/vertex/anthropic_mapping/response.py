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
    FinishReason,
    ToolCallPart,
)
from aira_gateway.upstreams.base import UpstreamError, stream_failure

#: The finish reason for "the model answered, but not with the document asked for". A value rather
#: than an error, so it takes the path every abnormal finish takes and is refused in one place
#: (`FRD-112` FR-6).
SCHEMA_UNSATISFIED = FinishReason.SCHEMA_UNSATISFIED

#: Content-block types read for the answer. `thinking` is deliberately **not** one of them.
_ANSWER_BLOCKS = frozenset({"text"})

#: The block type this vendor puts its chain of thought in.
_THINKING_BLOCK = "thinking"

_STOP_REASONS = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "max_tokens": FinishReason.MAX_TOKENS,
    # The context window ran out before the output cap did: truncated all the same.
    "model_context_window_exceeded": FinishReason.MAX_TOKENS,
    "tool_use": FinishReason.TOOL_USE,
    "refusal": FinishReason.REFUSAL,
}

#: Finishes that already say why an answer is not the document asked for.
_SAYS_WHY_THERE_IS_NO_DOCUMENT = frozenset({FinishReason.MAX_TOKENS, FinishReason.REFUSAL})

#: The usage fields this API reports, each a **running total** in every event that carries it
#: (`message_start`, then `message_delta`) — the vendor's SDK assigns them, it does not add them.
_USAGE_FIELDS = (
    "input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
)

#: Breakdowns nested in the usage object, held whole like the counts above.
_USAGE_BLOCKS = ("cache_creation", "output_tokens_details")

#: The hour's share of `cache_creation_input_tokens`.
_LONG_WRITES = "ephemeral_1h_input_tokens"

#: Of `output_tokens`, those spent thinking — counted whether or not the text is shown.
_THINKING_TOKENS = "thinking_tokens"


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
    cached = _count(usage.get("cache_read_input_tokens"))
    created = _count(usage.get("cache_creation_input_tokens"))
    return CanonicalUsage(
        prompt_tokens=_count(usage.get("input_tokens")) + cached + created,
        cached_input_tokens=cached,
        cache_write_tokens=created,
        # The hour's writes cost more than the five minutes' (`pricing`), and only this breakdown
        # tells them apart.
        long_cache_write_tokens=_count(_nested(usage, "cache_creation", _LONG_WRITES)),
        completion_tokens=_count(usage.get("output_tokens")),
        reasoning_tokens=_count(_nested(usage, "output_tokens_details", _THINKING_TOKENS)),
    )


def _nested(usage: dict[str, Any], block: str, field: str) -> Any:
    inner = usage.get(block)
    return inner.get(field) if isinstance(inner, dict) else None


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def finish_reason(stop_reason: Any) -> FinishReason:
    """Mapped so `FRD-112` FR-6 can tell a complete document from a truncated one.

    An absent reason is a normal end; one this table does not know is `OTHER`, never `STOP`.
    """
    if stop_reason is None:
        return FinishReason.STOP
    return _STOP_REASONS.get(str(stop_reason), FinishReason.OTHER)


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
            # An abnormal finish rather than text, so the surface refuses it — under the reason the
            # model gave where it gave one: a truncated or refused answer is not a document the
            # model failed to shape, and the caller's remedy differs.
            return CanonicalResponse(
                model=model,
                text="",
                reasoning=reasoning,
                finish_reason=(
                    reason if reason in _SAYS_WHY_THERE_IS_NO_DOCUMENT else SCHEMA_UNSATISFIED
                ),
                usage=usage_of(data.get("usage")),
            )
        text = document
        # A `tool_use` stop beside a complete document is reported as `stop`, so FR-6's check
        # does not refuse the document.
        reason = FinishReason.STOP if reason is FinishReason.TOOL_USE else reason

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

    **Usage is a running total, held whole.** ``message_start`` carries the input counts — cache
    reads and writes included — and ``message_delta`` the output count, repeating whichever input
    counts the API version sends; each field an event carries is the total so far and *replaces*
    the one held. Adding them would count the first output token twice and, where the input is
    repeated, every input token; keeping only the plain counts would price a cached prefix as
    uncached input. The total so far rides every chunk, so a stream that breaks off still says what
    it had cost.

    A tool call's arguments arrive as `input_json_delta` fragments and are accumulated, never
    emitted as text, until the call is whole (`FRD-131` FR-6). An `error` event is the upstream
    failing mid-stream and is raised (`stream_failure`), never read as the end of the answer.
    """

    def __init__(self, *, include_reasoning: bool = False) -> None:
        #: The use case's switch (`FRD-135` FR-3), off unless passed, as on the buffered mapper.
        self._include_reasoning = include_reasoning
        self._usage: dict[str, Any] = {}
        self._finish: FinishReason | None = None
        #: The open `tool_use` block, if any: its id, its name, and the argument fragments so far.
        self._open_call: dict[str, str] | None = None
        self._calls: list[ToolCallPart] = []

    def feed(self, event: dict[str, Any]) -> CanonicalChunk | None:
        kind = event.get("type")

        if kind == "error":
            raise stream_failure(event.get("error"))

        if kind == "message_start":
            self._update_usage((event.get("message") or {}).get("usage"))
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
            if delta.get("type") == "thinking_delta":
                thought = str(delta.get("thinking") or "") if self._include_reasoning else ""
                if not thought:
                    return None
                return CanonicalChunk(
                    text_delta="",
                    reasoning_delta=thought,
                    usage=self._running() if self._usage else None,
                )
            if delta.get("type") == "input_json_delta" and self._open_call is not None:
                self._open_call["arguments"] += str(delta.get("partial_json") or "")
                return None
            if delta.get("type") == "text_delta":
                text = str(delta.get("text") or "")
                if not text:
                    return None
                # Nothing reported yet is unknown, not zero: a zero would settle as free.
                return CanonicalChunk(
                    text_delta=text, usage=self._running() if self._usage else None
                )
            return None

        if kind == "message_delta":
            self._update_usage(event.get("usage"))
            self._finish = finish_reason((event.get("delta") or {}).get("stop_reason"))
            return None

        if kind == "message_stop":
            # A call still open is finished first: a message ending without a closing event would
            # otherwise drop it.
            self._finish_call()
            return CanonicalChunk(
                text_delta="",
                finish_reason=self._finish or FinishReason.STOP,
                usage=self._running(),
                # Whole, on the chunk that ends the message.
                tool_calls=tuple(self._calls),
            )
        return None

    def _update_usage(self, reported: Any) -> None:
        """Take each count the event carries: it is the total so far, never an increment."""
        if not isinstance(reported, dict):
            return
        for field in _USAGE_FIELDS:
            value = reported.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                self._usage[field] = value
        for block in _USAGE_BLOCKS:
            if isinstance(reported.get(block), dict):
                self._usage[block] = reported[block]

    def _running(self) -> CanonicalUsage:
        """The usage so far, read exactly as a finished answer's is (`usage_of`)."""
        return usage_of(self._usage)

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


class MessageAssembler:
    """Anthropic's typed SSE events → the message a non-streamed call answers with.

    For a request the caller did not stream, sent as a stream all the same (`FRD-119`): a long
    answer to a large context takes longer than any time limit a request/response can be given,
    while a stream carries something — its events, the provider's pings — the whole time, so this
    gateway's time limit measures a silence rather than the whole answer. Anthropic's own SDKs
    refuse a non-streamed request they expect to take ten minutes for the same reason.

    The events are put together as those SDKs put them together (`get_final_message`): each block
    from its start, its deltas appended, a function call's arguments parsed when the block stops,
    the usage replaced field by field as the running totals arrive. The message is then read by
    :func:`anthropic_to_canonical`, exactly as a non-streamed answer is — one reading of an
    answer, not two. A stream that ends without `message_stop` was cut off, and is the
    provider's failure rather than a shorter answer.
    """

    def __init__(self) -> None:
        self._message: dict[str, Any] | None = None
        self._blocks: dict[int, dict[str, Any]] = {}
        #: A function call's argument fragments, by block, until the block stops.
        self._arguments: dict[int, str] = {}
        self._finished = False

    def feed(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "error":
            raise stream_failure(event.get("error"))
        if kind == "message_start":
            message = event.get("message")
            self._message = dict(message) if isinstance(message, dict) else {}
            usage = self._message.get("usage")
            self._message["usage"] = dict(usage) if isinstance(usage, dict) else {}
            return
        index = event.get("index")
        if kind == "content_block_start" and isinstance(index, int):
            block = event.get("content_block")
            self._blocks[index] = dict(block) if isinstance(block, dict) else {}
        elif kind == "content_block_delta" and isinstance(index, int):
            self._delta(index, event.get("delta") or {})
        elif kind == "content_block_stop" and isinstance(index, int):
            self._close(index)
        elif kind == "message_delta" and self._message is not None:
            delta = event.get("delta") or {}
            for field in ("stop_reason", "stop_sequence"):
                if field in delta:
                    self._message[field] = delta[field]
            usage = event.get("usage")
            if isinstance(usage, dict):
                # Running totals, each the figure so far: replaced, never added (`StreamAssembler`).
                self._message["usage"].update(usage)
        elif kind == "message_stop":
            self._finished = True

    def message(self) -> dict[str, Any]:
        """The whole message — or the provider's failure, for a stream it cut off."""
        if self._message is None or not self._finished:
            raise UpstreamError("The model provider ended the answer before it was complete.", 502)
        return {**self._message, "content": [self._blocks[i] for i in sorted(self._blocks)]}

    def _delta(self, index: int, delta: dict[str, Any]) -> None:
        block = self._blocks.setdefault(index, {})
        kind = delta.get("type")
        if kind == "text_delta":
            block["text"] = str(block.get("text") or "") + str(delta.get("text") or "")
        elif kind == "thinking_delta":
            block["thinking"] = str(block.get("thinking") or "") + str(delta.get("thinking") or "")
        elif kind == "signature_delta":
            block["signature"] = str(delta.get("signature") or "")
        elif kind == "input_json_delta":
            self._arguments[index] = self._arguments.get(index, "") + str(
                delta.get("partial_json") or ""
            )

    def _close(self, index: int) -> None:
        if index not in self._arguments:
            return
        fragments = self._arguments.pop(index)
        try:
            arguments = json.loads(fragments) if fragments else {}
        except ValueError:
            # The name is kept, so the caller sees that the model asked (`StreamAssembler`).
            arguments = {}
        block = self._blocks.setdefault(index, {})
        block["input"] = arguments if isinstance(arguments, dict) else {}
