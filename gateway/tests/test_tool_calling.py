"""Tool calling end to end through the Gemini surface and the OpenAI dialect (`FRD-131`).

Two properties run through everything here. **Nothing is executed** — the gateway is a courier for
a declaration one way and a request-to-run the other. And **nothing is silently dropped**: a turn
that carries a tool result must reach the model, because a conversation missing its middle answers
a different question than the one asked.
"""

from __future__ import annotations

import asyncio
import json

from aira_gateway.api.gemini import schemas
from aira_gateway.api.gemini.mapping import canonical_to_gemini, gemini_to_canonical
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
    ToolCallPart,
    ToolDeclaration,
    ToolResultPart,
)
from aira_gateway.core.schema import parse
from aira_gateway.upstreams.openai.mapping import (
    StreamedToolCalls,
    canonical_to_openai,
    openai_to_canonical,
)

DECLARATION = {
    "name": "read_file",
    "description": "Read a file.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
}


def _gemini_request(**extra: object) -> schemas.GenerateContentRequest:
    body: dict[str, object] = {"contents": [{"role": "user", "parts": [{"text": "read hello.py"}]}]}
    body.update(extra)
    return schemas.GenerateContentRequest.model_validate(body)


# ---- the Gemini surface, in ------------------------------------------------------------------


def test_a_declaration_reaches_the_canonical_request() -> None:
    request = gemini_to_canonical(
        "m", _gemini_request(tools=[{"functionDeclarations": [DECLARATION]}])
    )

    assert [tool.name for tool in request.tools] == ["read_file"]
    assert request.tools[0].parameters is not None


def test_a_declaration_is_parsed_with_the_schema_parser() -> None:
    """The same parser, bounds and error vocabulary a `responseSchema` gets: it is caller-supplied
    structure with caller-controlled recursion, arriving through another field."""
    request = gemini_to_canonical(
        "m", _gemini_request(tools=[{"functionDeclarations": [DECLARATION]}])
    )
    schema = request.tools[0].parameters
    assert schema is not None
    assert schema.properties is not None and "path" in schema.properties


def test_a_replayed_call_and_result_survive_the_round_trip() -> None:
    """The middle of every agent exchange. Before `FRD-131` both parts were refused, and the
    refusal was right — dropping them would have deleted a turn from the conversation."""
    request = gemini_to_canonical(
        "m",
        _gemini_request(
            contents=[
                {"role": "user", "parts": [{"text": "read hello.py"}]},
                {
                    "role": "model",
                    "parts": [{"functionCall": {"name": "read_file", "args": {"path": "a"}}}],
                },
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": "read_file",
                                "response": {"text": "print(1)"},
                            }
                        }
                    ],
                },
            ]
        ),
    )

    assert request.messages[1].tool_calls[0].arguments == {"path": "a"}
    assert "print(1)" in request.messages[2].tool_results[0].content


def test_a_call_gets_an_id_even_though_google_sends_none() -> None:
    """Google matches a result to a call by name; the other two dialects require an id. Without
    one generated here, a conversation begun on this surface could not be continued anywhere."""
    request = gemini_to_canonical(
        "m",
        _gemini_request(
            contents=[
                {"role": "model", "parts": [{"functionCall": {"name": "read_file", "args": {}}}]}
            ]
        ),
    )

    assert request.messages[0].tool_calls[0].id


# ---- the Gemini surface, out -----------------------------------------------------------------


def test_a_tool_call_is_rendered_as_a_function_call_part() -> None:
    response = canonical_to_gemini(
        CanonicalResponse(
            model="m",
            text="",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
            tool_calls=(ToolCallPart(id="c1", name="read_file", arguments={"path": "a"}),),
        )
    )

    part = response.candidates[0].content.parts[0]
    assert part.functionCall is not None
    assert part.functionCall.name == "read_file"
    assert part.functionCall.args == {"path": "a"}


def test_text_and_a_call_in_one_answer_keep_their_order() -> None:
    response = canonical_to_gemini(
        CanonicalResponse(
            model="m",
            text="I will read it.",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
            tool_calls=(ToolCallPart(id="c1", name="read_file", arguments={}),),
        )
    )

    parts = response.candidates[0].content.parts
    assert parts[0].text == "I will read it."
    assert parts[1].functionCall is not None


