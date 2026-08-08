"""Tool calling under every condition worth naming (`FRD-131`).

`test_tool_calling.py` proves the feature works. This proves what happens when it **does not** —
and it is organised by the two questions that actually matter rather than by whatever came to mind:

    where in the path        what is wrong with it
    ─────────────────────    ────────────────────────────────────────────────────────────
    A  the declaration       unusable, ambiguous, empty, unbounded
    B  the replayed turn     a call or result that does not fit the conversation
    C  the model's answer    malformed, undeclared, absent, mixed
    D  the stream            fragmented, truncated, interleaved, and the collision on
                             Anthropic where the same event means two different things
    E  governance            the toggle, the capability, the dialect conflict
    F  the audit row         every one of the above, seen from the evidence side

The organising principle behind the assertions: **a wrong answer must be impossible to mistake for
a right one.** A caller who receives prose where a function call was expected has no way to tell
that from a model choosing not to call — so wherever that could happen, something must refuse, by
name, and the audit row must say what was asked and what came back.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.upstreams.openai.mapping import StreamedToolCalls, openai_to_canonical

TOOL = {
    "name": "read_file",
    "description": "Read a file.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
}


def _app():
    return create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))


async def _use_case(app, slug: str, *, tools_enabled: bool = True) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug=slug, name=slug, tools_enabled=tools_enabled))
        await session.commit()


async def _declare(app, *capabilities: str) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model="mock-1", capabilities=list(capabilities)))
        await session.commit()


async def _rows(app) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.created_at))
        return list(result.scalars())


def _post(client: TestClient, body: dict[str, Any], slug: str = "uc") -> Any:
    return client.post(
        "/v1beta/models/mock-1:generateContent", json=body, headers={"x-aira-use-case": slug}
    )


def _body(**extra: Any) -> dict[str, Any]:
    return {
        "contents": [{"role": "user", "parts": [{"text": "read hello.py"}]}],
        "tools": [{"functionDeclarations": [TOOL]}],
        **extra,
    }


# =================================================================================================
# A. The declaration — what the caller offers
# =================================================================================================


@pytest.mark.parametrize(
    ("label", "tools", "fragment"),
    [
        (
            "a name nothing can call",
            [{"functionDeclarations": [{**TOOL, "name": ""}]}],
            "usable function name",
        ),
        (
            "a name no provider accepts",
            [{"functionDeclarations": [{**TOOL, "name": "my tool.v2"}]}],
            "usable function name",
        ),
        (
            "the same name twice",
            [{"functionDeclarations": [TOOL, {**TOOL, "description": "other"}]}],
            "declared twice",
        ),
        (
            "the same name across two tool objects",
            [{"functionDeclarations": [TOOL]}, {"functionDeclarations": [TOOL]}],
            "declared twice",
        ),
        (
            "parameters that are not a schema",
            [{"functionDeclarations": [{**TOOL, "parameters": {"type": "nonsense"}}]}],
            "",
        ),
    ],
)
async def test_an_unusable_declaration_is_refused_by_name(label, tools, fragment) -> None:
    """Each of these was accepted before this suite existed. A declaration the provider will
    reject, or that a returned call cannot be matched to, is better refused **here** — our message
    can name the field, theirs names nothing an operator can act on."""
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc")
        await _declare(app, "generate", "tools")
        response = _post(client, _body(tools=tools))

    assert response.status_code == 400, label
    if fragment:
        assert fragment in response.text, label


async def test_an_empty_tools_list_is_the_same_as_none() -> None:
    """A client that always sends the field must not be treated as declaring functions — otherwise
    the use-case gate refuses a request that asks for nothing."""
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc", tools_enabled=False)  # off, deliberately
        response = _post(client, _body(tools=[]))

    assert response.status_code == 200, response.text


async def test_a_tool_object_with_no_declarations_is_the_same_as_none() -> None:
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc", tools_enabled=False)
        response = _post(client, _body(tools=[{"functionDeclarations": []}]))

    assert response.status_code == 200, response.text


async def test_a_parameter_schema_is_bounded_like_any_other() -> None:
    """Caller-supplied structure with caller-controlled recursion, arriving through another field.
    The bounds `FRD-112` counts are the whole of our exposure to it."""
    deep: dict[str, Any] = {"type": "object", "properties": {"a": {"type": "string"}}}
    for _ in range(12):
        deep = {"type": "object", "properties": {"n": deep}}

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc")
        await _declare(app, "generate", "tools")
        response = _post(
            client, _body(tools=[{"functionDeclarations": [{**TOOL, "parameters": deep}]}])
        )

    assert response.status_code == 400
    assert "nests deeper" in response.text


async def test_a_function_without_parameters_is_accepted() -> None:
    """Not every function takes arguments, and refusing one that does not would be inventing a
    requirement no provider has."""
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc")
        await _declare(app, "generate", "tools")
        response = _post(
            client,
            _body(tools=[{"functionDeclarations": [{"name": "now", "description": "Time."}]}]),
        )

    assert response.status_code == 200, response.text


# =================================================================================================
# B. The replayed turn — a conversation carrying calls and results
# =================================================================================================


async def test_a_part_carrying_two_shapes_is_refused() -> None:
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc")
        response = _post(
            client,
            _body(
                contents=[
                    {
                        "role": "model",
                        "parts": [{"text": "a", "functionCall": {"name": "read_file"}}],
                    }
                ]
            ),
        )

    assert response.status_code == 400
    assert "exactly one of" in response.text


async def test_a_result_that_answers_no_call_is_carried_not_policed() -> None:
    """A deliberate non-decision. A caller who trimmed their history is replaying a legitimate
    conversation, and `ADR-0013` says the gateway governs model *access*, not the caller's
    conversation. The provider decides; we do not invent a rule neither of us can enforce."""
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc")
        await _declare(app, "generate", "tools")
        response = _post(
            client,
            _body(
                contents=[
                    {
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "read_file", "response": {}}}],
                    }
                ]
            ),
        )

    assert response.status_code == 200, response.text


async def test_a_turn_of_only_a_tool_result_is_not_an_empty_request() -> None:
    """The ordinary middle of an agent exchange. `FRD-113` FR-7 refuses a request that asks
    nothing; this asks plenty and carries no prose."""
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc")
        await _declare(app, "generate", "tools")
        response = _post(
            client,
            _body(
                contents=[
                    {
                        "role": "user",
                        "parts": [
                            {"functionResponse": {"name": "read_file", "response": {"t": "x"}}}
                        ],
                    }
                ]
            ),
        )

    assert response.status_code == 200, response.text


# =================================================================================================
# C. The model's answer — malformed, undeclared, absent
# =================================================================================================


@pytest.mark.parametrize(
    ("label", "arguments", "expected"),
    [
        ("well formed", '{"path": "a"}', {"path": "a"}),
        ("truncated JSON", '{"path": ', {}),
        ("not JSON at all", "oops", {}),
        ("a JSON array, not an object", "[1, 2]", {}),
        ("a bare string", '"hello"', {}),
        ("empty", "", {}),
        ("null", "null", {}),
    ],
)
def test_arguments_of_every_shape_keep_the_name(label, arguments, expected) -> None:
    """**The name always survives.** A model's malformed arguments are the model's mistake, and the
    caller can see *that* it asked for `read_file` and decide what to do. Raising instead would
    hide their fault behind ours and turn a recoverable turn into a 502."""
    response = openai_to_canonical(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "read_file", "arguments": arguments}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        "m",
    )

    assert response.tool_calls[0].name == "read_file", label
    assert response.tool_calls[0].arguments == expected, label


def test_a_call_with_no_name_is_dropped() -> None:
    """A call nobody can route is not a smaller call. Dropped rather than forwarded with an empty
    name, which a client would try to look up and fail on obscurely."""
    response = openai_to_canonical(
        {
            "choices": [
                {"message": {"tool_calls": [{"id": "c1", "function": {"arguments": "{}"}}]}}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        "m",
    )

    assert response.tool_calls == ()


def test_a_model_that_calls_something_undeclared_is_carried_and_recorded() -> None:
    """**Carried, not refused** — and the reasoning is `ADR-0013`. The model asking for a function
    nobody offered is a fact about the model; deciding it is out of order would be the gateway
    thinking for the use case. The client will reject it, and the audit row makes "the model asked
    for something nobody offered" an answerable question."""
    response = openai_to_canonical(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "rm_rf", "arguments": "{}"}}
                        ]
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        "m",
    )

    assert [call.name for call in response.tool_calls] == ["rm_rf"]


