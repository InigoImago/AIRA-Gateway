"""A caller cannot choose the depth at which this gateway stops working (2026-09-08).

`ensure_body_is_encodable` closed one door: a **value** the audit row cannot hold — an unpaired
surrogate, an `Infinity` — because *"a caller could choose not to be logged, with six
characters."* The door beside it was open. Every walk over a body recurses — Python's decoder,
`json.dumps` inside that very check, `strip_attachments`, the redactor, `storable` — and how far
they recurse is a number the caller writes.

Measured before the fix, against a gateway whose body ceiling is 8 MB:

- **~1 000 levels (12 kB)**, on a request refused for an unrelated reason — `RecursionError`
  inside `strip_attachments`: a `400` to the caller and **no audit row**. `FRD-122`'s guarantee,
  gone for the price of a nested object.
- **the same depth inside a valid `functionResponse.response`** — the request was *served*, the
  model answered, and then the write raised: `500`, no answer, no row.
- **~50 000 levels (300 kB)** — `RecursionError` out of `json.loads`, so `500` on both surfaces
  and on `:dryRun`, `:checkThinking` and `/v1beta/suspensions`.

Three assertions follow from that, and they are different questions:

1. a body over the bound is a **named refusal** on every surface — not a 500, and not a message
   about JSON being invalid, because it is perfectly valid JSON;
2. a request that is refused is still **recorded**, which is the guarantee that was actually lost;
3. no route reads a body without the bound — the layering half, because a rule four call sites
   have to remember is one the fifth will not.

The depth the *interpreter* fails at is not a constant: `RecursionError` fires when the stack runs
out, so it depends on how deep the request already is. That is the argument for a bound of our own
rather than for catching the error — a bound is the same everywhere, and an exception is not.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_common.nesting import MAX_JSON_DEPTH
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import Base
from aira_gateway.db.models import ModelRead, RequestLog, UseCaseRead
from aira_gateway.persistence.redaction import NoOpRedactor
from aira_gateway.persistence.writer import (
    MAX_STORED_DEPTH,
    TOO_DEEP,
    PendingLog,
    RequestLogWriter,
    within_depth,
)

TOOL = {
    "name": "read_file",
    "description": "Read a file.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
}

#: Both incident roles, so nothing is refused for want of authority — a `403` would be a perfectly
#: good answer to a deep body and would prove nothing about the bound.
ADMIN = Principal(
    subject="root",
    method="apikey",
    roles=("global-admin", "it-security"),
    use_cases=("uc",),
    username="root",
    credential="key-1",
)


def _deep_document(depth: int) -> bytes:
    return (b'{"a":' * depth) + b"1" + (b"}" * depth)


def _deep_value(depth: int) -> Any:
    node: Any = {"leaf": 1}
    for _ in range(depth):
        node = {"n": node}
    return node


def _app() -> Any:
    # `log_queue_size=0` writes the audit row **on the request path**, which is a supported
    # configuration and the one that makes a failed write visible to the caller rather than to a
    # background task's log. With the queue on, the same defect loses the row silently.
    return create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))


async def _seed(app: Any) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug="uc", name="uc", tools_enabled=True))
        session.add(ModelRead(model="mock-1", capabilities=["generate", "tools"]))
        await session.commit()


async def _rows(app: Any) -> int:
    async with app.state.db_sessionmaker() as session:
        return int((await session.execute(select(func.count(RequestLog.id)))).scalar_one())


def _client(app: Any) -> TestClient:
    app.dependency_overrides[require_principal] = lambda: ADMIN
    # A `500` has to arrive as a response rather than as a raised exception, or the assertions
    # below would be about pytest instead of about the gateway.
    return TestClient(app, raise_server_exceptions=False)


#: Every route that reads a hand-written body, and the depth-refusal each surface renders. Two
#: envelopes, because the KIRA surface's whole contract is the predecessor's error shape — the
#: check is that *both* say what happened, not that they say it the same way.
ROUTES: list[tuple[str, int, str]] = [
    ("/v1beta/models/mock-1:generateContent", 400, "nests deeper"),
    ("/v1beta/models/mock-1:embedContent", 400, "nests deeper"),
    ("/v1beta/pipeline:dryRun", 400, "nests deeper"),
    ("/v1beta/suspensions", 400, "nests deeper"),
    ("/v1beta/models/mock-1:checkThinking", 400, "nests deeper"),
    ("/kira/api/external/chat", 400, "nests deeper"),
    ("/kira/api/external/embed", 400, "nests deeper"),
    ("/kira/api/external/streaming-chat", 400, "nests deeper"),
]


@pytest.mark.parametrize(("path", "status", "phrase"), ROUTES)
async def test_a_body_over_the_bound_is_a_named_refusal(
    path: str, status: int, phrase: str
) -> None:
    app = _app()
    with _client(app) as client:
        await _seed(app)
        response = client.post(
            path,
            content=_deep_document(MAX_JSON_DEPTH + 1),
            headers={"content-type": "application/json", "x-aira-use-case": "uc"},
        )

    assert response.status_code == status, (path, response.text)
    assert phrase in response.text, (path, response.text)


@pytest.mark.parametrize("path", [route for route, _status, _phrase in ROUTES])
async def test_a_body_at_the_bound_is_not_refused_for_its_depth(path: str) -> None:
    """The other half, and the one a bound gets wrong quietly.

    A document of exactly `MAX_JSON_DEPTH` is not deep, and every one of these routes will refuse
    it for some *other* reason — a missing field, a wrong shape. What must not appear is this
    bound's name: a limit that fires one level early is a limit nobody can work out from the
    message.
    """
    app = _app()
    with _client(app) as client:
        await _seed(app)
        response = client.post(
            path,
            content=_deep_document(MAX_JSON_DEPTH),
            headers={"content-type": "application/json", "x-aira-use-case": "uc"},
        )

    assert "nests deeper" not in response.text, (path, response.text)
    assert response.status_code < 500, (path, response.text)


async def test_a_deep_body_is_still_recorded() -> None:
    """The guarantee that was actually lost (`FRD-122`): the log records what was **asked**.

    Written as a count across a refusal, because "it did not 500" is a different claim. Before the
    fix this request answered `400` — correctly, for an unrelated reason — and wrote nothing,
    with `audit_refusal_not_recorded` in a log nobody reads during an incident.
    """
    app = _app()
    with _client(app) as client:
        await _seed(app)
        before = await _rows(app)
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            content=_deep_document(MAX_JSON_DEPTH + 1),
            headers={"content-type": "application/json", "x-aira-use-case": "uc"},
        )
        after = await _rows(app)

    assert response.status_code == 400
    assert after == before + 1, "a refused request that reached a surface is a row"


async def test_a_deep_value_inside_a_valid_request_is_refused_before_the_model() -> None:
    """The expensive shape: a body the surfaces would otherwise **accept**.

    `FunctionResponse.response` is `dict[str, Any]` — caller data, replayed from a tool the caller
    ran, and bounded by nothing. Before the fix this request reached the model, got an answer, and
    then died in the writer: `500`, no answer returned, no row, and on a real provider the spend
    had already happened.
    """
    app = _app()
    with _client(app) as client:
        await _seed(app)
        before = await _rows(app)
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": "read hello.py"}]},
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": "read_file",
                                "response": _deep_value(MAX_JSON_DEPTH + 5),
                            }
                        }
                    ],
                },
            ],
            "tools": [{"functionDeclarations": [TOOL]}],
        }
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=body,
            headers={"x-aira-use-case": "uc"},
        )
        after = await _rows(app)

    assert response.status_code == 400, response.text
    assert "nests deeper" in response.text
    assert after == before + 1


async def test_a_shallow_tool_result_still_serves() -> None:
    """The comparison that stops the test above being a tautology: the same request, shallower,
    is answered. A bound proves nothing unless the thing under it works."""
    app = _app()
    with _client(app) as client:
        await _seed(app)
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": "read hello.py"}]},
                {
                    "role": "user",
                    "parts": [
                        {"functionResponse": {"name": "read_file", "response": _deep_value(5)}}
                    ],
                },
            ],
            "tools": [{"functionDeclarations": [TOOL]}],
        }
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=body,
            headers={"x-aira-use-case": "uc"},
        )

    assert response.status_code == 200, response.text


# =================================================================================================
# The writer's own bound — the upstream's door, which no request-side check can close
# =================================================================================================


def test_a_payload_too_deep_to_walk_costs_its_value_and_not_the_row() -> None:
    """`storable`'s rule, applied to structure: name what was there rather than lose the record.

    This is the **response** side. A request body is bounded at the door; a response payload is a
    provider's output, and a model that answers with a thousand nested objects must not be able to
    erase the record of its own answer.
    """
    deep = _deep_value(MAX_STORED_DEPTH + 50)
    bounded = within_depth(deep)

    # It survived at all, which is the property — and it is still JSON.
    assert json.dumps(bounded)
    text = json.dumps(bounded)
    assert TOO_DEEP in text
    assert "nested deeper" in TOO_DEEP, "the marker has to say what was wrong with it"


async def test_the_writer_keeps_the_row_when_a_provider_answers_too_deep() -> None:
    """The **wire**, not the ends: `within_depth` exists and `_maybe` has to call it.

    Driven through `RequestLogWriter.submit`, which is upstream of the clamp, because a test that
    calls the clamp directly passes with the call site deleted — the failure this project has
    recorded ten times (`LESSONS.md` §1, *two correct halves and no wire*).

    A response payload is the one door a request-side bound cannot close, so this is where the
    property has to hold.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        writer = RequestLogWriter(sessionmaker, GatewaySettings(), NoOpRedactor(), max_queue=0)
        await writer.submit(
            PendingLog(
                subject="alice",
                auth_method="api_key",
                use_case=None,
                source_ip="127.0.0.1",
                operation="generateContent",
                model="mock-1",
                status=200,
                usage=None,
                latency_ms=5,
                trace_id=None,
                request_payload={"contents": []},
                response_payload=_deep_value(MAX_STORED_DEPTH + 200),
                cost_nanos=100,
                api="gemini",
            )
        )
        async with sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())
    finally:
        await engine.dispose()

    assert len(rows) == 1, "a provider's answer must not be able to erase the row that records it"
    assert TOO_DEEP in json.dumps(rows[0].response_payload)


