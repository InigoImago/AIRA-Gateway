"""Per-use-case control over whether payloads are stored at all (FRD-404).

Retention decides when a stored prompt goes. This decides whether it is ever written — the only
control that helps for data that must not be persisted in the first place.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import Base
from aira_gateway.db.models import RequestLog, UseCaseRead

BODY = {"contents": [{"role": "user", "parts": [{"text": "meine Personalnummer ist 4711"}]}]}
URL = "/v1beta/models/mock-1:generateContent"


def _app(*, store_payloads: bool = True):
    return create_app(
        GatewaySettings(auth_required=False, store_payloads=store_payloads, enforce_budgets=False)
    )


async def _use_case(app, slug: str, *, store_payloads: bool) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug=slug, name=slug, store_payloads=store_payloads))
        await session.commit()


async def _logs(app) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.created_at))
        return list(result.scalars())


def _post(client: TestClient, use_case: str | None) -> None:
    """Send one request. The client is shared: a nested TestClient would run the lifespan again
    and dispose the in-memory database on exit."""
    headers = {"x-aira-use-case": use_case} if use_case else {}
    assert client.post(URL, json=BODY, headers=headers).status_code == 200


async def test_a_use_case_can_decline_storage_entirely() -> None:
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "private-uc", store_payloads=False)
        _post(client, "private-uc")

        rows = await _logs(app)
        assert len(rows) == 1, "the request is still accounted for"
        assert rows[0].request_payload is None
        assert rows[0].response_payload is None
        # Everything the accounting needs is still there.
        assert rows[0].use_case == "private-uc"
        assert rows[0].total_tokens is not None


async def test_storage_stays_on_where_the_use_case_allows_it() -> None:
    app = _app()
    with TestClient(app) as client:
        await _use_case(app, "open-uc", store_payloads=True)
        _post(client, "open-uc")

        rows = await _logs(app)
        assert rows[0].request_payload is not None
        assert "4711" in str(rows[0].request_payload)


async def test_the_installation_setting_is_a_kill_switch() -> None:
    """A use-case admin may decline storage but must not be able to re-enable it."""
    app = _app(store_payloads=False)
    with TestClient(app) as client:
        await _use_case(app, "wants-storage", store_payloads=True)
        _post(client, "wants-storage")

        assert (await _logs(app))[0].request_payload is None


async def test_requests_without_a_use_case_follow_the_installation_setting() -> None:
    app = _app()
    with TestClient(app) as client:
        _post(client, None)
        assert (await _logs(app))[0].request_payload is not None

    off = _app(store_payloads=False)
    with TestClient(off) as client:
        _post(client, None)
        assert (await _logs(off))[0].request_payload is None


async def test_an_unknown_use_case_keeps_the_previous_behaviour() -> None:
    # The read-model may lag behind Management; that must not silently drop the audit payload.
    app = _app()
    with TestClient(app) as client:
        _post(client, "not-yet-distributed")
        assert (await _logs(app))[0].request_payload is not None


# ---- switching it off purges what is already there ------------------------------------------


@pytest.fixture
async def sessionmaker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_switching_storage_off_removes_what_was_already_stored(sessionmaker) -> None:
    from datetime import UTC, datetime, timedelta

    from aira_gateway.retention import RetentionService

    now = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
    async with sessionmaker() as session:
        session.add(UseCaseRead(slug="uc", name="uc", store_payloads=False, retention_days=90))
        session.add(
            RequestLog(
                subject="alice",
                auth_method="api_key",
                use_case="uc",
                api="gemini",
                operation="generateContent",
                model="mock-1",
                status=200,
                created_at=now - timedelta(minutes=1),  # well inside the 90-day period
                request_payload={"contents": []},
                response_payload={"text": "…"},
            )
        )
        await session.commit()

    # "Do not keep prompts" means the existing ones go too, not that they linger for 90 days.
    result = await RetentionService(sessionmaker).prune(now)
    assert result.payloads_cleared == 1

    async with sessionmaker() as session:
        row = (await session.execute(select(RequestLog))).scalars().one()
    assert row.request_payload is None
    assert row.status == 200  # the record itself survives