def test_an_ordinary_answer_is_shaped_exactly_as_before() -> None:
    """The regression guard: every response that existed before this feature must be byte-for-byte
    what it was."""
    response = canonical_to_gemini(
        CanonicalResponse(
            model="m", text="hi", usage=CanonicalUsage(prompt_tokens=1, completion_tokens=2)
        )
    )

    parts = response.candidates[0].content.parts
    assert len(parts) == 1
    assert parts[0].text == "hi"
    assert parts[0].functionCall is None


# ---- the OpenAI dialect ----------------------------------------------------------------------


def test_declarations_become_this_dialects_tools() -> None:
    body = canonical_to_openai(
        CanonicalRequest(
            model="m",
            messages=[CanonicalMessage(role=Role.USER, text="hi")],
            tools=(ToolDeclaration(name="read_file", parameters=parse({"type": "object"})),),
        )
    )

    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "read_file"


def test_a_request_without_tools_carries_no_tools_key() -> None:
    """An absent field and an empty list are different requests to several implementations."""
    body = canonical_to_openai(
        CanonicalRequest(model="m", messages=[CanonicalMessage(role=Role.USER, text="hi")])
    )

    assert "tools" not in body


def test_a_tool_result_becomes_its_own_message_with_the_tool_role() -> None:
    """One canonical turn is not one wire message here: this API carries each result as a message
    of its own, keyed to the call it answers."""
    body = canonical_to_openai(
        CanonicalRequest(
            model="m",
            messages=[
                CanonicalMessage(role=Role.USER, text="read it"),
                CanonicalMessage(
                    role=Role.MODEL,
                    parts=[ToolCallPart(id="c1", name="read_file", arguments={"path": "a"})],
                ),
                CanonicalMessage(
                    role=Role.USER,
                    parts=[ToolResultPart(call_id="c1", name="read_file", content="print(1)")],
                ),
            ],
        )
    )

    assert body["messages"][1]["tool_calls"][0]["id"] == "c1"
    # Arguments travel as a JSON *string* in this dialect, not as an object.
    assert json.loads(body["messages"][1]["tool_calls"][0]["function"]["arguments"]) == {
        "path": "a"
    }
    assert body["messages"][2] == {"role": "tool", "tool_call_id": "c1", "content": "print(1)"}


def test_a_returned_call_has_its_arguments_parsed() -> None:
    response = openai_to_canonical(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "hello.py"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        },
        "m",
    )

    assert response.tool_calls[0].arguments == {"path": "hello.py"}
    assert response.finish_reason == "tool_use"


def test_unparseable_arguments_keep_the_name_rather_than_failing_the_request() -> None:
    """A model occasionally produces arguments that are not valid JSON. The caller can see *that*
    it asked for `read_file` and decide; a 502 would hide the model's mistake behind ours."""
    response = openai_to_canonical(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "read_file", "arguments": "{oops"}}
                        ]
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        "m",
    )

    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments == {}


# ---- the streaming trap, which is why this class exists ---------------------------------------


def test_arguments_split_across_deltas_are_reassembled() -> None:
    """**The trap `FRD-131` named before anything was built.** A streamed tool call arrives in
    pieces: the name once, then the arguments as string fragments. A mapper that forwarded each
    delta would emit several half-formed calls, none of them parseable."""
    calls = StreamedToolCalls()
    calls.add([{"index": 0, "id": "c1", "function": {"name": "read_file", "arguments": ""}}])
    calls.add([{"index": 0, "function": {"arguments": '{"pa'}}])
    calls.add([{"index": 0, "function": {"arguments": 'th": "he'}}])
    calls.add([{"index": 0, "function": {"arguments": 'llo.py"}'}}])

    finished = calls.finish()

    assert len(finished) == 1
    assert finished[0].arguments == {"path": "hello.py"}


