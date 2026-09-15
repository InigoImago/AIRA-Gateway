"""The OpenAI wire format → canonical: answers, stream chunks, streamed tool calls, vectors.

Reasoning is returned only where the use case asked for it (`FRD-135` FR-3). A reasoning model on
this dialect **thinks whether or not it was asked** and always sends its thoughts, so the answer is
read from `content` alone and `REASONING_FIELD` from exactly one place, under the use case's switch.
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

_FINISH_REASONS = {
    "stop": "stop",
    "length": "max_tokens",
    "content_filter": "refusal",
    "tool_calls": "tool_use",
}

#: The field a reasoning model returns its chain of thought in. Read only by `reasoning_of`.
REASONING_FIELD = "reasoning"


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


def finish_reason(value: Any) -> str:
    return _FINISH_REASONS.get(str(value), "stop")


def reasoning_of(message: dict[str, Any], *, wanted: bool) -> str:
    """The chain of thought, when — and only when — the use case asked for it.

    The provider sends the field regardless, so reading it unconditionally would return reasoning
    into a response the gateway persists, for every use case that never asked (`FRD-135` §8).
    """
    return str(message.get(REASONING_FIELD) or "") if wanted else ""


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
    """
    choices = data.get("choices") or []
    first = choices[0] if choices else {}
    message = first.get("message") or {}
    return CanonicalResponse(
        model=model,
        text=answer_of(message),
        reasoning=reasoning_of(message, wanted=include_reasoning),
        finish_reason=finish_reason(first.get("finish_reason")),
        usage=_usage_of(data.get("usage")),
        tool_calls=tool_calls_of(message.get("tool_calls")),
    )


def openai_chunk_to_canonical(data: dict[str, Any]) -> CanonicalChunk | None:
    """One SSE payload → a canonical chunk, or ``None`` for one that carries nothing.

    The final chunk has an empty ``choices`` array and only ``usage``; indexing ``choices[0]``
    unconditionally would lose the token counts of every streamed request.
    """
    choices = data.get("choices") or []
    usage = _usage_of(data["usage"]) if data.get("usage") else None

    if not choices:
        return CanonicalChunk(text_delta="", finish_reason=None, usage=usage) if usage else None

    first = choices[0]
    delta = first.get("delta") or {}
    reason = first.get("finish_reason")
    # `delta.reasoning` is discarded, exactly as `answer_of` ignores the field.
    return CanonicalChunk(
        text_delta=str(delta.get("content") or ""),
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


def embedding_values(data: dict[str, Any]) -> list[list[float]]:
    """Vectors in the order submitted — the contract `FRD-113` FR-1 makes.

    Sorted by ``index`` rather than trusted: the field exists because the API promises no order.
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
