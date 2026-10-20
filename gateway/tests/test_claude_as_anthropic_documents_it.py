"""The Anthropic body this gateway sends, held to what Anthropic documents (`FRD-145`).

Each test is a rule from Anthropic's own reference, checked against the body the dialect builds;
before this round every one of them was a request Anthropic answers 400:

- Claude Opus 4.7 and every later model refuse `thinking: {type: "enabled"}` and think only
  `adaptive`, steered by `output_config.effort`; Claude 4.5 and earlier refuse `adaptive`. The
  catalogue says which a model takes — a budget range or level words — and the dialect spells each.
- A budget is at least 1024 tokens. The newest models return their thinking empty unless asked
  for `display: "summarized"`.
- A text block must contain non-whitespace text, and a turn must contain a block.
- Structured output knows ten string formats, `minItems` of 0 and 1, and no numeric or length
  constraints.
- An hour's cache write is billed at twice the input price; the five minutes' at 1.25x.
"""

from __future__ import annotations

from typing import Any

import pytest

from aira_common.models import ThinkingMode
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalUsage,
    DataPart,
    FinishReason,
    Role,
    TextPart,
    Thinking,
    ToolCallPart,
    ToolChoice,
    ToolDeclaration,
    ToolMode,
    ToolResultPart,
)
from aira_gateway.core.schema import parse
from aira_gateway.pricing import Price, PricingService
from aira_gateway.upstreams.base import DialectUnsupported
from aira_gateway.upstreams.vertex.anthropic_mapping import (
    StreamAssembler,
    anthropic_to_canonical,
    canonical_to_anthropic,
    schema_for_anthropic,
    schema_refusal,
    usage_of,
)
from aira_gateway.upstreams.vertex.anthropic_mapping.request import MIN_THINKING_BUDGET

#: A one-pixel PNG: the magic bytes are what the attachment checks look at.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _request(**over: Any) -> CanonicalRequest:
    return CanonicalRequest(
        model="claude",
        messages=[CanonicalMessage(role=Role.USER, text="hello")],
        **over,
    )


def _body(request: CanonicalRequest, max_tokens: int = 8192) -> dict[str, Any]:
    return canonical_to_anthropic(request, max_tokens=max_tokens)


# == thinking =====================================================================================


@pytest.mark.parametrize("level", ["low", "medium", "high", "xhigh", "max"])
def test_a_level_word_is_adaptive_thinking_at_that_effort(level: str) -> None:
    """What the reference list declares for every Claude from Opus 4.7 on: five effort words."""
    body = _body(_request(thinking=Thinking(mode=level)))

    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": level}


def test_auto_is_adaptive_thinking_with_the_effort_left_to_the_model() -> None:
    body = _body(_request(thinking=Thinking(mode=ThinkingMode.AUTO)))

    assert body["thinking"] == {"type": "adaptive"}
    assert "output_config" not in body


