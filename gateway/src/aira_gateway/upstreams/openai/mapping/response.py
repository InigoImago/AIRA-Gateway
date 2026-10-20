"""The OpenAI wire format → canonical: answers, stream chunks, streamed tool calls, vectors.

Reasoning is returned only where the use case asked for it (`FRD-135` FR-3). A reasoning model on
this dialect **thinks whether or not it was asked** and always sends its thoughts, so the answer is
read from `content` alone and `REASONING_FIELDS` from exactly one place, under the use case's
switch.
"""

from __future__ import annotations

import json
from typing import Any

from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalResponse,
    CanonicalUsage,
    EmbeddingVectors,
    FinishReason,
    ToolCallPart,
)
from aira_gateway.upstreams.base import UpstreamError, stream_failure

_FINISH_REASONS = {
    "stop": FinishReason.STOP,
    "length": FinishReason.MAX_TOKENS,
    "content_filter": FinishReason.REFUSAL,
    "tool_calls": FinishReason.TOOL_USE,
    # The deprecated single-call spelling some compatible servers still send.
    "function_call": FinishReason.TOOL_USE,
}

#: The fields a reasoning model returns its chain of thought in, in the order they are read: the
#: dialect has two spellings — `reasoning_content` is DeepSeek's, and what Agent Platform's open
#: models send. Read only by `reasoning_of`.
REASONING_FIELDS = ("reasoning", "reasoning_content")

#: The fields that make a `usage` block a **report**. Agent Platform puts a `usage` holding only
#: its traffic type on every chunk of a stream, and read as a report it is zero tokens — the
#: running total of a stream whose caller left before the last chunk.
USAGE_COUNT_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")


def reports_usage(payload: Any) -> bool:
    """Whether a `usage` block says what was used, rather than only that there is a block."""
    return isinstance(payload, dict) and any(field in payload for field in USAGE_COUNT_FIELDS)


def _usage_of(payload: Any) -> CanonicalUsage:
    usage = payload if isinstance(payload, dict) else {}
    # Prefix caching is automatic on this dialect and this field is its only trace (`FRD-133`
    # §4a). A self-hosted runtime reports none, and zero is the honest answer for it.
    details = usage.get("prompt_tokens_details")
    cached = int((details or {}).get("cached_tokens", 0) or 0) if isinstance(details, dict) else 0
    # Already inside `completion_tokens` here, unlike Gemini's: read for visibility, never added
    # (`FRD-135` FR-1).
    output_details = usage.get("completion_tokens_details")
    reasoning = (
        int((output_details or {}).get("reasoning_tokens", 0) or 0)
        if isinstance(output_details, dict)
        else 0
    )
    return CanonicalUsage(
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        reasoning_tokens=reasoning,
        # No cache-write figure exists on this dialect; only Anthropic separates the two.
        cached_input_tokens=cached,
    )


def finish_reason(value: Any) -> FinishReason:
    """This dialect's word for how a choice ended. Absent is a normal end — several compatible
    servers send none — and a word this table does not know is `OTHER`, never `STOP`."""
    if value is None:
        return FinishReason.STOP
    return _FINISH_REASONS.get(str(value), FinishReason.OTHER)


def reasoning_of(message: dict[str, Any], *, wanted: bool) -> str:
    """The chain of thought, when — and only when — the use case asked for it.

    The provider sends the field regardless, so reading it unconditionally would return reasoning
    into a response the gateway persists, for every use case that never asked (`FRD-135` §8).
    """
    thoughts = next((message[field] for field in REASONING_FIELDS if message.get(field)), "")
    return str(thoughts) if wanted else ""


def answer_of(message: dict[str, Any]) -> str:
    """The answer, and **only** the answer.

    Empty when the model spent its whole allowance thinking; the finish reason (`length`, mapped to
    `max_tokens`) says why. The reasoning is never substituted for it (`FRD-111`).
    """
    return str(message.get("content") or "")


def tool_calls_of(raw: Any) -> tuple[ToolCallPart, ...]:
    """This dialect's tool calls → canonical, with the arguments parsed.

    Arguments arrive as a **JSON string**. One that does not parse becomes empty arguments with the
    name intact: the caller sees *that* the model asked, where a 502 would hide the model's mistake
    behind ours.
    """
    calls: list[ToolCallPart] = []
    for index, entry in enumerate(raw or ()):
        function = entry.get("function") or {}
        name = str(function.get("name") or "")
        if not name:
            continue
        raw_arguments = function.get("arguments")
        try:
            parsed = json.loads(raw_arguments) if raw_arguments else {}
        except TypeError, ValueError:
            parsed = {}
        calls.append(
            ToolCallPart(
                id=str(entry.get("id") or f"{name}-{index}"),
                name=name,
                arguments=parsed if isinstance(parsed, dict) else {},
            )
        )
    return tuple(calls)


