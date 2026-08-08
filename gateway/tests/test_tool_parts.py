"""Tool declarations and calls in the canonical core (`FRD-131` stage 1).

Half of these tests are about the new capability and half are about **the old one still working**.
`FRD-110` made the same kind of change — ordered parts where there had been one string — and it
passed the whole existing suite unmodified because `text=` still constructed and `.text` still
read. The same bar applies here, and the cases below say so explicitly rather than trusting that
a green suite means it.
"""

from __future__ import annotations

from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    DataPart,
    Role,
    TextPart,
    ToolCallPart,
    ToolDeclaration,
    ToolResultPart,
)
from aira_gateway.core.schema import parse

PARAMS = parse({"type": "object", "properties": {"path": {"type": "string"}}})


def _request(**overrides: object) -> CanonicalRequest:
    values: dict[str, object] = {
        "model": "m",
        "messages": [CanonicalMessage(role=Role.USER, text="read hello.py")],
    }
    values.update(overrides)
    return CanonicalRequest(**values)  # type: ignore[arg-type]


# ---- what still has to be true --------------------------------------------------------------


def test_a_request_that_declares_nothing_is_unchanged() -> None:
    """The ordinary case, which is every request made before this feature existed."""
    request = _request()

    assert request.tools == ()
    assert request.messages[0].text == "read hello.py"
    assert request.messages[0].tool_calls == []


def test_text_still_ignores_the_new_parts() -> None:
    """`.text` is what the injection filter and the routing classifier read. It has always meant
    "what does this message say", and a tool call is not something the caller said."""
    message = CanonicalMessage(
        role=Role.MODEL,
        parts=[
            TextPart(text="I will read it."),
            ToolCallPart(id="c1", name="read_file", arguments={"path": "hello.py"}),
        ],
    )

    assert message.text == "I will read it."


def test_attachments_are_unaffected() -> None:
    message = CanonicalMessage(
        role=Role.USER,
        parts=[
            DataPart(media_type="application/pdf", data=b"%PDF-"),
            ToolResultPart(call_id="c1", name="read_file", content="print(1)"),
        ],
    )

    assert len(message.attachments) == 1
    assert len(message.tool_results) == 1


def test_an_actually_empty_request_is_still_empty() -> None:
    """`FRD-113` FR-7's no-op-billing refusal must not be weakened by this change."""
    assert _request(messages=[CanonicalMessage(role=Role.USER, text="   ")]).is_empty


# ---- the new capability ---------------------------------------------------------------------


def test_a_declaration_is_carried() -> None:
    request = _request(tools=(ToolDeclaration(name="read_file", parameters=PARAMS),))

    assert request.tools[0].name == "read_file"
    assert request.tools[0].parameters is not None


def test_a_turn_carrying_only_a_tool_result_is_not_empty() -> None:
    """The middle of every agent exchange: no prose at all, just what the tool returned. Judging
    that "asks nothing" would refuse the ordinary second turn — and it is exactly what the
    pre-`FRD-131` rule did."""
    request = _request(
        messages=[
            CanonicalMessage(role=Role.USER, text="read hello.py"),
            CanonicalMessage(
                role=Role.MODEL,
                parts=[ToolCallPart(id="c1", name="read_file", arguments={"path": "hello.py"})],
            ),
            CanonicalMessage(
                role=Role.USER,
                parts=[ToolResultPart(call_id="c1", name="read_file", content="print(1)")],
            ),
        ]
    )

    assert not request.is_empty


def test_several_calls_in_one_turn() -> None:
    """All three vendors can return more than one, so the shape is plural from the start rather
    than replaced the first time one does."""
    response = CanonicalResponse(
        model="m",
        text="",
        usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        tool_calls=(
            ToolCallPart(id="c1", name="read_file", arguments={"path": "a"}),
            ToolCallPart(id="c2", name="read_file", arguments={"path": "b"}),
        ),
    )

    assert [call.arguments["path"] for call in response.tool_calls] == ["a", "b"]


def test_arguments_are_parsed_structure_not_a_blob() -> None:
    """Every dialect sends either an object or a JSON string of one. Keeping both shapes would
    push that difference into every consumer."""
    call = ToolCallPart(id="c1", name="f", arguments={"n": 1})

    assert call.arguments["n"] == 1


def test_a_response_without_tool_calls_still_constructs() -> None:
    response = CanonicalResponse(
        model="m", text="hi", usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1)
    )

    assert response.tool_calls == ()