def test_two_calls_in_one_stream_are_kept_apart_by_index() -> None:
    """`index` is the only key on every delta — `id` and `name` arrive once. Accumulating by
    anything else would merge two calls into one."""
    calls = StreamedToolCalls()
    calls.add(
        [{"index": 0, "id": "a", "function": {"name": "read_file", "arguments": '{"p":"1"}'}}]
    )
    calls.add(
        [{"index": 1, "id": "b", "function": {"name": "read_file", "arguments": '{"p":"2"}'}}]
    )

    finished = calls.finish()

    assert [call.arguments["p"] for call in finished] == ["1", "2"]


def test_a_stream_carrying_no_calls_accumulates_nothing() -> None:
    calls = StreamedToolCalls()
    calls.add(None)
    calls.add([])

    assert not calls.pending
    assert calls.finish() == ()


def test_a_fragment_with_no_name_is_dropped_rather_than_guessed_at() -> None:
    """Half a function call is not a smaller function call, it is a different one."""
    calls = StreamedToolCalls()
    calls.add([{"index": 0, "function": {"arguments": '{"path":'}}])

    assert calls.finish() == ()


# ---- the toggle: least privilege is the default, not a setting somebody remembers --------------


def _app():
    from aira_gateway.app import create_app
    from aira_gateway.config import GatewaySettings

    return create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))


async def _use_case(app, slug: str, *, tools_enabled: bool) -> None:
    from aira_gateway.db.models import UseCaseRead

    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug=slug, name=slug, tools_enabled=tools_enabled))
        await session.commit()


async def _declare_tools(app, model: str = "mock-1") -> None:
    """The catalog decides, not the adapter (`FRD-114`). Undeclared means unsupported, so a model
    that can do tool calling still has to *say* so before the chain will send it one."""
    from aira_gateway.db.models import ModelRead

    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=model, capabilities=["generate", "tools"]))
        await session.commit()


BODY = {
    "contents": [{"parts": [{"text": "read hello.py"}]}],
    "tools": [{"functionDeclarations": [DECLARATION]}],
}


async def test_a_use_case_that_has_not_enabled_tools_is_refused_by_name() -> None:
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "plain-uc", tools_enabled=False)
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=BODY,
            headers={"x-aira-use-case": "plain-uc"},
        )

    assert response.status_code == 400
    body = response.json()["error"]
    assert body["status"] == "FAILED_PRECONDITION"
    # Names the use case *and* who can change it — a refusal nobody can act on is a wall.
    assert "plain-uc" in body["message"]
    assert "administrator" in body["message"]


async def test_a_use_case_that_has_enabled_them_gets_through() -> None:
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "agent-uc", tools_enabled=True)
        await _declare_tools(app)
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=BODY,
            headers={"x-aira-use-case": "agent-uc"},
        )

    assert response.status_code == 200, response.text


async def test_an_unknown_use_case_is_refused_rather_than_assumed_permissive() -> None:
    """Absence of a row is absence of permission. A read-model that has not caught up yet must not
    be read as consent."""
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=BODY,
            headers={"x-aira-use-case": "never-heard-of-it"},
        )

    assert response.status_code == 400


async def test_a_request_without_tools_never_reads_the_use_case_row() -> None:
    """The ordinary request pays nothing for a capability it does not use — no use case configured
    at all, and it still succeeds exactly as before."""
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": [{"parts": [{"text": "hi"}]}]},
            headers={"x-aira-use-case": "no-such-use-case"},
        )

    assert response.status_code == 200, response.text


# ---- the capability: a fallback must not answer without tools ---------------------------------


async def test_a_model_that_does_not_declare_tools_is_refused_by_name() -> None:
    """Undeclared means unsupported (`ADR-0012`). The alternative is a model answering in prose to
    a client whose entire loop is built on parsing a function call."""
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "agent-uc2", tools_enabled=True)
        # No catalog row at all: the model is undeclared, which is not permission.
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=BODY,
            headers={"x-aira-use-case": "agent-uc2"},
        )

    assert response.status_code == 400
    assert "does not declare tool calling" in response.text
    assert "mock-1" in response.text