def test_what_is_shallow_enough_is_untouched() -> None:
    """The comparison. A clamp that rewrites everything is a clamp that has lost the payload."""
    payload = {"contents": [{"parts": [{"text": "hello"}]}], "n": [1, 2, {"deep": True}]}
    assert within_depth(payload) == payload


def test_the_writer_bound_sits_above_the_door_bound() -> None:
    """Two bounds, one rule, and the order between them is the point.

    A request body this gateway *accepted* must never be clipped by the recorder: a stored request
    that disagreed with the one that was served would be evidence nobody could use. So the writer
    is the looser of the two, and the relation is asserted rather than left to two constants that
    happen to be in the right order today.
    """
    assert MAX_STORED_DEPTH > MAX_JSON_DEPTH


# =================================================================================================
# Layering — every body goes through the one reader
# =================================================================================================

_API = Path(__file__).resolve().parents[1] / "src" / "aira_gateway" / "api"
#: Every module that reads a caller's body. The `serving` package holds the reader and is exempt.
_READERS = sorted(
    path for path in _API.rglob("*.py") if "serving" not in path.relative_to(_API).parts
)


def test_there_are_modules_to_check() -> None:
    """A guard on the guard: an empty glob would make the assertion below pass by asking nothing
    (`LESSONS.md` §7)."""
    assert len(_READERS) >= 4, [str(path) for path in _READERS]


@pytest.mark.parametrize("path", _READERS, ids=lambda p: p.name)
def test_no_route_reads_a_body_without_the_bound(path: Path) -> None:
    """`request.json()` is how this defect arrived on five routes at once.

    Each of them was written correctly by its own lights — a `try`, an `except ValueError`, a
    `400` — and every one of them inherited the hazard, because the hazard is in the call rather
    than in the handling around it. `json_body` is the one reader, and this is what stops the
    sixth route being written the old way.
    """
    tree = ast.parse(path.read_text())
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "json"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "request"
    ]
    assert not offenders, (
        f"{path.name} reads a body with `request.json()` at line(s) {offenders}. Use "
        "`serving.json_body`, which bounds the nesting — an unbounded parse is a 500 the caller "
        "chooses."
    )
