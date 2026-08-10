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
    DataPart,
    Role,
    TextPart,
    ToolCallPart,
    ToolResultPart,
)
from aira_gateway.core.schema import ResponseSchema, to_json_schema
from aira_gateway.upstreams.base import DialectUnsupported

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


#: What the Messages API can express (`FRD-124`). It has `top_p`, `top_k` and `stop_sequences`,
#: and it has **no** `seed` and no presence/frequency penalties — so a request that pins a seed for
#: reproducibility is refused on a Claude candidate rather than answered non-reproducibly with a
#: 200. This is the same shape as the `limited` thinking refusal one file over, and it is the
#: reason `SAMPLING` is declared per dialect rather than assumed.
SAMPLING = frozenset({"top_p", "top_k", "stop_sequences"})

_UNSUPPORTED_SAMPLING = {
    "seed": "no seed parameter, so a request cannot be made reproducible here",
    "presence_penalty": "no presence penalty",
    "frequency_penalty": "no frequency penalty",
}


def _add_sampling(body: dict[str, Any], request: CanonicalRequest) -> None:
    if request.top_p is not None:
        body["top_p"] = request.top_p
    if request.top_k is not None:
        body["top_k"] = request.top_k
    if request.stop_sequences:
        body["stop_sequences"] = list(request.stop_sequences)
    for name, why in _UNSUPPORTED_SAMPLING.items():
        if getattr(request, name) is not None:
            # A backstop behind the dispatch chain, which skips this candidate first. Kept because
            # the requirement and the mapping have to agree, and on the day they disagree the
            # mapping is the one holding the request.
            raise ValueError(f"The Anthropic Messages API has {why}; '{name}' cannot be honoured.")


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
        joined = "\n\n".join(system_parts)
        # A plain string unless caching is asked for. `cache_control` only exists on the block
        # form, and switching shape unconditionally would change a wire body for every request in
        # order to serve the few that opted in.
        body["system"] = (
            [{"type": "text", "text": joined, "cache_control": _cache_control(request)}]
            if request.cache_prefix
            else joined
        )
    if request.temperature is not None:
        body["temperature"] = request.temperature
    _add_sampling(body, request)
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
    if request.tools:
        body["tools"] = [
            {
                "name": tool.name,
                # `strict` asks the provider to guarantee the arguments match the schema. Requested
                # rather than assumed: the matrix has a whole row for arguments that do not parse,
                # and this removes one cause of it without removing the handling.
                "strict": True,
                "description": tool.description,
                "input_schema": (
                    to_json_schema(tool.parameters)
                    if tool.parameters is not None
                    else {"type": "object", "properties": {}}
                ),
            }
            for tool in request.tools
        ]
        if request.cache_prefix:
            # **On the last tool, not on each.** A breakpoint marks "everything up to here", and
            # Anthropic allows four in a request; one per tool would exhaust them on the fourth
            # function and cache almost nothing. Together with the system block this is two, which
            # is what the measurement says covers 99.1 % of an assistant turn.
            body["tools"][-1]["cache_control"] = _cache_control(request)
        # No `tool_choice`: the model decides. `AUTO` is what the surface accepts and the other
        # modes are refused there, so pinning anything here would invent an instruction nobody gave.
    if request.response_schema is not None:
        # **A first-class parameter, separate from `tools`** — checked against the API on
        # 2026-08-08. This used to be a forced tool call, and that was correct when `FRD-119` was
        # written: the dialect had no schema field, so the documented way to get a document was to
        # declare one tool and pin `tool_choice` to it.
        #
        # It also made the two mutually exclusive, because the caller's functions and our schema
        # needed the *same field*. That exclusion was never our design and is now gone: both travel
        # together, the model may call a tool **or** answer with the document, and `stop_reason`
        # says which.
        body["output_config"] = {
            "format": {
                "type": "json_schema",
                "schema": schema_for_anthropic(request.response_schema),
            }
        }
    return body