async def test_the_mock_answers_a_tool_request_with_a_call() -> None:
    """The mock honours what it is given, or tool calling would only ever be exercised against a
    model nobody has in CI — the state `FRD-110` refused to leave attachments in."""
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "agent-uc3", tools_enabled=True)
        await _declare_tools(app)
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=BODY,
            headers={"x-aira-use-case": "agent-uc3"},
        )

    assert response.status_code == 200, response.text
    part = response.json()["candidates"][0]["content"]["parts"][0]
    assert part["functionCall"]["name"] == "read_file"


async def test_a_second_turn_carrying_the_result_gets_prose() -> None:
    """The exchange has to be able to *end*. A mock that always asked for another call would loop
    forever and no test could assert on the outcome."""
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "agent-uc4", tools_enabled=True)
        await _declare_tools(app)
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json={
                **BODY,
                "contents": [
                    {"role": "user", "parts": [{"text": "read hello.py"}]},
                    {
                        "role": "model",
                        "parts": [{"functionCall": {"name": "read_file", "args": {"path": "a"}}}],
                    },
                    {
                        "role": "user",
                        "parts": [
                            {"functionResponse": {"name": "read_file", "response": {"t": "print"}}}
                        ],
                    },
                ],
            },
            headers={"x-aira-use-case": "agent-uc4"},
        )

    assert response.status_code == 200, response.text
    assert "acted on the tool result" in response.text


# ---- the audit row (`FRD-131` FR-7) -----------------------------------------------------------
#
# Found live, after stages 1-4 were "done": a real assistant turn stored `{"text": ""}` and nothing
# else. A streamed tool call has **no text delta** to accumulate — the answer *is* the call — so a
# row built from the accumulated text alone says nothing about what the model asked to have run.
# For a client that streams, which is every coding assistant, that is the whole audit trail of the
# feature missing.


async def _rows(app) -> list:
    from sqlalchemy import select

    from aira_gateway.db.models import RequestLog

    async with app.state.db_sessionmaker() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.created_at))
        return list(result.scalars())


async def test_a_buffered_tool_call_is_on_the_audit_row() -> None:
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "audit-uc", tools_enabled=True)
        await _declare_tools(app)
        assert (
            client.post(
                "/v1beta/models/mock-1:generateContent",
                json=BODY,
                headers={"x-aira-use-case": "audit-uc"},
            ).status_code
            == 200
        )
        rows = await _rows(app)

    assert rows[-1].tool_calls == {"declared": 1, "called": ["read_file"]}


async def test_a_streamed_tool_call_is_on_the_audit_row_too() -> None:
    """**The one that was missing.** Same fact, other exit — the property `FRD-126` exists for."""
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "audit-uc2", tools_enabled=True)
        await _declare_tools(app)
        response = client.post(
            "/v1beta/models/mock-1:streamGenerateContent?alt=sse",
            json=BODY,
            headers={"x-aira-use-case": "audit-uc2"},
        )
        assert response.status_code == 200, response.text
        rows = await _rows(app)

    assert rows[-1].tool_calls == {"declared": 1, "called": ["read_file"]}


async def test_a_streamed_tool_call_reaches_the_client() -> None:
    """Recording it is not the same as delivering it. Without the chunk mapper carrying the call,
    a client would receive the answer's tokens and never the call — which for an assistant is the
    entire answer."""
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "audit-uc3", tools_enabled=True)
        await _declare_tools(app)
        response = client.post(
            "/v1beta/models/mock-1:streamGenerateContent?alt=sse",
            json=BODY,
            headers={"x-aira-use-case": "audit-uc3"},
        )

    assert "functionCall" in response.text
    assert "read_file" in response.text


async def test_a_request_that_declared_tools_and_got_none_says_so() -> None:
    """ "Offered ten functions and asked for none" and "offered none" are different events, and
    only one of them is a model behaving oddly. Both are recorded."""
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "audit-uc4", tools_enabled=True)
        await _declare_tools(app)
        client.post(
            "/v1beta/models/mock-1:generateContent",
            json={
                **BODY,
                "contents": [
                    {"role": "user", "parts": [{"text": "read it"}]},
                    {
                        "role": "model",
                        "parts": [{"functionCall": {"name": "read_file", "args": {}}}],
                    },
                    {
                        "role": "user",
                        "parts": [{"functionResponse": {"name": "read_file", "response": {}}}],
                    },
                ],
            },
            headers={"x-aira-use-case": "audit-uc4"},
        )
        rows = await _rows(app)

    assert rows[-1].tool_calls == {"declared": 1, "called": []}


