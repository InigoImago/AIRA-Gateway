"""The mock's answer is bounded by the schema it fills, never by the prompt it is sent.

Its JSON-schema generator put the whole prompt into every string of the answer, while the OpenAPI
one beside it always cut it to forty characters: a 16 KiB prompt with a 256-property schema came
back as 4.2 MB, built in memory on the event loop. The mock is registered in every demo deployment,
where the published demo key reaches it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role, TextPart
from aira_gateway.core.json_schema import parse_json_schema
from aira_gateway.db.models import ModelRead
from aira_gateway.upstreams.mock import ECHOED, MockProvider, synthesise_json

PROPERTIES = 256


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {f"p{index}": {"type": "string"} for index in range(PROPERTIES)},
    }


def test_every_string_of_a_structured_answer_repeats_a_bounded_share_of_the_prompt() -> None:
    prompt = "A" * 65_536
    request = CanonicalRequest(
        model="mock-1",
        messages=[CanonicalMessage(role=Role.USER, parts=[TextPart(text=prompt)])],
        response_json_schema=parse_json_schema(_schema()),
    )

    answer = asyncio.run(MockProvider().generate(request))

    document = json.loads(answer.text)
    assert set(document) == {f"p{index}" for index in range(PROPERTIES)}, "the shape is kept"
    assert all(value == "A" * ECHOED for value in document.values())
    assert len(answer.text) < PROPERTIES * (ECHOED + 16)


def test_the_bound_holds_at_every_depth_and_in_every_shape() -> None:
    """Arrays, unions and type lists all end in strings; each of them is cut alike."""
    schema = {
        "type": "object",
        "properties": {
            "list": {"type": "array", "items": {"type": "string"}},
            "choice": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "nullable": {"type": ["string", "null"]},
            "nested": {"type": "object", "properties": {"deep": {"type": "string"}}},
        },
    }

    document = synthesise_json(schema, "B" * 10_000)

    assert document == {
        "list": ["B" * ECHOED],
        "choice": "B" * ECHOED,
        "nullable": "B" * ECHOED,
        "nested": {"deep": "B" * ECHOED},
    }


async def test_a_demo_request_gets_an_answer_the_size_of_its_schema() -> None:
    """Through the route, as the audit measured it: 16 KiB in, a few kilobytes out."""
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="mock-1", capabilities=["generate", "structured_output"]))
            await session.commit()
        body = {
            "contents": [{"role": "user", "parts": [{"text": "A" * 16 * 1024}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": _schema(),
            },
        }
        response = client.post("/v1beta/models/mock-1:generateContent", json=body)

    assert response.status_code == 200, response.text[:300]
    assert len(response.content) < 32 * 1024, len(response.content)
