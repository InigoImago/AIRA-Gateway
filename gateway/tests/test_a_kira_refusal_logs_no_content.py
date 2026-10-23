"""A KIRA refusal's log line says what the body *was*, never what it *said* — and stays short.

The line is outside every control on stored content, and the privacy notice promises a log line
never carries the content of a request. It used to keep **every key** of the body: so the keys of
the objects a caller fills with data — a function's arguments and result, the property names of a
schema — reached the line, and in a security audit's sample those were a patient's name, an e-mail
address and `diagnose_hiv_positiv`. And the list of fields it does not model was unbounded: a
7.3 MB body of extra fields was a 7 MB line, on every refusal, a rate limit included.

Names now appear only where the contract defines them, and only along the objects the contract
defines; everything else is its kind and size.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.api.kira import schemas
from aira_gateway.api.kira.headers import MAX_NAMED_CHARACTERS, MAX_NAMED_FIELDS
from aira_gateway.api.kira.log_shape import SHAPE_ENTRIES, body_shape
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, UseCaseRead

BASE = "/kira/api/external"
UC = {"x-aira-use-case": "uc"}

NAME = "Max Mustermann geb 1970-01-01"
MAIL = "max.mustermann@example.org"
DIAGNOSIS = "diagnose_hiv_positiv"
SECRETS = (NAME, MAIL, DIAGNOSIS, "befund", "positiv")


def _app() -> Any:
    return create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))


async def _ready(app: Any) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug="uc", name="uc", tools_enabled=True))
        session.add(ModelRead(model="mock-1", numeric_id=1004, capabilities=["generate", "tools"]))
        await session.commit()


def _refusal_line(caplog: pytest.LogCaptureFixture) -> tuple[str, dict[str, Any]]:
    (raw,) = [r.getMessage() for r in caplog.records if "kira_request_refused" in r.getMessage()]
    return raw, json.loads(raw)


def _conversation() -> dict[str, Any]:
    """The audit's sample: data in a schema's property names, a call's argument keys and a
    result's keys — refused after it was read, for a model id nothing has."""
    return {
        "model_id": 999_999,
        "tools": [
            {
                "name": "lookup_patient",
                "description": "Find a patient.",
                "parameters": {"type": "object", "properties": {DIAGNOSIS: {"type": "string"}}},
            }
        ],
        "conversation_history": [
            {"role": "user", "content": {"parts": [{"text": "Wer ist der Patient?"}]}},
            {
                "role": "model",
                "content": {
                    "parts": [
                        {
                            "type": "function_call",
                            "id": "c1",
                            "name": "lookup_patient",
                            "arguments": {MAIL: "?"},
                            "opaque_metadata": {MAIL: "sig"},
                        }
                    ]
                },
            },
        ],
        "request": {
            "parts": [
                {
                    "type": "function_result",
                    "call_id": "c1",
                    "name": "lookup_patient",
                    "result": {NAME: {"befund": "positiv"}},
                    "error": {MAIL: "timeout"},
                }
            ]
        },
        "responseSchema": {"type": "OBJECT", "properties": {DIAGNOSIS: {"type": "STRING"}}},
    }


async def test_a_function_s_data_never_reaches_the_refusal_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _app()
    with caplog.at_level(logging.INFO), TestClient(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=_conversation(), headers=UC)

    assert response.status_code == 422, response.text
    raw, line = _refusal_line(caplog)
    for secret in SECRETS:
        assert secret not in raw, f"{secret!r} reached the log"
    # What it *was* survives — the contract's names along the contract's objects, each value by its
    # kind and size — which is what makes the line worth having.
    shape = line["request_shape"]
    assert shape["tools"] == [
        {"name": "str(14)", "description": "str(15)", "parameters": "object(2)"}
    ]
    (call,) = shape["conversation_history"][1]["content"]["parts"]
    assert call["arguments"] == "object(1)" and call["opaque_metadata"] == "object(1)"
    (result,) = shape["request"]["parts"]
    assert result == {
        "type": "str(15)",
        "call_id": "str(2)",
        "name": "str(14)",
        "result": "object(1)",
        "error": "object(1)",
    }
    assert shape["responseSchema"] == "object(2)"


