"""Payload retention (FRD-404).

This code deletes data irreversibly, so the tests are about the boundaries: what exactly goes,
what exactly stays, and that a second run does not quietly take more than the first.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_gateway.consumer.apply import apply_event
from aira_gateway.db.base import Base
from aira_gateway.db.models import RequestLog, UseCaseRead
from aira_gateway.retention import DEFAULT_RETENTION_DAYS, RetentionService

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


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


async def _log(sessionmaker, *, use_case: str | None, age_days: float, subject: str = "alice"):
    """A request log entry aged ``age_days`` days, with payloads present."""
    async with sessionmaker() as session:
        entry = RequestLog(
            subject=subject,
            auth_method="api_key",
            use_case=use_case,
            api="gemini",
            operation="generateContent",
            model="mock-1",
            status=200,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            cost_nanos=85_000,
            created_at=NOW - timedelta(days=age_days),
            request_payload={"contents": [{"parts": [{"text": "meine Personalnummer ist 4711"}]}]},
            response_payload={"text": "…"},
        )
        session.add(entry)
        await session.commit()
        return entry.id


async def _rows(sessionmaker) -> list[RequestLog]:
    async with sessionmaker() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.created_at))
        return list(result.scalars())


async def _use_case(sessionmaker, slug: str, retention_days: int) -> None:
    async with sessionmaker() as session:
        session.add(UseCaseRead(slug=slug, name=slug, retention_days=retention_days))
        await session.commit()


# ---- the boundary ------------------------------------------------------------------------


async def test_payloads_older_than_the_period_are_removed(sessionmaker) -> None:
    await _use_case(sessionmaker, "uc", 7)
    await _log(sessionmaker, use_case="uc", age_days=8)

    result = await RetentionService(sessionmaker).prune(NOW)

    assert result.payloads_cleared == 1
    row = (await _rows(sessionmaker))[0]
    assert row.request_payload is None
    assert row.response_payload is None


async def test_payloads_inside_the_period_are_untouched(sessionmaker) -> None:
    await _use_case(sessionmaker, "uc", 7)
    await _log(sessionmaker, use_case="uc", age_days=6.9)

    assert (await RetentionService(sessionmaker).prune(NOW)).payloads_cleared == 0
    assert (await _rows(sessionmaker))[0].request_payload is not None


async def test_the_metadata_survives_so_reporting_keeps_working(sessionmaker) -> None:
    """The accounting is the reason payloads are cleared rather than rows deleted."""
    await _use_case(sessionmaker, "uc", 7)
    await _log(sessionmaker, use_case="uc", age_days=30)

    await RetentionService(sessionmaker).prune(NOW)

    row = (await _rows(sessionmaker))[0]
    assert row.subject == "alice"
    assert row.model == "mock-1"
    assert row.total_tokens == 30
    assert row.cost_nanos == 85_000
    assert row.status == 200


# ---- per use case ------------------------------------------------------------------------


async def test_each_use_case_follows_its_own_period(sessionmaker) -> None:
    await _use_case(sessionmaker, "short", 1)
    await _use_case(sessionmaker, "long", 90)
    await _log(sessionmaker, use_case="short", age_days=2)
    await _log(sessionmaker, use_case="long", age_days=2)

    await RetentionService(sessionmaker).prune(NOW)

    by_use_case = {row.use_case: row for row in await _rows(sessionmaker)}
    assert by_use_case["short"].request_payload is None
    assert by_use_case["long"].request_payload is not None


async def test_requests_without_a_use_case_follow_the_installation_default(sessionmaker) -> None:
    # An unbound break-glass key is not exempt just because nobody claimed its traffic.
    await _log(sessionmaker, use_case=None, age_days=8)

    assert (await RetentionService(sessionmaker).prune(NOW)).payloads_cleared == 1
    assert (await _rows(sessionmaker))[0].request_payload is None


async def test_the_default_period_is_one_week(sessionmaker) -> None:
    assert DEFAULT_RETENTION_DAYS == 7
    await _log(sessionmaker, use_case=None, age_days=6)
    assert (await RetentionService(sessionmaker).prune(NOW)).payloads_cleared == 0


async def test_a_custom_default_applies_to_unclaimed_traffic(sessionmaker) -> None:
    await _log(sessionmaker, use_case=None, age_days=2)
    service = RetentionService(sessionmaker, default_retention_days=1)
    assert (await service.prune(NOW)).payloads_cleared == 1


async def test_a_use_case_the_gateway_has_never_heard_of_still_gets_pruned(sessionmaker) -> None:
    """A use case removed from Management must not turn into an indefinite payload store."""
    await _log(sessionmaker, use_case="vanished", age_days=400)
    service = RetentionService(sessionmaker, log_retention_days=365)

    result = await service.prune(NOW)

    assert result.rows_deleted == 1
    assert await _rows(sessionmaker) == []


# ---- repeated runs -----------------------------------------------------------------------


async def test_a_second_run_clears_nothing_and_takes_nothing_more(sessionmaker) -> None:
    await _use_case(sessionmaker, "uc", 7)
    await _log(sessionmaker, use_case="uc", age_days=8)
    await _log(sessionmaker, use_case="uc", age_days=1)

    first = await RetentionService(sessionmaker).prune(NOW)
    second = await RetentionService(sessionmaker).prune(NOW)

    assert first.payloads_cleared == 1
    assert second.payloads_cleared == 0  # idempotent, and the count means what it says
    assert sum(row.request_payload is not None for row in await _rows(sessionmaker)) == 1


async def test_nothing_to_do_is_not_an_error(sessionmaker) -> None:
    result = await RetentionService(sessionmaker).prune(NOW)
    assert result.payloads_cleared == 0
    assert result.rows_deleted == 0
    assert "payloads cleared: 0" in str(result)


# ---- record retention --------------------------------------------------------------------


async def test_rows_are_kept_forever_unless_record_retention_is_switched_on(sessionmaker) -> None:
    await _use_case(sessionmaker, "uc", 7)
    await _log(sessionmaker, use_case="uc", age_days=3650)

    result = await RetentionService(sessionmaker).prune(NOW)

    assert result.rows_deleted == 0
    assert len(await _rows(sessionmaker)) == 1


async def test_record_retention_removes_whole_rows_when_configured(sessionmaker) -> None:
    await _use_case(sessionmaker, "uc", 7)
    await _log(sessionmaker, use_case="uc", age_days=100)
    await _log(sessionmaker, use_case="uc", age_days=10)

    service = RetentionService(sessionmaker, log_retention_days=90)
    result = await service.prune(NOW)

    assert result.rows_deleted == 1
    assert len(await _rows(sessionmaker)) == 1


async def test_a_nonsensical_period_falls_back_to_at_least_a_day(sessionmaker) -> None:
    service = RetentionService(sessionmaker, default_retention_days=0, log_retention_days=-5)
    await _log(sessionmaker, use_case=None, age_days=2)

    result = await service.prune(NOW)

    assert result.payloads_cleared == 1  # clamped to one day, not to zero
    assert result.rows_deleted == 0  # negative record retention stays off


# ---- distribution ------------------------------------------------------------------------


async def test_the_period_arrives_from_management(sessionmaker) -> None:
    async with sessionmaker() as session:
        await apply_event(
            session,
            "usecase.upserted",
            {"slug": "uc", "name": "UC", "retention_days": 30},
        )
        stored = await session.get(UseCaseRead, "uc")
    assert stored is not None and stored.retention_days == 30


async def test_a_use_case_event_without_a_period_defaults_to_a_week(sessionmaker) -> None:
    # Forward compatibility: an older Management does not send the field, and the safe reading
    # of a missing retention period is the short one, not "keep forever".
    async with sessionmaker() as session:
        await apply_event(session, "usecase.upserted", {"slug": "uc", "name": "UC"})
        stored = await session.get(UseCaseRead, "uc")
    assert stored is not None and stored.retention_days == DEFAULT_RETENTION_DAYS


# ---- a use case that no longer exists ------------------------------------------------------


async def test_payloads_of_a_deleted_use_case_are_removed_too(sessionmaker) -> None:
    """The hole the sweep had, and the sentence that hid it.

    `_delete_usecase` in the consumer keeps `request_logs` on purpose — the audit trail and the
    spend history outlive the use case — and states *"their payloads still expire on the retention
    clock."* They did not. The clock is built from the `use_cases` read-model: a period per slug it
    knows, plus one pass for rows with no use case at all. A row whose slug is **neither** — which
    is precisely what deleting a use case produces — matched no pass and was never cleared, for
    ever, on an installation that has not switched the optional record retention on.

    Measured on the running stack before this was written: **1509 rows** carrying stored prompts
    for use cases that no longer exist. Deleting a use case is exactly the moment its prompts
    should go, and it was the one moment they could not.
    """
    await _log(sessionmaker, use_case="deleted-uc", age_days=30)

    result = await RetentionService(sessionmaker).prune(NOW)

    assert result.payloads_cleared == 1, "an orphaned row's payloads were left in place"
    rows = await _rows(sessionmaker)
    assert rows[0].request_payload is None
    assert rows[0].response_payload is None
    # The row itself stays: the spend history is what a later question is answered from
    # (`FRD-404` §4.1), and only the record retention deletes it.
    assert rows[0].total_tokens == 30


async def test_an_orphan_keeps_its_payload_until_the_default_period_is_up(sessionmaker) -> None:
    """The other half, or the fix is "delete everything you do not recognise". A use case whose
    read-model row has not arrived yet is indistinguishable from one that was deleted — Kafka
    orders neither — so an orphan follows the **installation default**, exactly like traffic that
    names no use case at all."""
    await _log(sessionmaker, use_case="not-yet-known", age_days=1)

    result = await RetentionService(sessionmaker).prune(NOW)

    assert result.payloads_cleared == 0
    assert (await _rows(sessionmaker))[0].request_payload is not None


async def test_an_empty_read_model_still_sweeps(sessionmaker) -> None:
    """A guard on the widening itself: with no use cases known at all, `NOT IN ()` is the shape
    SQL is worst at. SQLAlchemy renders an empty `IN` as a constant rather than invalid SQL, and
    the constant has to be the one that means *everything is unknown* — which it is, because
    nothing is known."""
    await _log(sessionmaker, use_case="anything", age_days=30)

    result = await RetentionService(sessionmaker).prune(NOW)

    assert result.payloads_cleared == 1


async def test_a_known_use_case_is_not_swept_by_the_orphan_pass(sessionmaker) -> None:
    """And the widening must not reach past its own edge. A use case with a **longer** period than
    the installation default keeps its payloads for that period — if the orphan pass matched it
    too, every long retention would silently become the default."""
    await _use_case(sessionmaker, "long-uc", 30)
    await _log(sessionmaker, use_case="long-uc", age_days=10)

    result = await RetentionService(sessionmaker).prune(NOW)

    assert result.payloads_cleared == 0
    assert (await _rows(sessionmaker))[0].request_payload is not None


# ---- a retired use case keeps its own clock (`FRD-607`) --------------------------------------


async def _retired(sessionmaker, slug: str, retention_days: int) -> None:
    """A use case Management has retired: the row survives, marked."""
    async with sessionmaker() as session:
        session.add(
            UseCaseRead(
                slug=slug,
                name=slug,
                retention_days=retention_days,
                deleted_at=NOW - timedelta(days=1),
            )
        )
        await session.commit()


async def test_a_retired_use_case_keeps_the_period_it_promised(sessionmaker) -> None:
    """**The promise made to a data subject does not change because somebody pressed Delete.**

    The two numbers differ on purpose and in the direction that matters: the use case promised
    **90** days and the installation default is **7**. While a deleted use case's row was removed,
    these payloads fell through to the default and were destroyed 83 days early — a different
    promise, substituted silently, at the moment of deletion.

    The other direction is just as wrong and is the test below.
    """
    await _retired(sessionmaker, "retired-long", 90)
    await _log(sessionmaker, use_case="retired-long", age_days=30)
    service = RetentionService(sessionmaker, default_retention_days=7)

    result = await service.prune(NOW)

    assert result.payloads_cleared == 0
    assert [row.request_payload for row in await _rows(sessionmaker)] != [None]


async def test_a_retired_use_case_is_not_kept_longer_either(sessionmaker) -> None:
    """The mirror, and the one the GDPR asks about: a **short** promise must still be honoured.

    A use case that promised 3 days and was retired must not inherit an installation default of 30
    and keep prompts for a month. Retiring is not consent renewed any more than it is consent
    withdrawn — the period the data subject was told about is the period that applies.
    """
    await _retired(sessionmaker, "retired-short", 3)
    await _log(sessionmaker, use_case="retired-short", age_days=10)
    service = RetentionService(sessionmaker, default_retention_days=30)

    result = await service.prune(NOW)

    assert result.payloads_cleared == 1
    assert [row.request_payload for row in await _rows(sessionmaker)] == [None]


async def test_a_retired_use_case_with_storage_switched_off_is_cleared_on_sight(
    sessionmaker,
) -> None:
    """Two states that both mean *do not keep this*, and they compose rather than cancel."""
    async with sessionmaker() as session:
        session.add(
            UseCaseRead(
                slug="retired-off",
                name="off",
                retention_days=90,
                store_payloads=False,
                deleted_at=NOW - timedelta(days=1),
            )
        )
        await session.commit()
    await _log(sessionmaker, use_case="retired-off", age_days=0.1)

    result = await RetentionService(sessionmaker).prune(NOW)

    assert result.payloads_cleared == 1


async def test_a_purged_use_case_falls_back_to_the_installation_default(sessionmaker) -> None:
    """After the second decision there is nothing left to read a period from, and the default is
    the honest consequence — which is part of why a purge is a decision somebody takes rather than
    a cleanup that happens."""
    await _log(sessionmaker, use_case="purged", age_days=10)
    service = RetentionService(sessionmaker, default_retention_days=7)

    result = await service.prune(NOW)

    assert result.payloads_cleared == 1