def openai_to_canonical(
    data: dict[str, Any], model: str, *, include_reasoning: bool = False
) -> CanonicalResponse:
    """This dialect's answer → canonical.

    ``include_reasoning`` carries the use case's switch (`FRD-135` FR-3). The argument defaults to
    **off**, so a call site that forgets it withholds rather than discloses. Reasoning tokens are
    counted either way: a use case pays for thinking whether or not it sees it.

    An answer without a choice is no answer: the upstream's fault, never an empty one that
    finished normally — which a structured request would take for a document.
    """
    choices = data.get("choices") or []
    if not choices:
        raise UpstreamError("The upstream answered with no choice.", 502)
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") or {}
    return CanonicalResponse(
        model=model,
        text=answer_of(message),
        reasoning=reasoning_of(message, wanted=include_reasoning),
        finish_reason=finish_reason(first.get("finish_reason")),
        usage=_usage_of(data.get("usage")),
        tool_calls=tool_calls_of(message.get("tool_calls")),
    )


def openai_chunk_to_canonical(
    data: dict[str, Any], *, include_reasoning: bool = False
) -> CanonicalChunk | None:
    """One SSE payload → a canonical chunk, or ``None`` for one that carries nothing.

    The final chunk has an empty ``choices`` array and only ``usage``; indexing ``choices[0]``
    unconditionally would lose the token counts of every streamed request. An `error` payload is
    the upstream failing mid-stream and is raised (`stream_failure`).
    """
    if data.get("error") is not None:
        raise stream_failure(data["error"])
    choices = data.get("choices") or []
    usage = _usage_of(data["usage"]) if reports_usage(data.get("usage")) else None

    if not choices:
        return CanonicalChunk(text_delta="", finish_reason=None, usage=usage) if usage else None

    first = choices[0]
    delta = first.get("delta") or {}
    reason = first.get("finish_reason")
    return CanonicalChunk(
        text_delta=str(delta.get("content") or ""),
        # Sent whether or not it was asked for, so read only where the use case asked for it.
        reasoning_delta=reasoning_of(delta, wanted=include_reasoning),
        finish_reason=finish_reason(reason) if reason else None,
        usage=usage,
    )


class StreamedToolCalls:
    """Reassembles tool calls that arrive **in pieces** (`FRD-131` FR-6).

    The first delta of a call carries an index, an id and the name; the arguments follow as string
    fragments across later deltas. Fragments are accumulated by **index**, the only key on every
    delta, and the finished calls are emitted on the chunk that ends the message. A call that never
    received a name is dropped rather than guessed at.
    """

    def __init__(self) -> None:
        self._by_index: dict[int, dict[str, str]] = {}

    def add(self, deltas: Any) -> None:
        for delta in deltas or ():
            index = int(delta.get("index", 0))
            entry = self._by_index.setdefault(index, {"id": "", "name": "", "arguments": ""})
            if delta.get("id"):
                entry["id"] = str(delta["id"])
            function = delta.get("function") or {}
            if function.get("name"):
                entry["name"] = str(function["name"])
            if function.get("arguments"):
                entry["arguments"] += str(function["arguments"])

    @property
    def pending(self) -> bool:
        return bool(self._by_index)

    def finish(self) -> tuple[ToolCallPart, ...]:
        """The completed calls, in the order the provider indexed them."""
        raw = [
            {
                "id": entry["id"],
                "function": {"name": entry["name"], "arguments": entry["arguments"]},
            }
            for _, entry in sorted(self._by_index.items())
            if entry["name"]
        ]
        self._by_index.clear()
        return tool_calls_of(raw)


class StreamAssembler:
    """One stream's payloads → canonical chunks, with its tool calls assembled across them.

    Stateful, so one per stream — and one per *attempt*, where a stream may be opened in a second
    place: half a tool call from the first must never be finished by the second.
    """

    def __init__(self, *, include_reasoning: bool = False) -> None:
        self._calls = StreamedToolCalls()
        #: The use case's switch (`FRD-135` FR-3), off unless passed: this dialect streams its
        #: reasoning whether or not it was asked for.
        self._include_reasoning = include_reasoning

    def feed(self, payload: dict[str, Any]) -> CanonicalChunk | None:
        choices = payload.get("choices") or []
        if choices and isinstance(choices[0], dict):
            self._calls.add((choices[0].get("delta") or {}).get("tool_calls"))
        chunk = openai_chunk_to_canonical(payload, include_reasoning=self._include_reasoning)
        if chunk is None:
            return None
        if chunk.finish_reason is not None and self._calls.pending:
            # Emitted whole, on the chunk that ends the message — never in pieces.
            chunk = chunk.model_copy(update={"tool_calls": self._calls.finish()})
        return chunk


def embedding_values(data: dict[str, Any]) -> EmbeddingVectors:
    """Vectors in the order submitted — the contract `FRD-113` FR-1 makes — **and what they cost**.

    Sorted by ``index`` rather than trusted: the field exists because the API promises no order.

    The usage travels with them (`FRD-403`). This returned a plain list and dropped
    ``data["usage"]``, so every embedding call through this dialect was recorded with no tokens and
    therefore **no cost** — unpriceable traffic on a model whose price is on file. Measured against
    the local runtime, which reports `prompt_tokens` for exactly these calls and had them discarded
    one layer above. `None` where the upstream said nothing: not reported is not zero.
    """
    entries = [entry for entry in (data.get("data") or []) if isinstance(entry, dict)]
    entries.sort(key=lambda entry: int(entry.get("index", 0)))
    usage = data.get("usage")
    reported = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    return EmbeddingVectors(
        ([float(value) for value in entry.get("embedding") or []] for entry in entries),
        input_tokens=None if reported is None else int(reported),
    )