async def test_prose_when_a_call_was_possible_is_a_normal_answer() -> None:
    """A model choosing not to call is a real answer, not an error. What must never happen is that
    the caller cannot tell it apart from a dropped declaration — which is what the audit row is
    for (see F)."""
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc")
        await _declare(app, "generate", "tools")
        response = _post(
            client,
            _body(
                contents=[
                    {"role": "user", "parts": [{"text": "hi"}]},
                    {"role": "model", "parts": [{"functionCall": {"name": "read_file"}}]},
                    {
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "read_file", "response": {}}}],
                    },
                ]
            ),
        )

    assert response.status_code == 200
    # On the **parsed** answer, not on the response text: `Part` serialises all four shapes, so
    # `"functionCall"` appears in the body as a null field even when there is no call. A string
    # search here would have passed for the wrong reason.
    parts = response.json()["candidates"][0]["content"]["parts"]
    assert all(part.get("functionCall") is None for part in parts)
    assert any(part.get("text") for part in parts)


# =================================================================================================
# D. The stream — fragments, truncation, interleaving
# =================================================================================================


def test_fragments_arriving_one_character_at_a_time_still_reassemble() -> None:
    calls = StreamedToolCalls()
    calls.add([{"index": 0, "id": "c1", "function": {"name": "read_file"}}])
    for character in '{"path":"a"}':
        calls.add([{"index": 0, "function": {"arguments": character}}])

    assert calls.finish()[0].arguments == {"path": "a"}