#: What this dialect's schema support does **not** cover (checked against the API on 2026-08-08).
#: Our `ResponseSchema` is Google's vocabulary and is wider, so a schema that works on one provider
#: is not automatically expressible here — `ADR-0012` §3's case exactly, and the answer is the same
#: one: skip the candidate **by name** rather than send a constraint that will be dropped.
UNSUPPORTED_SCHEMA_FIELDS = frozenset(
    {"minimum", "maximum", "min_length", "max_length", "min_items", "max_items", "pattern"}
)

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
        if isinstance(part, ToolCallPart):
            blocks.append(
                {"type": "tool_use", "id": part.id, "name": part.name, "input": part.arguments}
            )
            continue
        if isinstance(part, ToolResultPart):
            # `tool_result` belongs to the **user** turn on this dialect and names the call by id.
            blocks.append(
                {"type": "tool_result", "tool_use_id": part.call_id, "content": part.content}
            )
            continue
        if not isinstance(part, DataPart):  # pragma: no cover - the union is closed above
            raise DialectUnsupported(f"Unsupported part {type(part).__name__}.")
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


#: The five-minute lifetime, which is the default and the cheap one: a write costs 1.25x base
#: input against 2x for an hour. The measured gap between assistant turns is 41 seconds with 13 of
#: 14 inside five minutes, so the longer window would pay double to buy almost nothing.
_EPHEMERAL = {"type": "ephemeral"}


def _cache_control(request: CanonicalRequest) -> dict[str, str]:
    """The marker, with a lifetime only when the long one was asked for.

    An absent `ttl` is Anthropic's five-minute default, so the short case sends exactly what it
    sent before this parameter existed — the expensive option is the one that has to appear on the
    wire, never the cheap one.
    """
    return {**_EPHEMERAL, "ttl": "1h"} if request.cache_ttl == "1h" else dict(_EPHEMERAL)


def usage_of(payload: Any) -> CanonicalUsage:
    """Token usage. Cache tokens are folded into the input count where the provider reports them
    separately — they *were* input, and leaving them out would understate what the request cost.
    """
    usage = payload if isinstance(payload, dict) else {}
    cached = int(usage.get("cache_read_input_tokens", 0) or 0)
    created = int(usage.get("cache_creation_input_tokens", 0) or 0)
    return CanonicalUsage(
        # The total stays whole; the parts ride along beside it (`FRD-133`). Anthropic's own
        # arithmetic is the same: `input_tokens` counts only what fell *after* the last cache
        # breakpoint, so the three add up to everything the request was charged input for.
        prompt_tokens=int(usage.get("input_tokens", 0) or 0) + cached + created,
        cached_input_tokens=cached,
        cache_write_tokens=created,
        completion_tokens=int(usage.get("output_tokens", 0) or 0),
    )


def finish_reason(stop_reason: Any) -> str:
    """Mapped so `FRD-112` FR-6 can tell a complete document from a truncated one."""
    return _STOP_REASONS.get(str(stop_reason), "stop")


def schema_for_anthropic(schema: ResponseSchema) -> dict[str, Any]:
    """Our schema in the shape this provider requires.

    Two obligations it adds over plain JSON Schema, both checked against the API: every object must
    carry `additionalProperties: false`, and every object must list its `required` fields. They are
    filled in here rather than demanded of the caller — a Gemini-shaped schema is a legitimate
    request, and this is a translation, not a constraint the caller broke.
    """
    tightened = _tighten(to_json_schema(schema))
    assert isinstance(tightened, dict)  # a schema's root is an object by construction
    return tightened