async def test_an_ordinary_request_records_nothing_about_tools() -> None:
    """A column that is never NULL stops being evidence of anything."""
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        client.post(
            "/v1beta/models/mock-1:generateContent",
            json={"contents": [{"parts": [{"text": "hi"}]}]},
        )
        rows = await _rows(app)

    assert rows[-1].tool_calls is None


async def test_arguments_are_never_recorded_in_the_metadata_column() -> None:
    """Arguments are caller content: they belong under `store_payloads`, inside the retention clock
    and behind `FRD-406`'s redaction — not in a column no clock covers."""
    from fastapi.testclient import TestClient

    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "audit-uc5", tools_enabled=True)
        await _declare_tools(app)
        client.post(
            "/v1beta/models/mock-1:generateContent",
            json={
                **BODY,
                "contents": [{"parts": [{"text": "secret-argument-value"}]}],
            },
            headers={"x-aira-use-case": "audit-uc5"},
        )
        rows = await _rows(app)

    assert "secret-argument-value" not in str(rows[-1].tool_calls)


# ---- the other two dialects (`FRD-131` FR-5) --------------------------------------------------
#
# One capability, three wire formats. The OpenAI dialect is above; these are the two that reach
# Vertex, and the Anthropic one is where the interesting collision lives: structured output on that
# dialect **is** a forced tool call, so the same field would have to serve two purposes.


def _tool_request(**extra: object) -> CanonicalRequest:
    values: dict[str, object] = {
        "model": "m",
        "messages": [CanonicalMessage(role=Role.USER, text="read hello.py")],
        "tools": (ToolDeclaration(name="read_file", parameters=parse({"type": "object"})),),
    }
    values.update(extra)
    return CanonicalRequest(**values)  # type: ignore[arg-type]


# -- Gemini as an upstream ---------------------------------------------------------------------


def test_the_gemini_upstream_sends_function_declarations() -> None:
    from aira_gateway.upstreams.gemini_mapping import canonical_to_gemini_request

    body = canonical_to_gemini_request(_tool_request())

    assert body["tools"][0]["functionDeclarations"][0]["name"] == "read_file"


def test_the_gemini_upstream_reads_a_call_back() -> None:
    from aira_gateway.upstreams.gemini_mapping import gemini_response_to_canonical

    response = gemini_response_to_canonical(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"functionCall": {"name": "read_file", "args": {"path": "a"}}}]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2},
        },
        "m",
    )

    assert response.tool_calls[0].name == "read_file"
    # Google sends no id; one is generated so the other two dialects, which require one, can serve
    # the next turn of the same conversation.
    assert response.tool_calls[0].id


def test_a_tool_result_becomes_an_object_for_google() -> None:
    """`functionResponse.response` is an **object** on this wire format. The canonical model keeps
    the result as text because two of three dialects want one, so it is parsed back here — and a
    non-JSON result is wrapped rather than rejected."""
    from aira_gateway.upstreams.gemini_mapping import canonical_to_gemini_request

    body = canonical_to_gemini_request(
        _tool_request(
            messages=[
                CanonicalMessage(
                    role=Role.USER,
                    parts=[ToolResultPart(call_id="c1", name="read_file", content="plain text")],
                )
            ]
        )
    )

    assert body["contents"][0]["parts"][0]["functionResponse"]["response"] == {
        "result": "plain text"
    }


def test_the_gemini_upstream_carries_a_call_on_a_stream_chunk() -> None:
    """Whole, in one chunk — this wire format has no fragmentation, and inventing an accumulator
    for it would be a mechanism defending against a problem it does not have."""
    from aira_gateway.upstreams.gemini_mapping import gemini_chunk_to_canonical

    chunk = gemini_chunk_to_canonical(
        {
            "candidates": [
                {"content": {"parts": [{"functionCall": {"name": "read_file", "args": {}}}]}}
            ]
        }
    )

    assert chunk.tool_calls[0].name == "read_file"