def test_a_stream_that_stops_mid_arguments_yields_a_call_with_none() -> None:
    """Truncated rather than dropped: the name is the useful half, and it is what the audit row
    needs. The arguments are empty because half of them is not a smaller set of arguments."""
    calls = StreamedToolCalls()
    calls.add([{"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": '{"pa'}}])

    finished = calls.finish()

    assert finished[0].name == "read_file"
    assert finished[0].arguments == {}


def test_calls_interleaved_across_indices_do_not_merge() -> None:
    """A provider is free to advance two calls in one delta. Accumulating by anything but `index`
    would concatenate their arguments into one unparseable string."""
    calls = StreamedToolCalls()
    calls.add(
        [
            {"index": 0, "id": "a", "function": {"name": "read_file", "arguments": '{"p":'}},
            {"index": 1, "id": "b", "function": {"name": "write_file", "arguments": '{"q":'}},
        ]
    )
    calls.add(
        [
            {"index": 1, "function": {"arguments": '"2"}'}},
            {"index": 0, "function": {"arguments": '"1"}'}},
        ]
    )

    finished = calls.finish()

    assert [(call.name, call.arguments) for call in finished] == [
        ("read_file", {"p": "1"}),
        ("write_file", {"q": "2"}),
    ]


def test_finishing_twice_does_not_repeat_the_calls() -> None:
    """A stream whose finish reason arrives twice — a provider quirk that has happened — must not
    produce the same call to the caller twice. Executing a function twice is not idempotent."""
    calls = StreamedToolCalls()
    calls.add([{"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": "{}"}}])

    assert len(calls.finish()) == 1
    assert calls.finish() == ()


def test_anthropic_argument_fragments_are_never_streamed_as_text() -> None:
    """**Rewritten on 2026-08-08, and the rewrite is the news.** This case used to assert that
    `input_json_delta` was disambiguated by `content_block_start`, because the same event carried
    the structured document for one block and a call's arguments for another. The provider gained
    a first-class schema parameter, the document is a text block now, and the ambiguity is *gone*
    rather than handled — the test that guarded it is deleted rather than kept passing.

    What remains is the one meaning: argument fragments, which must be accumulated and never sent
    to the client as the model's reply."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import StreamAssembler

    assembler = StreamAssembler()
    assembler.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "id": "c1", "name": "read_file"},
        }
    )

    assert (
        assembler.feed(
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": '{"p":1}'},
            }
        )
        is None
    )


def test_a_structured_document_streams_as_ordinary_text() -> None:
    """And the other half: with the schema now a request parameter, the document arrives through
    `text_delta` like any other answer — no special case, which is why there is none left."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import StreamAssembler

    emitted = StreamAssembler().feed(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": '{"a":1}'}}
    )

    assert emitted is not None and emitted.text_delta == '{"a":1}'