def _tighten(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    out = {key: _tighten(value) for key, value in node.items()}
    if out.get("type") == "object":
        properties = out.get("properties") or {}
        out["additionalProperties"] = False
        # Absent `required` means "everything is optional" in JSON Schema and is rejected here, so
        # the honest translation of an unqualified object is that all of its properties are needed.
        out.setdefault("required", list(properties))
    if isinstance(out.get("properties"), dict):
        out["properties"] = {k: _tighten(v) for k, v in out["properties"].items()}
    return out


def schema_refusal(schema: ResponseSchema) -> str | None:
    """Why this dialect cannot express ``schema``, or ``None``.

    Read by the dispatch chain (`ADR-0012` §3) so an inexpressible schema **skips the candidate**
    instead of being sent with its constraints quietly dropped — which is `FRD-112`'s whole point,
    one layer down.
    """
    used = sorted(
        field for field in UNSUPPORTED_SCHEMA_FIELDS if getattr(schema, field, None) is not None
    )
    nested = list((schema.properties or {}).values())
    if schema.items is not None:
        nested.append(schema.items)
    nested.extend(schema.any_of or ())
    for child in nested:
        deeper = schema_refusal(child)
        if deeper is not None:
            return deeper
    if not used:
        return None
    return (
        f"this dialect's structured output does not support {', '.join(used)}, and a schema sent "
        "without them would be satisfied by an answer the caller's constraint excludes"
    )


def structured_document(content: Any) -> str | None:
    """The document, which now arrives as an ordinary **text block**.

    It used to be the input of a forced tool call. The provider gained a first-class schema
    parameter, so that mechanism — and the reason a schema and the caller's tools could not coexist
    — is gone.

    **It must still be JSON.** With the forced tool, "the model answered in prose instead" was
    visible for free: there was no tool call to read. Now prose and a document arrive through the
    same channel, and returning the prose as though it were the document is exactly what `FRD-112`
    FR-6 exists to prevent — the caller's code calls `JSON.parse` on it and fails somewhere else
    entirely. So the text is parsed here, and a text that is not a document is **not one**.

    The provider states the JSON is guaranteed. This does not take that on trust: a guarantee that
    is checked costs one parse, and a guarantee that is assumed costs a support case.
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
    """The caller's tool calls in a response.

    No exclusion any more: the structured document is a text block, so every `tool_use` block in a
    response is something the caller declared. That simplification is the whole point of the
    2026-08-08 rewrite — a mechanism disappeared instead of one being added.
    """
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
    data: dict[str, Any], model: str, *, structured: bool = False
) -> CanonicalResponse:
    text = answer_text(data.get("content"))
    reason = finish_reason(data.get("stop_reason"))

    if structured:
        document = structured_document(data.get("content"))
        if document is None:
            # Surfaced as an abnormal finish reason rather than as text, so the surface refuses it
            # instead of handing the caller prose that will fail to parse in their code.
            calls = tool_calls_of(data.get("content"))
            if calls:
                # Not a failure: with a schema *and* tools in one request the model may legitimately
                # call a function instead of answering, and `stop_reason` says so. Reporting that
                # as an unsatisfied schema would turn a normal agent turn into an error.
                return CanonicalResponse(
                    model=model,
                    text="",
                    finish_reason=reason,
                    usage=usage_of(data.get("usage")),
                    tool_calls=calls,
                )
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
        model=model,
        text=text,
        finish_reason=reason,
        usage=usage_of(data.get("usage")),
        # Reported for a structured request too: the model may call a function *instead of*
        # answering, which is now a legitimate outcome the caller has to be able to see.
        tool_calls=tool_calls_of(data.get("content")),
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
        #: The `tool_use` block currently open, if it is one of the **caller's** tools: its id,
        #: its name, and the `input_json_delta` fragments seen so far.
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
            # `input_json_delta` belongs to a `tool_use` block and carries a call's arguments,
            # which must be accumulated and never emitted as text — a client would otherwise
            # receive `{"pa`, `th": "he` as the model's reply.
            #
            # It used to mean two things: the structured document arrived through the same event,
            # and only this one said which. The document is a text block now, so the ambiguity is
            # gone rather than handled.
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
            # `thinking_delta` is discarded here for the same reason `answer_text` drops the block.
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
            # Anything still open is finished first: a provider that ends the message without a
            # closing event would otherwise drop the last call entirely.
            self._finish_call()
            return CanonicalChunk(
                text_delta="",
                finish_reason=self._finish or "stop",
                usage=CanonicalUsage(
                    prompt_tokens=self._prompt, completion_tokens=self._completion
                ),
                # Whole, on the chunk that ends the message (`FRD-131` FR-6). Half a function call
                # is not a smaller function call.
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
            # The name is kept: the caller can see *that* the model asked for it and decide, and a
            # dropped call would hide a model's mistake behind ours.
            arguments = {}
        self._calls.append(
            ToolCallPart(
                id=open_call["id"] or open_call["name"],
                name=open_call["name"],
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
