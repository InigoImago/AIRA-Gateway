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
    DataPart,
    Role,
    TextPart,
    Thinking,
    ToolCallPart,
)
from aira_gateway.core.schema import ResponseSchema, to_json_schema
from aira_gateway.upstreams.base import DialectUnsupported

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
#:
#: `disabled` maps to the value `"none"` and **not** to an absent field. Measured against a real
#: server rather than assumed: a reasoning model sent no `reasoning_effort` **thinks anyway** — it
#: is the model's own default, not "off" — and spends the whole output allowance doing it. The
#: caller who explicitly switched thinking off then receives a 200, an empty or truncated answer,
#: and a bill for several hundred tokens of reasoning that is dropped before they ever see it.
#: `"none"` on the same server answers in about fifteen tokens.
_REASONING_EFFORT = {
    ThinkingMode.DISABLED: "none",
    ThinkingMode.MINIMAL: "minimal",
    ThinkingMode.LOW: "low",
    ThinkingMode.MEDIUM: "medium",
    ThinkingMode.HIGH: "high",
    ThinkingMode.AUTO: "medium",
}


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
        if not isinstance(part, DataPart):
            # Tool parts are carried by `_wire_messages`, which handles them *before* asking for
            # content — they never reach here. Explicit rather than implied: this loop used to
            # treat "not text" as "an attachment", and `FRD-131` made that untrue.
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


def _wire_messages(request: CanonicalRequest) -> list[dict[str, Any]]:
    """Canonical messages → this dialect's message list, which is **not** one-to-one.

    A canonical message carries ordered parts; this API carries a tool call as a field on an
    assistant message and each tool *result* as a message of its own with `role: "tool"`. So one
    canonical turn holding two results becomes two wire messages, and the order has to survive —
    a provider matches results to calls by id, but a model reading its own history reads the list.
    """
    out: list[dict[str, Any]] = []
    for message in request.messages:
        calls = message.tool_calls
        results = message.tool_results
        if not calls and not results:
            out.append({"role": _ROLE[message.role], "content": _content(message)})
            continue
        if calls:
            entry: dict[str, Any] = {"role": "assistant", "content": message.text or None}
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    # Arguments travel as a **JSON string** in this dialect, not as an object.
                    # The canonical model keeps them parsed, so the conversion happens once, here.
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in calls
            ]
            out.append(entry)
        for result in results:
            out.append({"role": "tool", "tool_call_id": result.call_id, "content": result.content})
    return out


def _wire_tools(request: CanonicalRequest) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": (
                    to_json_schema(tool.parameters)
                    if tool.parameters is not None
                    # A function that takes no arguments still needs a schema here; several
                    # implementations reject a declaration without one.
                    else {"type": "object", "properties": {}}
                ),
            },
        }
        for tool in request.tools
    ]


def canonical_to_openai(request: CanonicalRequest, *, stream: bool = False) -> dict[str, Any]:
    """Build a `/v1/chat/completions` body.

    Unlike Anthropic there is no separate system parameter — the system prompt is simply the first
    message — which is the one place this dialect is *simpler* than the other two.
    """
    body: dict[str, Any] = {
        "model": request.model,
        "messages": _wire_messages(request),
    }
    if request.tools:
        body["tools"] = _wire_tools(request)
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        body["max_tokens"] = request.max_output_tokens
    if request.thinking is not None:
        body["reasoning_effort"] = _reasoning_effort(request.thinking)
    if request.response_schema is not None:
        body["response_format"] = _response_format(request.response_schema)
    _add_sampling(body, request)
    if stream:
        body["stream"] = True
        # Usage is **not** reported on a streamed response unless it is asked for, and a stream
        # that reports none is released rather than settled (`FRD-405`) — so forgetting this would
        # make every streamed request free.
        body["stream_options"] = {"include_usage": True}
    return body


#: What this dialect can express (`FRD-124`). `top_k` is absent from the OpenAI chat API and is
#: therefore **refused** on a candidate served this way, not quietly dropped — a caller who pinned
#: `top_k=1` for near-determinism and silently got the default is the same failure as a dropped
#: attachment, one layer down.
SAMPLING = frozenset({"top_p", "seed", "presence_penalty", "frequency_penalty", "stop_sequences"})


