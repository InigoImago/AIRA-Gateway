"""A Claude conversation stays in its cache, from one message to the next (`ADR-0051`).

A prompt cache is a property of **consecutive requests**, which is why a test of one body cannot see
it: every body here was well formed while each new message of the caller's rewrote the conversation
at the write price, because the thinking of the turn before had been taken out of it.

No Claude model is reachable from this environment, so `_Anthropic` stands in, and a stand-in more
permissive than the thing it replaces proves nothing. It holds what Anthropic documents, and refuses
what Anthropic refuses:

- a cache entry exists only where an earlier request put a breakpoint, and a breakpoint looks back
  at most twenty positions for one, a run of calls or of results counting as one;
- from the last breakpoint on, input is not cached; up to it, it is read as far as an entry reaches
  and written beyond that;
- models from Opus 4.5 and Sonnet 4.6 on keep earlier turns' thinking in the prefix, earlier models
  drop it at the caller's next message, and everything after it falls out of the cache;
- a thinking block is valid only on the system prompt, the tools and the messages it was made on,
  and only after the block it was made after; blocks may be left out from the front, not the middle;
- a marker is refused on a thinking block and on empty text, and a fifth is refused.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import httpx
import pytest

from aira_common.tokens import StaticTokenSource
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    DataPart,
    Role,
    TextPart,
    Thinking,
    ToolCallPart,
    ToolDeclaration,
    ToolResultPart,
)
from aira_gateway.upstreams.carried import opened, seal
from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexModel
from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic
from aira_gateway.upstreams.vertex.anthropic_mapping.thinking_blocks import DIALECT, prefixes
from aira_gateway.upstreams.vertex.transport import VertexTransport

LOOKBACK = 20
MAX_BREAKPOINTS = 4
#: What a file an assistant read weighs here, in the stand-in's tokens: enough that the conversation
#: dwarfs the tools and the system prompt, as it does on a large repository.
FILE = "x" * 4_000

SYSTEM = "You are a coding assistant."
TOOLS = (ToolDeclaration(name="read"), ToolDeclaration(name="write"))
CLEARED = "[Old tool result content cleared]"


def _tokens(value: Any) -> int:
    return len(json.dumps(value)) // 4 + 1


def _without_marker(block: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in block.items() if key != "cache_control"}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class _Refused(Exception):
    pass


class _Anthropic:
    """Anthropic's documented rules for a conversation's prefix: see the module's docstring."""

    def __init__(self, *, keeps_earlier_thinking: bool = True) -> None:
        self._keeps = keeps_earlier_thinking
        self._entries: set[str] = set()
        self._blocks_made = 0
        #: What the model is to do next: call a function this many more times, then answer.
        self.calls_left = 0
        self.bodies: list[dict[str, Any]] = []
        self.usages: list[dict[str, int]] = []
        self.refusals: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        try:
            self._check_markers(body)
            self._check_thinking(body)
        except _Refused as refusal:
            self.refusals.append(str(refusal))
            return httpx.Response(
                400, json={"error": {"type": "invalid_request_error", "message": str(refusal)}}
            )
        usage = self._account(body)
        self.usages.append(usage)
        return self._answer(body, usage)

    # == what a request may carry ================================================================

    def _check_markers(self, body: dict[str, Any]) -> None:
        marked = [block for block in _all_blocks(body) if "cache_control" in block]
        if len(marked) > MAX_BREAKPOINTS:
            raise _Refused(
                f"A maximum of 4 blocks with cache_control may be provided: {len(marked)}"
            )
        for block in marked:
            if block.get("type") in ("thinking", "redacted_thinking"):
                raise _Refused("cache_control cannot be set on a thinking block")
            if block.get("type") == "text" and not block["text"].strip():
                raise _Refused("cache_control cannot be set for empty text blocks")
            if block.get("type") == "tool_result" and not block.get("content"):
                raise _Refused("cache_control cannot be set for empty text blocks")
        if isinstance(body.get("system"), list) and not body["system"][0]["text"].strip():
            raise _Refused("system: text content blocks must be non-empty")

    def _check_thinking(self, body: dict[str, Any]) -> None:
        """Each block on what it was made on, after the block it was made after."""
        before: str | None = None
        for index, message in enumerate(body["messages"]):
            if message["role"] != "assistant":
                continue
            made_on = _made_on(body, index)
            for number, block in enumerate(message["content"]):
                if block["type"] != "thinking":
                    continue
                state = json.loads(base64.b64decode(block["signature"]))
                if state["on"] != made_on:
                    raise _Refused(
                        f"messages.{index}.content.{number}: Invalid `signature` in `thinking` "
                        "block. The block is bound to a different conversation."
                    )
                if before is not None and state["after"] != before:
                    raise _Refused(
                        f"messages.{index}.content.{number}: Invalid `signature` in `thinking` "
                        "block. A block before it was removed."
                    )
                before = block["signature"]

    # == what a request costs ====================================================================

    def _account(self, body: dict[str, Any]) -> dict[str, int]:
        positions = _positions(body, keeps_earlier_thinking=self._keeps)
        breakpoints = [i for i, position in enumerate(positions) if position["marked"]]
        if not breakpoints:
            return {"input_tokens": sum(p["tokens"] for p in positions)}
        hit = -1
        for mark in breakpoints:
            for i in range(mark, max(mark - LOOKBACK, -1), -1):
                if positions[i]["prefix"] in self._entries:
                    hit = max(hit, i)
                    break
        last = breakpoints[-1]
        for mark in breakpoints:
            self._entries.add(positions[mark]["prefix"])
        return {
            "cache_read_input_tokens": sum(p["tokens"] for p in positions[: hit + 1]),
            "cache_creation_input_tokens": sum(p["tokens"] for p in positions[hit + 1 : last + 1]),
            "input_tokens": sum(p["tokens"] for p in positions[last + 1 :]),
        }

    # == what the model says =====================================================================

    def _answer(self, body: dict[str, Any], usage: dict[str, int]) -> httpx.Response:
        self._blocks_made += 1
        state = {
            "made": self._blocks_made,
            "on": _made_on(body, len(body["messages"])),
            "after": _last_signature(body),
        }
        signature = base64.b64encode(json.dumps(state).encode()).decode()
        events: list[dict[str, Any]] = [
            {"type": "message_start", "message": {"usage": {**usage, "output_tokens": 1}}},
            _block_start(0, {"type": "thinking", "thinking": "", "signature": ""}),
            _delta(0, type="signature_delta", signature=signature),
            _block_stop(0),
        ]
        if self.calls_left:
            self.calls_left -= 1
            call = {"type": "tool_use", "id": f"call-{self._blocks_made}", "name": "read"}
            events += [
                _block_start(1, {**call, "input": {}}),
                _delta(1, type="input_json_delta", partial_json='{"path": "a.py"}'),
                _block_stop(1),
                _message_end("tool_use"),
            ]
        else:
            events += [
                _block_start(1, {"type": "text", "text": ""}),
                _delta(1, type="text_delta", text="Done."),
                _block_stop(1),
                _message_end("end_turn"),
            ]
        events.append({"type": "message_stop"})
        stream = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})


