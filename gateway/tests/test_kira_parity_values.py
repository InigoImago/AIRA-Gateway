"""The predecessor's values where this surface answered with its own (`FRD-146` FR-11 to FR-19).

Each was found by sending the same request to both and comparing: the field existed on both sides
and said something different. So each test pins the predecessor's value, not merely a value.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.api.kira import schemas
from aira_gateway.api.kira.keepalive import PING, TICK, with_pings
from aira_gateway.api.kira.mapping import thinking_of, to_canonical, to_chat_response
from aira_gateway.api.kira.refusals import refusal_response
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage, DataPart
from aira_gateway.db.models import ModelRead
from aira_gateway.upstreams.base import UpstreamError

BASE = "/kira/api/external"
PDF = b"%PDF-1.7\n" + b"x" * 200


def _app(**settings: Any):  # noqa: ANN201
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0, **settings))


async def _catalogue(app, model: str = "mock-1", **fields: Any) -> None:  # noqa: ANN001
    defaults: dict[str, Any] = {
        "numeric_id": 1004,
        "capabilities": ["generate", "embed"],
        "publisher": "google",
    }
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=model, **{**defaults, **fields}))
        await session.commit()


def _chat(**over: Any) -> dict[str, Any]:
    return {"request": {"parts": [{"text": "Hallo"}]}, "model_id": 1004, **over}


# == /models (FR-11, FR-12) =======================================================================


async def test_models_lists_levels_beside_modes_and_always_a_default() -> None:
    """A model with level words had no `thinkingConfig` at all, so a client concluded it could not
    think. The predecessor lists modes and levels in one array, names a default, and writes an
    undeclared bound as `null`."""
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "thinking"],
            thinking={"modes": ["disabled", "auto"], "levels": ["high", "medium", "low"]},
        )
        (listed,) = client.get(f"{BASE}/models").json()

    assert listed["capabilities"] == ["Chat"]
    assert listed["thinkingConfig"] == {
        "mode": ["auto", "disabled", "high", "medium", "low"],
        "minTokens": None,
        "maxTokens": None,
        # Nothing declared: the model decides, which is what `auto` says.
        "defaultThinking": {"mode": "auto", "tokens": None},
    }


async def test_a_model_with_levels_only_is_listed_as_thinking() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(
            app,
            capabilities=["generate", "thinking"],
            thinking={"levels": ["high", "low"], "default": {"mode": "low"}},
        )
        (listed,) = client.get(f"{BASE}/models").json()

    assert listed["thinkingConfig"]["mode"] == ["high", "low"]
    assert listed["thinkingConfig"]["defaultThinking"] == {"mode": "low", "tokens": None}


async def test_a_model_that_does_not_think_has_no_thinking_block() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        (listed,) = client.get(f"{BASE}/models").json()

    assert "thinkingConfig" not in listed
    assert listed["capabilities"] == ["Chat", "Embedding"]


# == the answer (FR-13, FR-17) ====================================================================


def _answer(text: str, *, completion: int = 30, reasoning: int = 0) -> CanonicalResponse:
    return CanonicalResponse(
        model="m",
        text=text,
        usage=CanonicalUsage(
            prompt_tokens=10, completion_tokens=completion, reasoning_tokens=reasoning
        ),
    )


def test_token_output_is_the_answer_without_the_thinking() -> None:
    """The predecessor reports what the model *answered*; the thinking is billed, and the audit
    row and the budget keep it, but this field does not carry it."""
    usage = to_chat_response(_answer("ok", completion=30, reasoning=25)).usage_data

    assert usage is not None
    assert (usage.token_input, usage.token_output) == (10, 5)


def test_a_thinking_count_larger_than_the_output_is_no_negative_figure() -> None:
    usage = to_chat_response(_answer("ok", completion=3, reasoning=9)).usage_data

    assert usage is not None and usage.token_output == 0


def test_an_empty_answer_has_no_parts() -> None:
    """`parts: []`, as the predecessor sends it — not one part that says nothing."""
    assert to_chat_response(_answer("")).parts == []
    assert [part.text for part in to_chat_response(_answer("x")).parts] == ["x"]


# == ignored, and named (FR-14) ===================================================================


def test_a_thinking_count_beside_another_mode_is_ignored() -> None:
    assert thinking_of(schemas.ThinkingSetting(mode="auto", tokens=512)).tokens is None  # type: ignore[union-attr]
    assert thinking_of(schemas.ThinkingSetting(mode="limited", tokens=512)).tokens == 512  # type: ignore[union-attr]


async def test_an_ignored_thinking_count_is_served_and_named() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, capabilities=["generate", "thinking"], thinking={"modes": ["auto"]})
        response = client.post(f"{BASE}/chat", json=_chat(thinking={"mode": "auto", "tokens": 99}))

    assert response.status_code == 200, response.text
    assert response.headers["x-aira-unmodelled-fields"] == "thinking.tokens"


async def test_an_attachment_in_the_system_instruction_is_left_out_and_named() -> None:
    """The predecessor sends a system instruction's text and drops its attachments. Sent anyway,
    a model that cannot read the document would refuse a request the predecessor served."""
    encoded = base64.b64encode(PDF).decode()
    request = schemas.ChatRequest.model_validate(
        _chat(
            system_instruction={
                "parts": [{"text": "be brief"}, {"mime_type": "application/pdf", "data": encoded}]
            }
        )
    )

    system = to_canonical(request, "mock-1").messages[0]

    assert not any(isinstance(part, DataPart) for part in system.parts)
    assert system.text == "be brief"
    assert schemas.ignored_values(request) == ("system_instruction.parts.1",)

    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{BASE}/chat", json=request.model_dump(by_alias=True))

    assert response.status_code == 200, response.text
    assert response.headers["x-aira-unmodelled-fields"] == "system_instruction.parts.1"


# == statuses (FR-15, FR-16) ======================================================================


async def test_a_temperature_outside_the_range_is_the_predecessors_422() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(f"{BASE}/chat", json=_chat(temperature=2.5))

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"][0]["loc"] == ["body", "temperature"]
    assert body["details"][0]["type"] == "less_than_equal"


async def test_an_attachment_type_nobody_accepts_is_422() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post(
            f"{BASE}/chat",
            json=_chat(
                request={
                    "parts": [{"text": "?"}, {"mime_type": "application/x-nope", "data": "AA=="}]
                }
            ),
        )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("task_type", "code"),
    [
        # Not one of the eight: the predecessor's enum refuses it.
        ("SOMETHING_ELSE", "VALIDATION_ERROR"),
        # One of the eight, not declared for this model: the catalogue's own code.
        ("CLUSTERING", "INVALID_EMBEDDING_TASK_TYPE"),
    ],
)
async def test_an_unknown_task_type_and_an_undeclared_one_keep_apart(
    task_type: str, code: str
) -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app, embedding={"task_types": ["RETRIEVAL_QUERY"]})
        response = client.post(
            f"{BASE}/embed", json={"text": "a", "model_id": 1004, "task_type": task_type}
        )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == code


@pytest.mark.parametrize("status", [400, 401, 403, 404, 500, 503, 504])
def test_every_provider_failure_is_502(status: int) -> None:
    """Whatever the provider said — the predecessor answers `502 EXTERNAL_KI_API_ERROR`, and a
    client that handles it handles all of them."""
    response = refusal_response(UpstreamError("upstream said no", status))

    assert response.status_code == 502
    assert json.loads(bytes(response.body))["code"] == "EXTERNAL_KI_API_ERROR"


def test_a_provider_asking_to_slow_down_stays_429() -> None:
    response = refusal_response(UpstreamError("slow down", 429))

    assert response.status_code == 429
    assert json.loads(bytes(response.body))["code"] == "EXTERNAL_KI_API_TOO_MANY_REQUEST"


# == the stream keeps its connection (FR-19) ======================================================


async def test_the_stream_tells_a_proxy_not_to_buffer_it() -> None:
    app = _app()
    with TestClient(app) as client:
        await _catalogue(app)
        with client.stream("POST", f"{BASE}/streaming-chat", json=_chat()) as response:
            assert response.status_code == 200
            assert response.headers["x-accel-buffering"] == "no"
            "".join(response.iter_text())


async def _slow(*items: str, delay: float) -> AsyncIterator[str]:
    for item in items:
        await asyncio.sleep(delay)
        yield item


async def test_a_quiet_stream_is_a_tick_and_the_items_still_arrive_in_order() -> None:
    received = [item async for item in with_pings(_slow("a", "b", delay=0.05), interval=0.01)]

    assert [item for item in received if item is not TICK] == ["a", "b"]
    assert TICK in received, "nothing was sent while the model was quiet"
    assert received[-1] == "b"


async def test_a_busy_stream_is_never_interrupted_by_a_tick() -> None:
    received = [item async for item in with_pings(_slow("a", "b", "c", delay=0), interval=1)]

    assert received == ["a", "b", "c"]


async def test_a_failure_of_the_stream_reaches_the_reader() -> None:
    async def failing() -> AsyncIterator[str]:
        yield "a"
        raise UpstreamError("broke", 502)

    received: list[object] = []
    with pytest.raises(UpstreamError):
        async for item in with_pings(failing(), interval=1):
            received.append(item)
    assert received == ["a"]


async def test_a_reader_that_leaves_closes_the_stream() -> None:
    """A caller who hangs up must close the model's answer, as closing it directly would."""
    closed = asyncio.Event()

    async def endless() -> AsyncIterator[str]:
        try:
            while True:
                yield "x"
                await asyncio.sleep(0)
        finally:
            closed.set()

    pinged = with_pings(endless(), interval=1)
    assert await anext(pinged) == "x"
    await pinged.aclose()  # type: ignore[attr-defined]

    await asyncio.wait_for(closed.wait(), timeout=1)


def test_the_ping_is_a_comment_and_not_an_event() -> None:
    """SSE ignores a line starting with a colon: no `update` without text reaches a client."""
    assert PING.startswith(":")
    assert PING.endswith("\n\n")
    assert "data:" not in PING