def _add_sampling(body: dict[str, Any], request: CanonicalRequest) -> None:
    if request.top_p is not None:
        body["top_p"] = request.top_p
    if request.seed is not None:
        body["seed"] = request.seed
    if request.presence_penalty is not None:
        body["presence_penalty"] = request.presence_penalty
    if request.frequency_penalty is not None:
        body["frequency_penalty"] = request.frequency_penalty
    if request.stop_sequences:
        body["stop"] = list(request.stop_sequences)
    if request.top_k is not None:
        # Unreachable through the dispatch chain, which skips this candidate before we get here.
        # Kept as a backstop: the requirement and the mapping have to agree, and the day they
        # disagree the mapping is the one holding the request.
        raise DialectUnsupported(
            "This dialect has no 'top_k'. It is refused rather than dropped: a caller who set it "
            "and silently received the model's default would get a different answer with a 200 on "
            "it."
        )


def _reasoning_effort(setting: Thinking) -> str:
    """The abstract level this dialect takes — including an explicit `"none"` for off.

    The obvious implementation omits the field for `disabled`, on the reasoning that a parameter
    nobody sets is a feature nobody gets. That reasoning is wrong for a *reasoning* model, whose
    own default is to think, and the failure it produces is the worst-behaved kind: a 200 with an
    empty answer. Off has to be said out loud.
    """
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


#: The field a reasoning model returns its chain of thought in — **read from, never**. `FRD-111`
#: §2 decided thoughts are not returned, logged or persisted. With Gemini that was free (we simply
#: never ask); with Anthropic it became an active obligation to drop a block; here it is a third
#: shape of the same obligation, and the most easily missed, because the obvious implementation —
#: concatenating everything the message carries — would return it.
#:
#: Measured, not assumed: a one-word answer from a local reasoning model came back with `content`
#: of "Hi" and 439 characters of `reasoning`, all of it billed inside `completion_tokens`.
REASONING_FIELD = "reasoning"


def answer_of(message: dict[str, Any]) -> str:
    """The answer, and **only** the answer.

    An empty string when the model spent its whole allowance thinking. That is not hidden: the
    finish reason is `length` in that case and maps to `max_tokens`, so a caller receiving nothing
    can tell why. Substituting the reasoning for the missing answer would be the opposite of what
    `FRD-111` decided — chain of thought is the least reviewed text a model produces, it routinely
    restates the input, and it would land in a column the gateway also persists.
    """
    return str(message.get("content") or "")


def tool_calls_of(raw: Any) -> tuple[ToolCallPart, ...]:
    """This dialect's tool calls → canonical, with the arguments parsed.

    Arguments arrive as a **JSON string**, and a model occasionally produces one that does not
    parse — a truncated stream, or simply a bad generation. That is mapped to empty arguments with
    the name intact rather than raising: the caller can see *that* the model asked for `read_file`
    and decide what to do, whereas a 502 would hide a model's mistake behind ours.
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


def openai_to_canonical(data: dict[str, Any], model: str) -> CanonicalResponse:
    choices = data.get("choices") or []
    first = choices[0] if choices else {}
    message = first.get("message") or {}
    return CanonicalResponse(
        model=model,
        text=answer_of(message),
        finish_reason=finish_reason(first.get("finish_reason")),
        usage=_usage_of(data.get("usage")),
        tool_calls=tool_calls_of(message.get("tool_calls")),
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
    # `delta.reasoning` is discarded here for the same reason `answer_of` ignores it. A streaming
    # mapper that forwarded every string field in the delta would stream the model's thoughts to
    # the caller, one token at a time, into a response the gateway also persists.
    return CanonicalChunk(
        text_delta=str(delta.get("content") or ""),
        finish_reason=finish_reason(reason) if reason else None,
        usage=usage,
    )


class StreamedToolCalls:
    """Reassembles tool calls that arrive **in pieces** (`FRD-131` FR-6).

    This is the trap the FRD named before anything was built, and it is worth stating precisely.
    In a streamed response a tool call does not arrive whole: the first delta carries an index, an
    id and the function name, and the *arguments* then arrive as a series of string fragments
    across the following deltas — `{"pa`, `th": "he`, `llo.py"}`. A mapper that forwarded each
    delta would emit several half-formed calls, none of them parseable, and a client would either
    error or, worse, act on the first fragment it could make sense of.

    So fragments are accumulated by **index** — the only key present on every delta; `id` and
    `name` appear once — and the finished calls are emitted on the chunk that ends the message.
    Anything still incomplete when the stream ends is dropped rather than guessed at: half a
    function call is not a smaller function call, it is a different one.
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
