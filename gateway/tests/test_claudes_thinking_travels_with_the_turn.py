"""Claude's thinking blocks go back with the turn that continues them (`FRD-152`).

Anthropic needs a model message back as it was produced when its function calls are answered —
every thinking block complete, unmodified and in its place. This gateway handed the reasoning to a
use case that wanted it as text and dropped the blocks, so at every step of a tool loop the model
went on without its own reasoning: under a budget it stopped thinking for the rest of the turn,
adaptively it thought again from nothing — paid twice, and silent on the wire while it did, which is
the silence that ended streams as `499`s before they were kept alive (`ADR-0046`).

The blocks now ride the signature of the message's first function call — the field every Gemini
client hands back on that call — and are put back in their places in the turn in progress.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from aira_common.models import ThinkingMode
from aira_common.tokens import StaticTokenSource
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    Role,
    TextPart,
    Thinking,
    ToolCallPart,
    ToolDeclaration,
    ToolResultPart,
)
from aira_gateway.upstreams.base import DialectUnsupported, UpstreamError
from aira_gateway.upstreams.carried import is_sealed, opened, seal
from aira_gateway.upstreams.gemini_mapping import canonical_to_gemini_request
from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexModel
from aira_gateway.upstreams.vertex.anthropic_mapping import (
    StreamAssembler,
    anthropic_to_canonical,
    canonical_to_anthropic,
)
from aira_gateway.upstreams.vertex.anthropic_mapping.thinking_blocks import DIALECT
from aira_gateway.upstreams.vertex.transport import VertexTransport

#: A thinking block as Anthropic returns one under `display: "summarized"`.
THOUGHT = {"type": "thinking", "thinking": "Read the file first.", "signature": "EqQBCkYIBhgCKkA="}
#: The same block under `display: "omitted"`, the newest models' default: no text, the signature.
SILENT = {"type": "thinking", "thinking": "", "signature": "EqQBCkYIBhgCKkB="}
REDACTED = {"type": "redacted_thinking", "data": "EmwKAhgBEgy3va3pzix/LafPsn4="}
CALL = {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "hello.py"}}
SECOND_CALL = {"type": "tool_use", "id": "toolu_2", "name": "list_dir", "input": {"path": "."}}
#: The call as it goes back: under the id the caller's Gemini client gave it.
CALLED = {
    "type": "tool_use",
    "id": "read_file-1",
    "name": "read_file",
    "input": {"path": "hello.py"},
}


def _answer(*content: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": list(content),
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def _start(index: int, block: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": block}


def _delta(index: int, **delta: str) -> dict[str, Any]:
    return {"type": "content_block_delta", "index": index, "delta": delta}


def _stop(index: int) -> dict[str, Any]:
    return {"type": "content_block_stop", "index": index}


def _end(reason: str, output: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": reason},
            "usage": {"output_tokens": output},
        },
        {"type": "message_stop"},
    ]


def _calls(*content: dict[str, Any], include_reasoning: bool = True) -> tuple[ToolCallPart, ...]:
    return anthropic_to_canonical(
        _answer(*content), "claude-1", include_reasoning=include_reasoning
    ).tool_calls


# == the envelope ================================================================================


def test_a_sealed_state_opens_only_for_the_dialect_that_sealed_it() -> None:
    signature = seal(DIALECT, [{"type": "text", "text": "x"}])

    assert opened(signature, DIALECT) == [{"type": "text", "text": "x"}]
    assert opened(signature, "another") is None
    assert is_sealed(signature)


def test_a_vendors_own_signature_is_not_mistaken_for_one() -> None:
    google = base64.b64encode(b"\x12\x34 an opaque thought signature").decode()

    assert not is_sealed(google)
    assert opened(google, DIALECT) is None
    assert not is_sealed("not base64 at all!")
    assert not is_sealed(None)


def test_an_envelope_survives_the_round_trip_googles_sdks_make() -> None:
    """Google's SDKs read the field as bytes and write it again: only canonical base64 comes back
    character for character."""
    signature = seal(DIALECT, [THOUGHT, {"type": "tool_use", "name": "read_file"}])

    assert base64.b64encode(base64.b64decode(signature)).decode() == signature


# == the answer: the message's thinking rides its first call ====================================


def test_the_first_call_carries_its_messages_thinking_text_and_order() -> None:
    first, second = _calls(THOUGHT, {"type": "text", "text": "Reading."}, CALL, SECOND_CALL)

    assert opened(first.signature, DIALECT) == [
        THOUGHT,
        {"type": "text", "text": "Reading."},
        {"type": "tool_use", "name": "read_file"},
        {"type": "tool_use", "name": "list_dir"},
    ]
    assert second.signature is None


def test_a_hidden_thought_is_carried_whatever_the_use_case_says() -> None:
    """An empty block discloses nothing, and the newest models send every block empty."""
    (call,) = _calls(SILENT, CALL, include_reasoning=False)

    assert opened(call.signature, DIALECT) == [SILENT, {"type": "tool_use", "name": "read_file"}]


def test_reasoning_a_use_case_does_not_return_is_not_carried_either() -> None:
    """A Claude 4 model returns its summarised thinking unasked. Sealed, it would reach the caller
    after all — readable to anybody who decodes the field."""
    (call,) = _calls(THOUGHT, CALL, include_reasoning=False)

    assert call.signature is None


def test_redacted_thinking_is_carried_like_any_other() -> None:
    (call,) = _calls(REDACTED, SILENT, CALL)

    assert opened(call.signature, DIALECT)[:2] == [REDACTED, SILENT]


@pytest.mark.parametrize(
    "content",
    [
        [CALL],
        [{"type": "thinking", "thinking": "unsigned"}, CALL],
        [THOUGHT, {"type": "server_tool_use", "id": "s", "name": "web_search"}, CALL],
    ],
    ids=["no-thinking", "unsigned", "a-block-not-sent-back"],
)
def test_nothing_is_carried_that_could_not_go_back_whole(content: list[dict[str, Any]]) -> None:
    assert all(call.signature is None for call in _calls(*content))


def test_a_stream_carries_the_same_envelope_on_its_last_chunk() -> None:
    """Thinking, its signature and the text arrive in fragments; the envelope holds each whole."""
    assembler = StreamAssembler(include_reasoning=True)
    events: list[dict[str, Any]] = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
        _start(0, {**THOUGHT, "thinking": "", "signature": ""}),
        _delta(0, type="thinking_delta", thinking="Read the "),
        _delta(0, type="thinking_delta", thinking="file first."),
        _delta(0, type="signature_delta", signature=THOUGHT["signature"]),
        _stop(0),
        _start(1, {"type": "text", "text": ""}),
        _delta(1, type="text_delta", text="Reading."),
        _stop(1),
        _start(2, {**CALL, "input": {}}),
        _delta(2, type="input_json_delta", partial_json='{"path": "hello.py"}'),
        _stop(2),
        *_end("tool_use", 9),
    ]
    chunks = [assembler.feed(event) for event in events]

    (call,) = chunks[-1].tool_calls
    assert call.arguments == {"path": "hello.py"}
    assert opened(call.signature, DIALECT) == [
        THOUGHT,
        {"type": "text", "text": "Reading."},
        {"type": "tool_use", "name": "read_file"},
    ]


# == the request: the turn in progress goes back as it was produced =============================


def _turn(call: ToolCallPart, *, text: str = "Reading.") -> list[CanonicalMessage]:
    return [
        CanonicalMessage(role=Role.USER, text="read hello.py"),
        CanonicalMessage(role=Role.MODEL, parts=[TextPart(text=text), call]),
        CanonicalMessage(
            role=Role.USER,
            parts=[ToolResultPart(call_id=call.id, name=call.name, content="print(1)")],
        ),
    ]


def _sealed_call(*content: dict[str, Any], client_id: str = "read_file-1") -> ToolCallPart:
    """The call as a Gemini client hands it back: its own id, the arguments, the signature."""
    (call, *_) = _calls(*content)
    return call.model_copy(update={"id": client_id})


def _body(messages: list[CanonicalMessage], **over: Any) -> dict[str, Any]:
    carry = over.pop("carry_thinking", True)
    request = CanonicalRequest(
        model="claude-1",
        messages=messages,
        tools=(ToolDeclaration(name="read_file"),),
        **over,
    )
    return canonical_to_anthropic(request, max_tokens=8192, carry_thinking=carry)


def _assistant(body: dict[str, Any], nth: int = -1) -> list[dict[str, Any]]:
    turns = [message for message in body["messages"] if message["role"] == "assistant"]
    return turns[nth]["content"]


def test_the_turn_in_progress_goes_back_with_its_thinking_in_its_place() -> None:
    call = _sealed_call(THOUGHT, {"type": "text", "text": "Reading."}, CALL)

    content = _assistant(_body(_turn(call)))

    # The call goes back under the caller's id, which its result names.
    assert content == [THOUGHT, {"type": "text", "text": "Reading."}, CALLED]


def test_an_earlier_turn_goes_back_without_its_thinking() -> None:
    """Anthropic needs the blocks only in the turn in progress, and allows them out of earlier
    turns; out the same way every time, so nothing before a block changes once it is made."""
    call = _sealed_call(THOUGHT, {"type": "text", "text": "Reading."}, CALL)
    finished = [
        *_turn(call),
        CanonicalMessage(role=Role.MODEL, text="It prints 1."),
        CanonicalMessage(role=Role.USER, text="and now?"),
    ]

    body = _body(finished)

    assert all(block["type"] != "thinking" for block in _assistant(body, 0))


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"name": "write_file"}, "a call the model did not make"),
        ({"text": "Reading, edited."}, "text the model did not write"),
    ],
)
def test_a_history_that_changed_goes_as_the_caller_sent_it(
    change: dict[str, Any], why: str
) -> None:
    call = _sealed_call(THOUGHT, {"type": "text", "text": "Reading."}, CALL)
    if "name" in change:
        call = call.model_copy(update=change)
    messages = _turn(call, text=change.get("text", "Reading."))

    content = _assistant(_body(messages))

    assert all(block["type"] != "thinking" for block in content), why


def test_without_carrying_the_turn_goes_as_before() -> None:
    call = _sealed_call(THOUGHT, {"type": "text", "text": "Reading."}, CALL)

    content = _assistant(_body(_turn(call), carry_thinking=False))

    assert [block["type"] for block in content] == ["text", "tool_use"]


def test_a_budget_continues_the_call_its_thinking_came_back_with() -> None:
    """The case this gateway refused by name: Anthropic requires the model's own block in front
    of a call a budget continues."""
    call = _sealed_call(THOUGHT, CALL)
    budget = Thinking(mode=ThinkingMode.LIMITED, tokens=2048)

    body = _body(_turn(call, text=""), thinking=budget)

    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert _assistant(body)[0] == THOUGHT


def test_a_default_budget_without_its_thinking_goes_on_without_thinking() -> None:
    """The installation's default, not the caller's request: refusing it would fail the second
    step of every agent loop on a client that drops the signature."""
    call = ToolCallPart(id="c", name="read_file", arguments={})
    budget = Thinking(mode=ThinkingMode.LIMITED, tokens=2048)

    body = _body(_turn(call), thinking=budget, thinking_from_default=True)

    assert "thinking" not in body


def test_a_budget_the_caller_asked_for_is_still_refused_without_its_thinking() -> None:
    call = ToolCallPart(id="c", name="read_file", arguments={})
    budget = Thinking(mode=ThinkingMode.LIMITED, tokens=2048)

    with pytest.raises(DialectUnsupported, match="does not carry it back"):
        _body(_turn(call), thinking=budget)


def test_google_is_never_sent_another_dialects_state_as_its_signature() -> None:
    """A conversation that moves to a Gemini model carries the envelope along unread."""
    sealed = _sealed_call(THOUGHT, CALL)
    google = ToolCallPart(id="g", name="read_file", arguments={}, signature="Zm9vYmFy")
    request = CanonicalRequest(
        model="gemini",
        messages=[
            CanonicalMessage(role=Role.USER, text="go"),
            CanonicalMessage(role=Role.MODEL, parts=[sealed, google]),
        ],
    )

    parts = canonical_to_gemini_request(request)["contents"][1]["parts"]

    assert "thoughtSignature" not in parts[0]
    assert parts[1]["thoughtSignature"] == "Zm9vYmFy"


# == the adapter: a refusal of the carried thinking is not the end of the conversation ===========


def _anthropic_stream(text: str) -> httpx.Response:
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
        _start(0, {"type": "text", "text": ""}),
        _delta(0, type="text_delta", text=text),
        _stop(0),
        *_end("end_turn", 1),
    ]
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _adapter(seen: list[dict[str, Any]], refusal: str) -> VertexAnthropicAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        thinks = any(
            block.get("type") == "thinking"
            for message in body["messages"]
            for block in message["content"]
            if isinstance(message["content"], list)
        )
        if thinks:
            return httpx.Response(
                400, json={"error": {"type": "invalid_request_error", "message": refusal}}
            )
        return _anthropic_stream("done")

    transport = VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return VertexAnthropicAdapter(
        transport, [VertexModel("eu", "anthropic", "claude-1")], default_max_tokens=2048
    )


THINKING_REFUSED = (
    "messages.1.content.0: Invalid `signature` in `thinking` block. The block is bound to a "
    "different conversation."
)


def _continuing() -> CanonicalRequest:
    return CanonicalRequest(
        model="claude-1",
        messages=_turn(_sealed_call(THOUGHT, {"type": "text", "text": "Reading."}, CALL)),
        tools=(ToolDeclaration(name="read_file"),),
    )


async def test_a_refused_thinking_block_is_sent_once_more_without_it() -> None:
    seen: list[dict[str, Any]] = []

    answer = await _adapter(seen, THINKING_REFUSED).generate(_continuing())

    assert answer.text == "done"
    assert len(seen) == 2
    assert _assistant(seen[1]) == [{"type": "text", "text": "Reading."}, CALLED]


async def test_a_refused_stream_is_opened_once_more_without_it() -> None:
    seen: list[dict[str, Any]] = []
    adapter = _adapter(seen, THINKING_REFUSED)

    chunks = [chunk async for chunk in adapter.stream_generate(_continuing())]

    assert "".join(chunk.text_delta for chunk in chunks) == "done"
    assert len(seen) == 2


@pytest.mark.parametrize(
    "refusal",
    [
        "max_tokens: must be at least 1",
        # Names thinking, and not a block: a fault in the request, which the same request repeats.
        "thinking.budget_tokens: Input should be greater than or equal to 1024",
    ],
)
async def test_any_other_refusal_stands(refusal: str) -> None:
    seen: list[dict[str, Any]] = []

    with pytest.raises(UpstreamError):
        await _adapter(seen, refusal).generate(_continuing())
    assert len(seen) == 1
