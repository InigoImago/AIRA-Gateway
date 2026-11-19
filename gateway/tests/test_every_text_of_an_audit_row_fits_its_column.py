"""Every text of an audit row is one its column can store: a row that does not fit is a lost one.

PostgreSQL refuses two things SQLite stores without a word: a NUL byte in any text, and a value
wider than its `varchar`. Either fails the INSERT after the request was handled, and the row that
would have recorded it is what goes. The model name and method from the URL and the subject from the
token were held to their columns one by one; what was not on that list was not held:

- the message a refused caller was given quotes their model name, NUL included, so probing with a
  NUL left no row — invisible to the audit and to the refusal-rate rule that watches for probing;
- an OpenAI-compatible server is audited under the name the installation gives it, and its region is
  the one configured, so a server named longer than its column lost **every** row it served — each
  request answered and billed, none recorded.

So every text passes one function on its way into a row, column by column as the model declares
them, with the width read from the model: a column added later is held without anybody remembering
to. And `provider` and `region` hold what the catalogue can name, so a name is kept whole rather
than cut into a region the register of processing activities would report as a transfer.

The listener below applies Postgres's two rules to every row the session flushes, which is what
lets this suite see the failure at all.
"""

from __future__ import annotations

from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import String, event, select
from sqlalchemy.orm import Session

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.persistence.service import RequestLogService
from aira_gateway.upstreams.base import ProviderRegistry
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.transport import OpenAITransport

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}

#: A server and a region an installation might name, each longer than the columns once were.
LONG_NAME = "self-hosted-inference-cluster-frankfurt"
LONG_REGION = "on-premises-frankfurt-datacenter-a1"


def _refuse_like_postgres(session: Session, _context: Any, _instances: Any) -> None:
    """Postgres's two rules for text, applied to every row the session is about to insert."""
    for row in session.new:
        for column in row.__table__.columns:
            value = getattr(row, column.key, None)
            if not isinstance(column.type, String) or not isinstance(value, str):
                continue
            if "\x00" in value:
                raise psycopg.DataError("PostgreSQL text fields cannot contain NUL (0x00) bytes")
            if column.type.length and len(value) > column.type.length:
                raise psycopg.errors.StringDataRightTruncation(
                    f"value too long for type character varying({column.type.length})"
                )


@pytest.fixture
def like_postgres() -> Any:
    event.listen(Session, "before_flush", _refuse_like_postgres)
    yield
    event.remove(Session, "before_flush", _refuse_like_postgres)


def _text_columns() -> dict[str, int | None]:
    return {
        column.key: column.type.length
        for column in RequestLog.__table__.columns
        if isinstance(column.type, String)
    }


async def test_every_text_column_takes_any_text_it_is_handed(like_postgres: None) -> None:
    """Asked of the model's own list of columns, so a text column added later is asked too."""
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    sessions = build_sessionmaker(engine)
    hostile = "\x00a\nb c" + "x" * 2000 + "\x00"
    texts = {name: hostile for name in _text_columns() if name != "id"}
    async with sessions() as session:
        await RequestLogService(session).record(
            **{
                **texts,
                "status": 200,
                "usage": None,
                "latency_ms": 1,
                "request_payload": None,
                "response_payload": None,
                "row_id": hostile,
            }
        )
        row = (await session.execute(select(RequestLog))).scalar_one()
    await engine.dispose()

    for name, width in _text_columns().items():
        value = getattr(row, name)
        assert "\x00" not in value, name
        if width is not None:
            assert 0 < len(value) <= width, name
            assert "\n" not in value, f"{name} is one line: a control character is not kept"
    # The caller's message is not one line, and keeps its line breaks.
    assert "a\nb" in row.error


@pytest.mark.parametrize(
    "resource",
    [
        pytest.param("ghost:generateContent", id="a-plain-unknown-model"),
        pytest.param("ghost%00:generateContent", id="a-nul-in-the-model-name"),
        pytest.param("ghost%00", id="a-nul-and-no-method"),
    ],
)
async def test_a_refusal_quoting_a_nul_is_recorded(like_postgres: None, resource: str) -> None:
    """Written inline, so the row is committed or lost by the time the response returns."""
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))

    with TestClient(app) as client:
        response = client.post(f"/v1beta/models/{resource}", json=_BODY)
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert response.status_code in (400, 404), response.text
    assert len(rows) == 1, "the refused request left no audit row"
    assert rows[0].error and "\x00" not in rows[0].error


def _served_by(provider: str, region: str) -> list[RequestLog]:
    """One request answered by an OpenAI-compatible server built as `build_openai_upstreams`
    builds one, with the queue the gateway runs with."""
    app = create_app(GatewaySettings(auth_required=False, allowed_regions=region))

    def server(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    client = httpx.AsyncClient(
        base_url="http://llm.internal:8000", transport=httpx.MockTransport(server)
    )
    adapter = OpenAIAdapter(
        OpenAITransport(client=client, name=f"The model server '{provider}'"),
        ["local-1"],
        provider=provider,
        publisher="local",
        region=region,
    )
    app.state.providers = ProviderRegistry([adapter])
    with TestClient(app) as test_client:

        async def catalogue() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(ModelRead(model="local-1", provider=provider[:64], approved=True))
                await session.commit()

        test_client.portal.call(catalogue)
        response = test_client.post("/v1beta/models/local-1:generateContent", json=_BODY)
        assert response.status_code == 200, response.text
        test_client.portal.call(app.state.log_writer.drain)

        async def rows() -> list[RequestLog]:
            async with app.state.db_sessionmaker() as session:
                return list((await session.execute(select(RequestLog))).scalars())

        return test_client.portal.call(rows)


@pytest.mark.parametrize(
    ("provider", "region"),
    [
        pytest.param(LONG_NAME, "dc-frankfurt", id="a-long-server-name"),
        pytest.param("gpu-a", LONG_REGION, id="a-long-region"),
    ],
)
def test_a_server_is_recorded_under_the_name_it_was_given(
    like_postgres: None, provider: str, region: str
) -> None:
    (row,) = _served_by(provider, region)

    assert (row.provider, row.region, row.outcome) == (provider, region, "served")


def test_a_name_longer_than_any_the_catalogue_holds_costs_its_tail_not_the_row(
    like_postgres: None,
) -> None:
    region = "r" * 80

    (row,) = _served_by("gpu-a", region)

    assert row.region == "r" * 64
    assert row.outcome == "served"