# -- Anthropic ---------------------------------------------------------------------------------


def test_anthropic_sends_the_callers_tools() -> None:
    from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

    body = canonical_to_anthropic(_tool_request(), max_tokens=100)

    assert body["tools"][0]["name"] == "read_file"
    assert "input_schema" in body["tools"][0]
    # The model decides. Pinning `tool_choice` here would invent an instruction the caller never
    # gave — the surface only accepts `AUTO`.
    assert "tool_choice" not in body


def test_anthropic_carries_a_call_and_its_result_as_blocks() -> None:
    from aira_gateway.upstreams.vertex.anthropic_mapping import canonical_to_anthropic

    body = canonical_to_anthropic(
        _tool_request(
            messages=[
                CanonicalMessage(
                    role=Role.MODEL,
                    parts=[ToolCallPart(id="c1", name="read_file", arguments={"path": "a"})],
                ),
                CanonicalMessage(
                    role=Role.USER,
                    parts=[ToolResultPart(call_id="c1", name="read_file", content="print(1)")],
                ),
            ]
        ),
        max_tokens=100,
    )

    blocks = [block for message in body["messages"] for block in message["content"]]
    assert {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a"}} in blocks
    assert {"type": "tool_result", "tool_use_id": "c1", "content": "print(1)"} in blocks


def test_anthropic_reports_every_tool_use_block_as_a_call() -> None:
    """Simplified on 2026-08-08. There used to be a block to filter out — the structured-output
    tool — and the document is a text block now, so every `tool_use` block in a response is
    something the caller declared."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import anthropic_to_canonical

    data = {
        "content": [{"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a"}}],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1, "output_tokens": 2},
    }

    assert [call.name for call in anthropic_to_canonical(data, "m").tool_calls] == ["read_file"]
    # And a structured request reports it too: the model calling a function *instead of* answering
    # is a legitimate outcome the caller has to be able to see.
    assert [
        call.name for call in anthropic_to_canonical(data, "m", structured=True).tool_calls
    ] == ["read_file"]


def test_anthropic_reassembles_a_streamed_call() -> None:
    """`input_json_delta` means two different things on this dialect, and only `content_block_start`
    says which. For the caller's own tool the fragments are arguments and must be accumulated —
    emitting them as text would send `{"pa`, `th": "he` to the client as the model's reply."""
    from aira_gateway.upstreams.vertex.anthropic_mapping import StreamAssembler

    assembler = StreamAssembler()
    assembler.feed({"type": "message_start", "message": {"usage": {"input_tokens": 5}}})
    assembler.feed(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "id": "c1", "name": "read_file"},
        }
    )
    for fragment in ('{"pa', 'th": "he', 'llo.py"}'):
        emitted = assembler.feed(
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": fragment},
            }
        )
        assert emitted is None, "argument fragments must never be streamed as text"
    assembler.feed({"type": "content_block_stop"})
    final = assembler.feed({"type": "message_stop"})

    assert final is not None
    assert final.tool_calls[0].arguments == {"path": "hello.py"}


def test_a_schema_this_dialect_cannot_express_skips_the_candidate() -> None:
    """What replaced `ToolsAndSchemaTogether` when the provider gained a schema parameter: the
    conflict between tools and a schema is gone, and what remains is that this dialect's schema
    vocabulary is **narrower** than the one our surface accepts."""
    from aira_gateway.core.schema import parse as parse_schema
    from aira_gateway.requirements import SchemaExpressible
    from aira_gateway.upstreams.vertex.anthropic_mapping import schema_refusal

    class _Registry:
        def provider_for(self, model: str):  # noqa: ANN202, ARG002
            return type("P", (), {"schema_refusal": staticmethod(schema_refusal)})()

    constrained = parse_schema({"type": "STRING", "pattern": "^x+$"})
    refusal = asyncio.run(SchemaExpressible(_Registry(), constrained).refusal("claude"))

    assert refusal is not None
    assert "pattern" in refusal