async def test_a_field_this_surface_does_not_model_is_named_and_never_opened(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Its name is what an operator needs — which fields does this client send that nothing here
    reads — and it is named in the response header already. What it holds is the caller's."""
    app = _app()
    body = {
        "model_id": 999_999,
        "request": {"parts": [{"text": "hi", "client_note": {MAIL: 1}}]},
        "client_meta": {MAIL: {"patient": NAME}},
        "Max Mustermann": True,
    }
    with caplog.at_level(logging.INFO), TestClient(app) as client:
        await _ready(app)
        response = client.post(f"{BASE}/chat", json=body, headers=UC)

    assert response.status_code == 422
    raw, line = _refusal_line(caplog)
    for secret in SECRETS:
        assert secret not in raw, f"{secret!r} reached the log"
    shape = line["request_shape"]
    assert shape["client_meta"] == "object(1)"
    # A name the header would not repeat — a space in it — is counted, not written.
    assert shape["…"] == "1 more"
    assert shape["request"]["parts"] == [{"text": "str(2)", "client_note": "object(1)"}]
    assert "client_meta" in line["unmodelled_fields"]


async def test_the_refusal_line_is_bounded_however_many_fields_the_body_carries(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The audit's second sample: 120 000 extra fields, a 7.3 MB body, a 7 MB line."""
    extras = {f"field_{index:07d}_{'x' * 40}": 0 for index in range(120_000)}
    raw_body = json.dumps({"request": {"parts": [{"text": "hi"}]}, "model_id": 999_999, **extras})
    app = _app()
    with caplog.at_level(logging.INFO), TestClient(app) as client:
        await _ready(app)
        response = client.post(
            f"{BASE}/chat", content=raw_body, headers={"content-type": "application/json", **UC}
        )

    assert response.status_code == 422, response.text[:200]
    raw, line = _refusal_line(caplog)
    assert len(raw) < 32 * 1024, f"a {len(raw_body)}-byte body made a {len(raw)}-byte line"
    # The header's rule, in the log as well: the first names, then how many more.
    *named, rest = line["unmodelled_fields"]
    assert 0 < len(named) <= MAX_NAMED_FIELDS
    assert sum(len(name) + 2 for name in named) <= MAX_NAMED_CHARACTERS
    assert rest == f"+{120_000 - len(named)} more"


def test_the_shape_is_bounded_in_entries_wherever_they_are() -> None:
    """Parts, turns and fields alike count against one budget, so no position is an exit."""
    turns = [{"role": "user", "content": {"parts": [{"text": "x"}] * 50}}] * 50
    shape = body_shape({"conversation_history": turns, "request": {"parts": []}}, "chat")

    assert len(json.dumps(shape)) < 16 * 1024
    flattened = json.dumps(shape)
    assert "more" in flattened, "the rest is counted, not silently dropped"
    assert flattened.count("str(1)") < SHAPE_ENTRIES


@pytest.mark.parametrize(
    ("operation", "body", "expected"),
    [
        (
            "embed",
            {"text": [NAME, MAIL], "model_id": 1, "task_type": "RETRIEVAL_QUERY"},
            {"text": "list(2)", "model_id": "number", "task_type": "str(15)"},
        ),
        (
            "rerank",
            {"model_id": 1, "query": NAME, "records": [{"id": MAIL, "title": "t", "content": "c"}]},
            {
                "model_id": "number",
                "query": f"str({len(NAME)})",
                "records": [{"id": f"str({len(MAIL)})", "title": "str(1)", "content": "str(1)"}],
            },
        ),
        # An operation the shape does not know is described whole, never walked.
        ("unknown", {MAIL: 1}, "object(1)"),
    ],
)
def test_every_operation_is_walked_along_its_own_request(
    operation: str, body: dict[str, Any], expected: Any
) -> None:
    assert body_shape(body, operation) == expected


# == the vocabulary the shape relies on is the one the validator reads ============================


def _part_fields_read() -> set[str]:
    """Every string a part is asked for, in the modules that read parts: `part.get("x")`,
    `raw["x"]`, `"x" in part` — read from the source, so a field added there is a field the
    shape must know before it can name it."""
    found: set[str] = set()
    source = Path(schemas.__file__).parent
    for module in ("schemas.py", "mapping.py"):
        tree = ast.parse((source / module).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"part", "raw"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                found.add(str(node.args[0].value))
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id in {"part", "raw"}
                and isinstance(node.slice, ast.Constant)
            ):
                found.add(str(node.slice.value))
            elif (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Constant)
                and isinstance(node.ops[0], ast.In | ast.NotIn)
                and isinstance(node.comparators[0], ast.Name)
                and node.comparators[0].id in {"part", "raw"}
            ):
                found.add(str(node.left.value))
    return found


def test_the_part_vocabulary_is_the_one_its_readers_read() -> None:
    """`PART_FIELDS` is a hand-written list, so it has a counterpart: the fields the validator and
    the mapper actually read. In both directions — a field read and not listed would be counted
    rather than named, one listed and not read would be named although nothing models it."""
    read = _part_fields_read()

    assert len(read) > 5, "the reader found the source it is meant to read"
    assert read == set(schemas.PART_FIELDS)
