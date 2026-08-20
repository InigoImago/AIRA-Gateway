from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.core.canonical import CanonicalUsage
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import RequestLog
from aira_gateway.persistence.service import SUBJECT_COLUMN, RequestLogService


@pytest_asyncio.fixture
async def make_session() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def test_record_with_usage(make_session) -> None:
    async with make_session() as session:
        record = await RequestLogService(session).record(
            subject="u",
            auth_method="oidc",
            use_case="demo-uc",
            source_ip="1.2.3.4",
            operation="generateContent",
            model="mock-1",
            status=200,
            usage=CanonicalUsage(prompt_tokens=3, completion_tokens=5),
            latency_ms=12,
            trace_id="abc",
            api="gemini",
            request_payload={"a": 1},
            response_payload={"b": 2},
        )
    assert record.total_tokens == 8

    async with make_session() as session:
        row = (await session.execute(select(RequestLog))).scalar_one()
    assert row.subject == "u"
    assert row.use_case == "demo-uc"
    assert (row.prompt_tokens, row.completion_tokens, row.total_tokens) == (3, 5, 8)
    assert row.source_ip == "1.2.3.4"
    assert row.trace_id == "abc"
    assert row.latency_ms == 12
    assert row.request_payload == {"a": 1}
    assert row.response_payload == {"b": 2}


async def test_record_without_usage(make_session) -> None:
    async with make_session() as session:
        await RequestLogService(session).record(
            subject="u",
            auth_method="api_key",
            use_case=None,
            source_ip=None,
            operation="embedContent",
            model="mock-1",
            status=200,
            usage=None,
            latency_ms=None,
            trace_id=None,
            api="gemini",
            request_payload=None,
            response_payload=None,
        )
    async with make_session() as session:
        row = (await session.execute(select(RequestLog))).scalar_one()
    assert row.prompt_tokens is None
    assert row.total_tokens is None
    assert row.use_case is None
    assert row.request_payload is None


async def test_the_name_is_persisted_beside_the_subject(make_session) -> None:
    """`FRD-606`: the column that lets one person be one figure.

    The mutation harness asked for this one. Deleting `username=username` from the row survived
    every test in the suite — the column existed, the grouping used it, the panel rendered it, and
    nothing checked the one step that fills it. The layer above (attribution → pending row) is
    covered in `test_persistence_recorder.py`; this is the step that actually persists.
    """
    async with make_session() as session:
        await RequestLogService(session).record(
            subject="kc-uuid-1",
            username="erika",
            auth_method="oidc",
            use_case="demo-uc",
            source_ip=None,
            operation="generateContent",
            model="mock-1",
            status=200,
            usage=None,
            latency_ms=1,
            trace_id=None,
            api="gemini",
            request_payload=None,
            response_payload=None,
        )

    async with make_session() as session:
        row = (await session.execute(select(RequestLog))).scalar_one()

    assert (row.subject, row.username) == ("kc-uuid-1", "erika")


async def test_a_row_without_a_name_keeps_null(make_session) -> None:
    """Null, not an empty string: the grouping falls back to the subject on null, and `''` would
    make every nameless row one imaginary person called nothing."""
    async with make_session() as session:
        await RequestLogService(session).record(
            subject="svc-1",
            auth_method="api_key",
            use_case="demo-uc",
            source_ip=None,
            operation="generateContent",
            model="mock-1",
            status=200,
            usage=None,
            latency_ms=1,
            trace_id=None,
            api="gemini",
            request_payload=None,
            response_payload=None,
        )

    async with make_session() as session:
        row = (await session.execute(select(RequestLog))).scalar_one()

    assert row.username is None


async def test_the_identity_columns_are_bounded_like_the_model_name(make_session) -> None:
    """`subject` and `username` are `String(255)` and neither is this service's to choose.

    `auth/oidc.py` cuts `preferred_username` to 150 and the client id to 64, on a comment saying the
    claim is *"bounded like every other claim that reaches a stored field"* — and `sub`, the one
    every audit row is keyed on, was not among them. SQLite enforces no width, so the hermetic suite
    could not see it; on Postgres the INSERT fails **after** the request has been served, and the
    row recording it is the thing that disappears. The same trade `_fits` already makes for the
    model name: the row's precision, never the row.
    """
    async with make_session() as session:
        row = await RequestLogService(session).record(
            subject="s" * 400,
            username="u" * 400,
            auth_method="oidc",
            use_case=None,
            source_ip=None,
            operation="generateContent",
            model="mock-1",
            status=200,
            usage=None,
            latency_ms=None,
            trace_id=None,
            request_payload=None,
            response_payload=None,
            api="gemini",
        )

    assert len(row.subject) == SUBJECT_COLUMN
    assert row.username is not None and len(row.username) == SUBJECT_COLUMN