def test_a_budget_is_the_enabled_shape_older_models_take() -> None:
    body = _body(_request(thinking=Thinking(mode=ThinkingMode.LIMITED, tokens=2048)))

    assert body["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert "output_config" not in body


def test_a_budget_below_anthropics_floor_is_refused_by_name() -> None:
    request = _request(thinking=Thinking(mode=ThinkingMode.LIMITED, tokens=MIN_THINKING_BUDGET - 1))

    with pytest.raises(DialectUnsupported, match="1024"):
        _body(request)


def test_the_floor_itself_is_sent() -> None:
    body = _body(_request(thinking=Thinking(mode=ThinkingMode.LIMITED, tokens=MIN_THINKING_BUDGET)))

    assert body["thinking"]["budget_tokens"] == MIN_THINKING_BUDGET


def test_a_use_case_that_wants_the_reasoning_asks_for_the_summary() -> None:
    """The newest models default to `display: "omitted"` and return their thinking blocks empty;
    a use case that turned reasoning on would receive nothing. Asked for only there: a use case that
    withholds reasoning gets the model's own default, which costs nothing to stream."""
    shown = _body(_request(thinking=Thinking(mode="high"), include_reasoning=True))
    withheld = _body(_request(thinking=Thinking(mode="high")))

    assert shown["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert withheld["thinking"] == {"type": "adaptive"}


def test_the_effort_and_a_schema_share_one_output_config() -> None:
    schema = parse({"type": "OBJECT", "properties": {"a": {"type": "STRING"}}, "required": ["a"]})
    body = _body(_request(thinking=Thinking(mode="low"), response_schema=schema))

    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"


def _continuing(**over: Any) -> CanonicalRequest:
    return CanonicalRequest(
        model="claude",
        messages=[
            CanonicalMessage(role=Role.USER, text="read a"),
            CanonicalMessage(
                role=Role.MODEL, parts=[ToolCallPart(id="toolu_1", name="read", arguments={})]
            ),
            CanonicalMessage(
                role=Role.USER,
                parts=[ToolResultPart(call_id="toolu_1", name="read", content="1")],
            ),
        ],
        **over,
    )


def test_adaptive_thinking_continues_a_tool_call_without_the_models_thinking_block() -> None:
    """Anthropic requires the thinking block in front of a continued call only with a budget; in
    adaptive mode no assistant turn has to begin with one. So an agent loop on a current Claude,
    which thinks adaptively, is sent — the refusal is for the budget shape alone."""
    body = _body(_continuing(thinking=Thinking(mode="high")))

    assert body["thinking"] == {"type": "adaptive"}
    with pytest.raises(DialectUnsupported, match="thinking budget"):
        _body(_continuing(thinking=Thinking(mode=ThinkingMode.LIMITED, tokens=2048)))


# == content ======================================================================================


def test_an_empty_text_block_is_left_out() -> None:
    request = CanonicalRequest(
        model="claude",
        messages=[
            CanonicalMessage(
                role=Role.USER, parts=[TextPart(text="  \n"), TextPart(text="question")]
            )
        ],
    )

    assert _body(request)["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "question"}]}
    ]


def test_a_turn_with_nothing_left_to_say_is_left_out() -> None:
    """A model turn that was only its thoughts reaches here empty; Anthropic refuses an empty
    turn, and joins two user turns either side of the gap itself."""
    request = CanonicalRequest(
        model="claude",
        messages=[
            CanonicalMessage(role=Role.USER, text="one"),
            CanonicalMessage(role=Role.MODEL, parts=[]),
            CanonicalMessage(role=Role.USER, text="two"),
        ],
    )

    assert [message["role"] for message in _body(request)["messages"]] == ["user", "user"]


def _with(media_type: str, data: bytes) -> CanonicalRequest:
    return CanonicalRequest(
        model="claude",
        messages=[
            CanonicalMessage(
                role=Role.USER,
                parts=[DataPart(media_type=media_type, data=data), TextPart(text="?")],
            )
        ],
    )


def test_each_media_type_goes_in_the_block_anthropic_reads_it_from() -> None:
    gif = _body(_with("image/gif", b"GIF89a"))["messages"][0]["content"][0]
    pdf = _body(_with("application/pdf", b"%PDF-1.7"))["messages"][0]["content"][0]
    text = _body(_with("text/plain", "grüße".encode()))["messages"][0]["content"][0]

    assert (gif["type"], gif["source"]["type"]) == ("image", "base64")
    assert (pdf["type"], pdf["source"]["media_type"]) == ("document", "application/pdf")
    assert text == {
        "type": "document",
        "source": {"type": "text", "media_type": "text/plain", "data": "grüße"},
    }


def test_a_media_type_with_no_block_is_refused_rather_than_sent_as_an_image() -> None:
    with pytest.raises(DialectUnsupported, match="audio/wav"):
        _body(_with("audio/wav", b"RIFF"))


def test_media_in_a_tool_result_goes_inside_the_result() -> None:
    request = _continuing().model_copy(
        update={
            "messages": [
                *_continuing().messages[:2],
                CanonicalMessage(
                    role=Role.USER,
                    parts=[
                        ToolResultPart(
                            call_id="toolu_1",
                            name="read",
                            content='{"ok": true}',
                            attachments=(DataPart(media_type="image/png", data=_PNG),),
                        )
                    ],
                ),
            ]
        }
    )

    result = _body(request)["messages"][2]["content"][0]

    assert result["type"] == "tool_result"
    assert result["content"][0] == {"type": "text", "text": '{"ok": true}'}
    assert result["content"][1]["type"] == "image"


# == tool choice ==================================================================================

_TOOLS = (ToolDeclaration(name="read"), ToolDeclaration(name="write"))


def _choosing(mode: ToolMode, *allowed: str, **over: Any) -> dict[str, Any]:
    return _body(_request(tools=_TOOLS, tool_choice=ToolChoice(mode=mode, allowed=allowed), **over))


def test_each_mode_is_anthropics_tool_choice() -> None:
    assert "tool_choice" not in _body(_request(tools=_TOOLS))
    assert "tool_choice" not in _choosing(ToolMode.AUTO)
    assert _choosing(ToolMode.NONE)["tool_choice"] == {"type": "none"}
    assert _choosing(ToolMode.ANY)["tool_choice"] == {"type": "any"}
    assert _choosing(ToolMode.ANY, "write")["tool_choice"] == {"type": "tool", "name": "write"}


def test_a_required_call_among_some_offers_only_those() -> None:
    """Anthropic has no list of allowed names, so the functions offered are the list."""
    body = _choosing(ToolMode.ANY, "read", "write")

    assert body["tool_choice"] == {"type": "any"}
    assert [tool["name"] for tool in body["tools"]] == ["read", "write"]
    narrowed = _body(
        _request(
            tools=(*_TOOLS, ToolDeclaration(name="delete")),
            tool_choice=ToolChoice(mode=ToolMode.ANY, allowed=("read", "write")),
        )
    )
    assert [tool["name"] for tool in narrowed["tools"]] == ["read", "write"]


def test_what_anthropic_cannot_say_is_refused_by_name() -> None:
    with pytest.raises(DialectUnsupported, match="VALIDATED"):
        _choosing(ToolMode.VALIDATED)
    # A required call with a thinking budget is Anthropic's own 400.
    with pytest.raises(DialectUnsupported, match="thinking budget"):
        _choosing(ToolMode.ANY, thinking=Thinking(mode=ThinkingMode.LIMITED, tokens=2048))


# == schemas ======================================================================================


def _schema(**node: Any) -> Any:
    return parse({"type": "OBJECT", "properties": {"x": node}, "required": ["x"]})


def test_a_format_that_states_no_constraint_is_left_out() -> None:
    """`int32` is the width of a number and `enum` restates the values beside it; Anthropic refuses
    every format outside its ten, and neither says anything the answer must satisfy."""
    width = _schema(type="INTEGER", format="int32")
    listed = _schema(type="STRING", format="enum", enum=["a", "b"])

    assert schema_refusal(width) is None
    assert "format" not in schema_for_anthropic(width)["properties"]["x"]
    assert schema_for_anthropic(listed)["properties"]["x"] == {"type": "string", "enum": ["a", "b"]}


def test_a_format_anthropic_knows_is_kept() -> None:
    dated = _schema(type="STRING", format="date-time")

    assert schema_refusal(dated) is None
    assert schema_for_anthropic(dated)["properties"]["x"]["format"] == "date-time"


@pytest.mark.parametrize(
    ("node", "named"),
    [
        ({"type": "STRING", "format": "byte"}, "byte"),
        ({"type": "ARRAY", "items": {"type": "STRING"}, "minItems": 2}, "min_items of 2"),
        ({"type": "OBJECT", "properties": {}, "minProperties": 1}, "min_properties"),
    ],
)
def test_a_constraint_anthropic_cannot_take_is_named(node: dict[str, Any], named: str) -> None:
    refusal = schema_refusal(_schema(**node))

    assert refusal is not None
    assert named in refusal


@pytest.mark.parametrize("minimum", [0, 1])
def test_min_items_of_zero_or_one_is_carried(minimum: int) -> None:
    schema = _schema(type="ARRAY", items={"type": "STRING"}, minItems=minimum)

    assert schema_refusal(schema) is None
    assert schema_for_anthropic(schema)["properties"]["x"]["minItems"] == minimum


def test_a_truncated_or_refused_answer_keeps_its_reason_under_a_schema() -> None:
    """Without a document the answer is refused either way; the reason tells the caller whether to
    raise the cap, rephrase, or fix the schema."""
    for stop, expected in [
        ("max_tokens", FinishReason.MAX_TOKENS),
        ("refusal", FinishReason.REFUSAL),
        ("end_turn", FinishReason.SCHEMA_UNSATISFIED),
    ]:
        data = {"content": [{"type": "text", "text": "not json"}], "stop_reason": stop}
        assert anthropic_to_canonical(data, "claude", structured=True).finish_reason is expected


# == usage and price ==============================================================================

_USAGE = {
    "input_tokens": 100,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 1000,
    "cache_creation": {"ephemeral_5m_input_tokens": 400, "ephemeral_1h_input_tokens": 600},
    "output_tokens": 50,
    "output_tokens_details": {"thinking_tokens": 30},
}


def test_the_hours_writes_and_the_thinking_are_read_from_the_usage() -> None:
    usage = usage_of(_USAGE)

    assert (usage.cache_write_tokens, usage.long_cache_write_tokens) == (1000, 600)
    assert (usage.completion_tokens, usage.reasoning_tokens) == (50, 30)


def test_a_stream_reports_them_too() -> None:
    assembler = StreamAssembler()
    assembler.feed({"type": "message_start", "message": {"usage": _USAGE}})
    assembler.feed({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}})
    final = assembler.feed({"type": "message_stop"})

    assert final is not None and final.usage is not None
    assert (final.usage.long_cache_write_tokens, final.usage.reasoning_tokens) == (600, 30)


async def test_an_hours_write_is_priced_at_twice_the_input() -> None:
    price = Price(
        input_per_million_nanos=1_000_000,
        output_per_million_nanos=0,
        cache_write_per_million_nanos=1_250_000,
    )
    usage = CanonicalUsage(
        prompt_tokens=2_000_000,
        completion_tokens=0,
        cache_write_tokens=2_000_000,
        long_cache_write_tokens=1_000_000,
    )
    service = PricingService.__new__(PricingService)

    async def price_for(model: str) -> Price:
        return price

    service.price_for = price_for  # type: ignore[method-assign]

    # A million at 1.25x and a million at 2x.
    assert await service.cost_nanos("claude", usage) == 1_250_000 + 2_000_000


# == the stream's reasoning =======================================================================


def _thinking_stream(*, include_reasoning: bool) -> list[Any]:
    assembler = StreamAssembler(include_reasoning=include_reasoning)
    events = [
        {"type": "content_block_start", "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hm"}},
        {"type": "content_block_stop"},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "yes"}},
    ]
    return [chunk for event in events if (chunk := assembler.feed(event)) is not None]


def test_a_streamed_thought_reaches_a_use_case_that_wants_it_and_no_other() -> None:
    shown = _thinking_stream(include_reasoning=True)
    withheld = _thinking_stream(include_reasoning=False)

    assert [(chunk.reasoning_delta, chunk.text_delta) for chunk in shown] == [
        ("hm", ""),
        ("", "yes"),
    ]
    assert [(chunk.reasoning_delta, chunk.text_delta) for chunk in withheld] == [("", "yes")]