def test_anthropic_closes_an_open_call_even_without_a_closing_event() -> None:
    """A provider that ends the message without `content_block_stop` would otherwise drop the last
    call entirely — the failure mode that is invisible because the answer still looks complete."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import StreamAssembler

    assembler = StreamAssembler()
    assembler.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "id": "c1", "name": "read_file"},
        }
    )
    assembler.feed(
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"p":1}'},
        }
    )
    final = assembler.feed({"type": "message_stop"})

    assert final is not None
    assert final.tool_calls[0].arguments == {"p": 1}


# =================================================================================================
# E. Governance — the toggle, the capability, the dialect
# =================================================================================================


@pytest.mark.parametrize(
    ("label", "tools_enabled", "capabilities", "fragment"),
    [
        ("the use case has not enabled it", False, ("generate", "tools"), "has not enabled"),
        ("the model does not declare it", True, ("generate",), "does not declare tool calling"),
    ],
)
async def test_a_refusal_names_what_is_missing_and_who_can_change_it(
    label, tools_enabled, capabilities, fragment
) -> None:
    """Two different refusals that must not be confused: one is a configuration somebody can
    change, the other is a fact about the model. Both are `FAILED_PRECONDITION` and both say
    which."""
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc", tools_enabled=tools_enabled)
        await _declare(app, *capabilities)
        response = _post(client, _body())

    assert response.status_code == 400, label
    assert response.json()["error"]["status"] == "FAILED_PRECONDITION", label
    assert fragment in response.text, label


async def test_a_request_with_no_use_case_cannot_declare_tools() -> None:
    """Tool calling is configured per use case, so a request naming none has nowhere to read the
    permission from — and defaulting to *allowed* would make the toggle decorative."""
    app = _app()
    with TestClient(app) as client:
        await _declare(app, "generate", "tools")
        response = client.post("/v1beta/models/mock-1:generateContent", json=_body())

    assert response.status_code == 400
    assert "names none" in response.text


# =================================================================================================
# F. The audit row — the same conditions, seen as evidence
# =================================================================================================


@pytest.mark.parametrize("verb", ["generateContent", "streamGenerateContent"])
async def test_both_transports_record_the_same_facts(verb: str) -> None:
    """The property `FRD-126` exists for, asserted as a comparison rather than twice — a fact
    recorded at one exit is a fact eventually missing from the other, and it *was*."""
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc")
        await _declare(app, "generate", "tools")
        client.post(
            f"/v1beta/models/mock-1:{verb}" + ("?alt=sse" if "stream" in verb else ""),
            json=_body(),
            headers={"x-aira-use-case": "uc"},
        )
        rows = await _rows(app)

    assert rows[-1].tool_calls == {"declared": 1, "called": ["read_file"]}


async def test_a_refused_request_records_the_refusal_not_a_tool_call() -> None:
    """`FRD-122`: the log records what was *asked*, not only what was served. A request refused
    for lack of permission is on the trail, and it did not call anything."""
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc", tools_enabled=False)
        await _declare(app, "generate", "tools")
        _post(client, _body())
        rows = await _rows(app)

    assert rows, "a refusal must leave a row"
    assert rows[-1].outcome != "served"
    assert not (rows[-1].tool_calls or {}).get("called")


async def test_the_row_distinguishes_offered_from_asked() -> None:
    """The distinction the column exists for. Without it, "the model ignored ten functions" and
    "nobody offered any" are the same row."""
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "uc")
        await _declare(app, "generate", "tools")
        # No tools at all.
        client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": [{"parts": [{"text": "hi"}]}]},
            headers={"x-aira-use-case": "uc"},
        )
        rows = await _rows(app)

    assert rows[-1].tool_calls is None