def _block_start(index: int, block: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": block}


def _delta(index: int, **delta: str) -> dict[str, Any]:
    return {"type": "content_block_delta", "index": index, "delta": delta}


def _block_stop(index: int) -> dict[str, Any]:
    return {"type": "content_block_stop", "index": index}


def _message_end(reason: str) -> dict[str, Any]:
    return {
        "type": "message_delta",
        "delta": {"stop_reason": reason},
        "usage": {"output_tokens": 9},
    }


def _all_blocks(body: dict[str, Any]) -> list[dict[str, Any]]:
    system = body.get("system")
    return [
        *body.get("tools", []),
        *(system if isinstance(system, list) else []),
        *(block for message in body["messages"] for block in message["content"]),
    ]


def _made_on(body: dict[str, Any], index: int) -> str:
    """What the conversation before message ``index`` is, as a block's validity reads it: without
    thinking blocks and markers, with the tools as a set."""
    system = body.get("system") or ""
    return _digest(
        {
            "system": system[0]["text"] if isinstance(system, list) else system,
            "tools": sorted(
                (_without_marker(tool) for tool in body.get("tools", [])), key=lambda t: t["name"]
            ),
            "messages": [
                [
                    message["role"],
                    [
                        _without_marker(block)
                        for block in message["content"]
                        if block["type"] not in ("thinking", "redacted_thinking")
                    ],
                ]
                for message in body["messages"][:index]
            ],
        }
    )


def _last_signature(body: dict[str, Any]) -> str | None:
    signatures = [
        block["signature"]
        for message in body["messages"]
        for block in message["content"]
        if block["type"] == "thinking"
    ]
    return signatures[-1] if signatures else None


def _positions(body: dict[str, Any], *, keeps_earlier_thinking: bool) -> list[dict[str, Any]]:
    """The request as the cache sees it, in order: a running hash and a weight per position. A run
    of function calls, or of results, is one position."""
    messages = body["messages"]
    new_turn = max(
        (
            index
            for index, message in enumerate(messages)
            if message["role"] == "user"
            and any(block["type"] != "tool_result" for block in message["content"])
        ),
        default=0,
    )
    rendered: list[tuple[str, dict[str, Any]]] = [("tool", tool) for tool in body.get("tools", [])]
    system = body.get("system")
    if isinstance(system, list):
        rendered += [("system", block) for block in system]
    for index, message in enumerate(messages):
        for block in message["content"]:
            if not keeps_earlier_thinking and block["type"] == "thinking" and index < new_turn:
                continue
            rendered.append((block["type"], block))

    positions: list[dict[str, Any]] = []
    seen = hashlib.sha256()
    kind_before = None
    for kind, block in rendered:
        plain = _without_marker(block)
        seen.update(json.dumps(plain, sort_keys=True).encode())
        position = {
            "prefix": seen.hexdigest(),
            "tokens": _tokens(plain),
            "marked": "cache_control" in block,
        }
        if kind in ("tool_use", "tool_result") and kind == kind_before:
            positions[-1] = {
                **position,
                "tokens": positions[-1]["tokens"] + position["tokens"],
                "marked": positions[-1]["marked"] or position["marked"],
            }
        else:
            positions.append(position)
        kind_before = kind
    return positions


class _Session:
    """An assistant's conversation as a client holds it: everything sent again, appended to."""

    def __init__(
        self,
        anthropic: _Anthropic,
        *,
        streamed: bool = False,
        cache_prefix: bool = True,
        cache_ttl: str = "5m",
    ) -> None:
        transport = VertexTransport(
            project="p",
            tokens=StaticTokenSource("t"),
            client=httpx.AsyncClient(transport=httpx.MockTransport(anthropic)),
        )
        self._adapter = VertexAnthropicAdapter(
            transport, [VertexModel("eu", "anthropic", "claude-1")], default_max_tokens=4096
        )
        self._anthropic = anthropic
        self._streamed = streamed
        self._cache_prefix = cache_prefix
        self._cache_ttl = cache_ttl
        self.system = SYSTEM
        self.tools = TOOLS
        self.messages: list[CanonicalMessage] = []

    async def turn(self, said: str, *, reads: int = 1) -> None:
        """One message of the caller's, the function calls it sets off, and the answer."""
        self.messages.append(CanonicalMessage(role=Role.USER, text=said))
        self._anthropic.calls_left = reads
        while True:
            answer = await self._ask()
            if not answer.tool_calls:
                self.messages.append(CanonicalMessage(role=Role.MODEL, text=answer.text))
                return
            self.messages.append(CanonicalMessage(role=Role.MODEL, parts=list(answer.tool_calls)))
            self.messages.append(
                CanonicalMessage(
                    role=Role.USER,
                    parts=[
                        ToolResultPart(call_id=call.id, name=call.name, content=FILE)
                        for call in answer.tool_calls
                    ],
                )
            )

    def clear_oldest_result(self) -> None:
        """What OpenCode does to a long session's old tool results, between two turns."""
        for index, message in enumerate(self.messages):
            results = message.tool_results
            if results and results[0].content != CLEARED:
                self.messages[index] = message.model_copy(
                    update={"parts": [results[0].model_copy(update={"content": CLEARED})]}
                )
                return

    async def _ask(self) -> CanonicalResponse:
        request = CanonicalRequest(
            model="claude-1",
            messages=[CanonicalMessage(role=Role.SYSTEM, text=self.system), *self.messages],
            tools=self.tools,
            thinking=Thinking(mode="auto"),
            cache_prefix=self._cache_prefix,
            cache_ttl=self._cache_ttl,
        )
        if not self._streamed:
            return await self._adapter.generate(request)
        text = ""
        calls: list[Any] = []
        async for chunk in self._adapter.stream_generate(request):
            text += chunk.text_delta
            calls.extend(chunk.tool_calls)
        return CanonicalResponse(
            model="claude-1",
            text=text,
            tool_calls=tuple(calls),
            usage=CanonicalUsage(prompt_tokens=0, completion_tokens=0),
        )


def _thinking_of(body: dict[str, Any]) -> list[str]:
    return [
        block["signature"]
        for message in body["messages"]
        for block in message["content"]
        if block["type"] == "thinking"
    ]


# == the conversation is one prefix, growing ======================================================


@pytest.mark.parametrize("streamed", [False, True], ids=["whole", "streamed"])
async def test_a_new_message_reads_the_whole_conversation_back(streamed: bool) -> None:
    """**The defect.** A turn that went back without its thinking was a different prefix from its
    first block on, so every new message of the caller's wrote the conversation again, at 1.25x or
    2x, and the provider read it anew before the first word. Now it writes what it added."""
    anthropic = _Anthropic()
    session = _Session(anthropic, streamed=streamed)

    for said in ("first", "second", "third"):
        await session.turn(said, reads=2)

    assert anthropic.refusals == []
    for before, usage in zip(anthropic.usages, anthropic.usages[1:], strict=False):
        assert usage["cache_read_input_tokens"] >= (
            before["cache_read_input_tokens"] + before["cache_creation_input_tokens"]
        ), "what the request before it wrote is read back"
        assert usage["cache_creation_input_tokens"] < 2 * _tokens(FILE), "only what it added"


async def test_every_request_is_the_one_before_it_with_something_added() -> None:
    """The property behind the cost, without the stand-in's arithmetic: byte for byte, leaving the
    moving markers out."""
    anthropic = _Anthropic()
    session = _Session(anthropic)

    for said in ("first", "second", "third"):
        await session.turn(said, reads=2)

    def plain(body: dict[str, Any]) -> list[Any]:
        return [
            [message["role"], [_without_marker(block) for block in message["content"]]]
            for message in body["messages"]
        ]

    for before, after in zip(anthropic.bodies, anthropic.bodies[1:], strict=False):
        assert plain(after)[: len(plain(before))] == plain(before)
        assert [_without_marker(t) for t in after["tools"]] == [
            _without_marker(t) for t in before["tools"]
        ]
        assert after["system"] == before["system"]


async def test_earlier_turns_go_back_with_their_thinking() -> None:
    anthropic = _Anthropic()
    session = _Session(anthropic)

    await session.turn("first", reads=2)
    made = _thinking_of(anthropic.bodies[-1])
    await session.turn("second", reads=0)

    assert _thinking_of(anthropic.bodies[-1])[: len(made)] == made
    assert len(made) == 2


async def test_a_model_up_to_sonnet_4_5_is_read_up_to_the_callers_last_message() -> None:
    """Those models drop a finished turn's thinking themselves, which ends the cached prefix there,
    and a long tool loop is further back than the twenty positions a breakpoint looks: without a
    read point of its own at the turn's start, the new message wrote the whole conversation."""
    anthropic = _Anthropic(keeps_earlier_thinking=False)
    session = _Session(anthropic)

    await session.turn("first", reads=1)
    await session.turn("second", reads=25)
    settled = sum(_tokens(message.text) for message in session.messages[:4])

    await session.turn("third", reads=0)

    usage = anthropic.usages[-1]
    assert usage["cache_read_input_tokens"] > settled + _tokens(FILE)


async def test_a_long_tool_loop_still_reads_the_step_before_it() -> None:
    anthropic = _Anthropic()
    session = _Session(anthropic)

    await session.turn("go", reads=30)

    reads = [usage["cache_read_input_tokens"] for usage in anthropic.usages]
    assert reads == sorted(reads)
    assert reads[-1] > 29 * _tokens(FILE)


# == a history that changed refuses nothing =======================================================


async def test_a_cleared_tool_result_refuses_nothing() -> None:
    """OpenCode clears old tool results. Every thinking block after one is then invalid, and sent
    back it is a refused request, on every step from then on: the gateway has no state to remember
    the refusal in. The envelope says what its blocks were made on, so they simply stay out."""
    anthropic = _Anthropic()
    session = _Session(anthropic)

    await session.turn("first", reads=2)
    session.clear_oldest_result()
    await session.turn("second", reads=2)
    await session.turn("third", reads=1)

    assert anthropic.refusals == []


async def test_blocks_made_after_the_change_go_back() -> None:
    anthropic = _Anthropic()
    session = _Session(anthropic)

    await session.turn("first", reads=2)
    session.clear_oldest_result()
    await session.turn("second", reads=2)
    made_since = _thinking_of(anthropic.bodies[-1])
    await session.turn("third", reads=0)

    assert made_since, "the turn after the change made blocks of its own"
    assert _thinking_of(anthropic.bodies[-1])[-len(made_since) :] == made_since


async def test_blocks_made_before_the_change_it_came_after_keep_theirs() -> None:
    """A block is judged on what came before it: a result cleared after it changes nothing."""
    anthropic = _Anthropic()
    session = _Session(anthropic)

    await session.turn("first", reads=2)
    (first, second) = _thinking_of(anthropic.bodies[-1])
    results = [index for index, m in enumerate(session.messages) if m.tool_results]
    session.messages[results[-1]] = session.messages[results[-1]].model_copy(
        update={
            "parts": [
                session.messages[results[-1]]
                .tool_results[0]
                .model_copy(update={"content": CLEARED})
            ]
        }
    )
    await session.turn("second", reads=0)

    assert anthropic.refusals == []
    carried = _thinking_of(anthropic.bodies[-1])
    assert first in carried
    assert second in carried, "made before the last result, and nothing before it changed"


@pytest.mark.parametrize(
    "change",
    ["the system prompt", "a tool"],
)
async def test_a_changed_prompt_or_tool_refuses_nothing(change: str) -> None:
    """OpenCode's system prompt holds the date; a client may gain a tool mid-session."""
    anthropic = _Anthropic()
    session = _Session(anthropic)

    await session.turn("first", reads=2)
    if change == "a tool":
        session.tools = (*TOOLS, ToolDeclaration(name="grep"))
    else:
        session.system = f"{SYSTEM} Today is another day."
    await session.turn("second", reads=1)

    assert anthropic.refusals == []
    assert len(_thinking_of(anthropic.bodies[-1])) == 1, "the turn's own, and none from before"


async def test_tools_in_another_order_change_nothing() -> None:
    """Anthropic binds a block to the set of tools, so a client that reorders them keeps them."""
    anthropic = _Anthropic()
    session = _Session(anthropic)

    await session.turn("first", reads=2)
    session.tools = tuple(reversed(TOOLS))
    await session.turn("second", reads=0)

    assert anthropic.refusals == []
    assert len(_thinking_of(anthropic.bodies[-1])) == 2


# == a refusal all the same costs a request, never the conversation ================================


def _refusing(*, of: str) -> _Anthropic:
    """A provider that sees a change this gateway cannot: it refuses what it was told to."""

    class Refusing(_Anthropic):
        def _check_thinking(self, body: dict[str, Any]) -> None:
            blocks = _thinking_of(body)
            new_turn = max(
                index
                for index, message in enumerate(body["messages"])
                if message["role"] == "user"
                and any(block["type"] != "tool_result" for block in message["content"])
            )
            earlier = sum(
                block["type"] == "thinking"
                for message in body["messages"][:new_turn]
                for block in message["content"]
            )
            if blocks and (of == "any" or earlier):
                raise _Refused(
                    "messages.1.content.0: Invalid `signature` in `thinking` block. The block is "
                    "bound to a different conversation."
                )

    return Refusing()


async def test_earlier_turns_are_given_up_first_and_the_turn_in_progress_keeps_its_thinking() -> (
    None
):
    """What this round added may not cost what it replaced: a refusal of the earlier turns' blocks
    leaves the request exactly as it was before they were carried."""
    anthropic = _refusing(of="earlier")
    session = _Session(anthropic)

    await session.turn("first", reads=2)
    await session.turn("second", reads=2)

    assert anthropic.refusals, "the blocks of the first turn were sent and refused"
    last = anthropic.bodies[-1]
    assert len(_thinking_of(last)) == 2, "the turn in progress still goes back with its own"


async def test_a_refusal_of_every_block_ends_without_any() -> None:
    anthropic = _refusing(of="any")
    session = _Session(anthropic)

    await session.turn("first", reads=2)
    await session.turn("second", reads=2)

    assert _thinking_of(anthropic.bodies[-1]) == []
    assert session.messages[-1].text == "Done."


# == an envelope from before this was built ========================================================


def _old_envelope() -> str:
    """As a gateway sealed it before an envelope said what its blocks were made on."""
    return seal(
        DIALECT,
        [
            {"type": "thinking", "thinking": "", "signature": "c2ln"},
            {"type": "tool_use", "name": "read"},
        ],
    )


def _with_old_envelope(*later: CanonicalMessage) -> CanonicalRequest:
    call = ToolCallPart(id="c1", name="read", arguments={}, signature=_old_envelope())
    return CanonicalRequest(
        model="claude-1",
        messages=[
            CanonicalMessage(role=Role.USER, text="go"),
            CanonicalMessage(role=Role.MODEL, parts=[call]),
            CanonicalMessage(
                role=Role.USER, parts=[ToolResultPart(call_id="c1", name="read", content="x")]
            ),
            *later,
        ],
    )


def test_an_envelope_from_before_goes_back_in_its_own_turn() -> None:
    body = canonical_to_anthropic(_with_old_envelope(), max_tokens=100)

    assert body["messages"][1]["content"][0]["type"] == "thinking"


def test_an_envelope_from_before_stays_out_of_a_later_turn() -> None:
    """It does not say what it was made on, so a conversation in progress goes on as it did:
    assuming its history unchanged could be a refused request on the first turn after an upgrade."""
    body = canonical_to_anthropic(
        _with_old_envelope(
            CanonicalMessage(role=Role.MODEL, text="done"),
            CanonicalMessage(role=Role.USER, text="next"),
        ),
        max_tokens=100,
    )

    assert [block["type"] for block in body["messages"][1]["content"]] == ["tool_use"]


def test_carrying_less_leaves_out_what_does_not_say_as_well() -> None:
    """The adapter's attempt without earlier turns' thinking. Whatever an envelope says or does not,
    that attempt sends no earlier block: it is what the request looked like before they were
    carried."""
    body = canonical_to_anthropic(
        _with_old_envelope(
            CanonicalMessage(role=Role.MODEL, text="done"),
            CanonicalMessage(role=Role.USER, text="next"),
        ),
        max_tokens=100,
        carry_earlier=False,
    )

    assert [block["type"] for block in body["messages"][1]["content"]] == ["tool_use"]


# == what a history is, for a block ================================================================


def _digests(
    *messages: CanonicalMessage,
    system: str = SYSTEM,
    tools: tuple[dict[str, Any], ...] = ({"name": "read"}, {"name": "write"}),
) -> list[str]:
    return prefixes(messages, system=system, tools=tools)


def _said(*parts: Any) -> CanonicalMessage:
    return CanonicalMessage(role=Role.USER, parts=list(parts))


def _called(arguments: dict[str, Any]) -> CanonicalMessage:
    return CanonicalMessage(
        role=Role.MODEL, parts=[ToolCallPart(id="c", name="read", arguments=arguments)]
    )


def test_each_message_is_told_what_came_before_it_and_an_answer_what_came_before_that() -> None:
    first, second, after = _digests(_said(TextPart(text="a")), _said(TextPart(text="b")))

    assert first != second != after
    assert first == _digests()[0], "nothing has been said before the first message"
    assert second == _digests(_said(TextPart(text="a")))[-1]


@pytest.mark.parametrize(
    ("one", "other"),
    [
        (_said(TextPart(text="a")), _said(TextPart(text=" a\n"))),
        (_called({"x": 1, "y": 2}), _called({"y": 2, "x": 1})),
    ],
    ids=["outer-whitespace", "key-order"],
)
def test_what_anthropic_does_not_count_as_a_change_is_none_here(
    one: CanonicalMessage, other: CanonicalMessage
) -> None:
    """A digest stricter than Anthropic's check only costs a block that was still good; these two
    are what a client's own round trip does to a history."""
    assert _digests(one)[-1] == _digests(other)[-1]


@pytest.mark.parametrize(
    ("one", "other"),
    [
        (_said(TextPart(text="a")), _said(TextPart(text="b"))),
        (_called({"x": 1}), _called({"x": 2})),
        (
            _said(ToolResultPart(call_id="c", name="read", content="a")),
            _said(ToolResultPart(call_id="c", name="read", content=CLEARED)),
        ),
        (
            _said(DataPart(media_type="image/png", data=b"one")),
            _said(DataPart(media_type="image/png", data=b"two")),
        ),
        (
            _said(
                ToolResultPart(
                    call_id="c",
                    name="read",
                    content="a",
                    attachments=(DataPart(media_type="image/png", data=b"one"),),
                )
            ),
            _said(
                ToolResultPart(
                    call_id="c",
                    name="read",
                    content="a",
                    attachments=(DataPart(media_type="image/png", data=b"two"),),
                )
            ),
        ),
    ],
    ids=["text", "arguments", "result", "attachment", "media-in-a-result"],
)
def test_what_anthropic_counts_as_a_change_is_one_here(
    one: CanonicalMessage, other: CanonicalMessage
) -> None:
    assert _digests(one)[-1] != _digests(other)[-1]


def test_the_system_prompt_and_the_set_of_tools_are_part_of_it() -> None:
    assert _digests(system="another")[-1] != _digests()[-1]
    assert _digests(tools=({"name": "read"},))[-1] != _digests()[-1]
    assert _digests(tools=({"name": "write"}, {"name": "read"}))[-1] == _digests()[-1]


def test_a_system_message_is_counted_with_the_system_prompt_not_where_it_stands() -> None:
    """It is sent at the top whichever turn it arrived in, and the system text says what it says."""
    system = CanonicalMessage(role=Role.SYSTEM, text=SYSTEM)

    assert (
        _digests(system, _said(TextPart(text="a")))[-1] == _digests(_said(TextPart(text="a")))[-1]
    )


def test_a_lone_surrogate_in_a_clients_json_has_a_digest_too() -> None:
    assert _digests(_said(TextPart(text="\ud83d")))[-1]


# == an envelope states what its blocks were made on ==============================================


async def test_an_envelope_names_what_its_message_was_made_on() -> None:
    anthropic = _Anthropic()
    session = _Session(anthropic)

    await session.turn("go", reads=1)

    state = opened(session.messages[1].tool_calls[0].signature, DIALECT)
    assert isinstance(state["prefix"], str)
    assert [entry["type"] for entry in state["layout"]] == ["thinking", "tool_use"]
