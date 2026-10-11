"""Erasure does not depend on who may read: all four content modes, one clock (`FRD-404`/`FRD-622`).

**Why this file exists.** Who may read a stored prompt and when it is deleted are two settings on
one panel, and the fourth reading mode — *kept for oversight, read by nobody in the use case* —
is the one that invites the wrong instinct in both directions:

- *"nobody here can see it, so it hardly matters how long it is kept"* — it matters more, not
  less: the people it is about cannot look at it either, and a deadline nobody enforces is a
  deadline nobody can be held to;
- *"it is only for oversight, so keep it"* — a visibility is not a legal basis, and `retention_days`
  is what the data subject was told.

So the question each case here asks is deliberately narrow: **does the visibility change what is
deleted, or when?** It must not. What *does* decide erasure is the other switch — storage off is a
period of zero, whatever the visibility said — and the read record is what survives all of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_gateway.db.base import Base
from aira_gateway.db.models import PayloadAccess, RequestLog, UseCaseRead
from aira_gateway.payloads import VISIBILITIES, PayloadVisibility
from aira_gateway.retention import RetentionService

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
PROMPT = {"contents": [{"parts": [{"text": "meine Personalnummer ist 4711"}]}]}


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


async def _use_case(
    sessionmaker,
    *,
    visibility: str,
    store_payloads: bool = True,
    retention_days: int = 7,
) -> None:
    async with sessionmaker() as session:
        session.add(
            UseCaseRead(
                slug="uc",
                name="uc",
                store_payloads=store_payloads,
                payload_visibility=visibility,
                retention_days=retention_days,
            )
        )
        await session.commit()


async def _log(sessionmaker, *, age_days: float, row_id: str = "row-1") -> None:
    async with sessionmaker() as session:
        session.add(
            RequestLog(
                id=row_id,
                subject="alice",
                auth_method="api_key",
                use_case="uc",
                api="gemini",
                operation="generateContent",
                model="mock-1",
                status=200,
                outcome="served",
                total_tokens=30,
                cost_nanos=85_000,
                created_at=NOW - timedelta(days=age_days),
                request_payload=PROMPT,
                response_payload={"text": "…"},
            )
        )
        await session.commit()


async def _payloads(sessionmaker) -> list[dict | None]:
    async with sessionmaker() as session:
        rows = (await session.execute(select(RequestLog).order_by(RequestLog.id))).scalars()
        return [row.request_payload for row in rows]


# ---- the default, which decides for every row nobody configured ----------------------------


async def test_a_use_case_the_event_said_nothing_about_is_readable_by_its_members(
    sessionmaker,
) -> None:
    """The column's own default, asserted where it can be seen: a read-model row written without
    the field. It is the value every existing use case gets on upgrade, and the one a test that
    passes the field explicitly can never check — which is most of them.

    `members` and not the narrowest, because this is the behaviour that existed before the setting
    did: a restriction is imposed, never inherited from a deployment.
    """
    async with sessionmaker() as session:
        session.add(UseCaseRead(slug="plain", name="plain"))
        await session.commit()
        row = (await session.execute(select(UseCaseRead))).scalar_one()

    assert row.payload_visibility == PayloadVisibility.EVERY_MEMBER.value


# ---- the clock is the same in every mode ---------------------------------------------------


@pytest.mark.parametrize("visibility", sorted(VISIBILITIES))
async def test_the_period_is_the_period_whoever_may_read_it(sessionmaker, visibility: str) -> None:
    """Parametrised over **every** value rather than over the new one: the claim is that erasure
    does not look at this field at all, and a test of one value is a test of one value."""
    await _use_case(sessionmaker, visibility=visibility, retention_days=7)
    await _log(sessionmaker, age_days=8)

    result = await RetentionService(sessionmaker).prune(NOW)

    assert result.payloads_cleared == 1
    assert await _payloads(sessionmaker) == [None]


@pytest.mark.parametrize("visibility", sorted(VISIBILITIES))
async def test_nothing_inside_the_period_is_taken_early_either(
    sessionmaker, visibility: str
) -> None:
    """The other side of the boundary, because a mode that quietly shortened retention would look
    like a feature ("it is not readable anyway") and would be data loss."""
    await _use_case(sessionmaker, visibility=visibility, retention_days=7)
    await _log(sessionmaker, age_days=6.9)

    assert (await RetentionService(sessionmaker).prune(NOW)).payloads_cleared == 0
    assert await _payloads(sessionmaker) == [PROMPT]


async def test_two_use_cases_with_the_same_period_are_cleared_alike(sessionmaker) -> None:
    """The comparison, which is stronger than two separate assertions: the only difference between
    these two use cases is who may read them, and the sweep must not be able to tell."""
    async with sessionmaker() as session:
        session.add(
            UseCaseRead(slug="uc", name="uc", retention_days=7, payload_visibility="members")
        )
        session.add(
            UseCaseRead(
                slug="uc-oversight",
                name="uc-oversight",
                retention_days=7,
                payload_visibility="oversight",
            )
        )
        await session.commit()
    await _log(sessionmaker, age_days=8, row_id="row-members")
    async with sessionmaker() as session:
        session.add(
            RequestLog(
                id="row-oversight",
                subject="alice",
                auth_method="api_key",
                use_case="uc-oversight",
                api="gemini",
                operation="generateContent",
                model="mock-1",
                status=200,
                outcome="served",
                created_at=NOW - timedelta(days=8),
                request_payload=PROMPT,
                response_payload={"text": "…"},
            )
        )
        await session.commit()

    await RetentionService(sessionmaker).prune(NOW)

    assert await _payloads(sessionmaker) == [None, None]


# ---- storage off is a period of zero, in every mode ----------------------------------------


@pytest.mark.parametrize("visibility", sorted(VISIBILITIES))
async def test_switching_storage_off_clears_what_is_there_whatever_the_mode_was(
    sessionmaker, visibility: str
) -> None:
    """**The deletion an operator actually performs.** Turning storage off means *do not keep
    prompts*, so what is already there goes on the next run rather than lingering for the rest of
    the old period — and the mode it was readable under does not buy it another day.

    A request from a **moment ago** on purpose: inside any period, so the only thing that can clear
    it is the zero the switch means. A moment and not zero, because the sweep clears what is older
    than its own instant and a row stamped with exactly that instant is not older than it.
    """
    await _use_case(sessionmaker, visibility=visibility, store_payloads=False, retention_days=3650)
    await _log(sessionmaker, age_days=0.001)

    result = await RetentionService(sessionmaker).prune(NOW)

    assert result.payloads_cleared == 1
    assert await _payloads(sessionmaker) == [None]


async def test_oversight_does_not_exempt_a_use_case_from_erasure(sessionmaker) -> None:
    """Stated as its own case because it is the instinct this mode invites: content kept *for*
    oversight is still somebody's data, and `retention_days` is what they were told."""
    await _use_case(
        sessionmaker, visibility=PayloadVisibility.OVERSIGHT_ONLY.value, retention_days=1
    )
    await _log(sessionmaker, age_days=2)

    assert (await RetentionService(sessionmaker).prune(NOW)).payloads_cleared == 1
    assert await _payloads(sessionmaker) == [None]


# ---- what erasure leaves behind -------------------------------------------------------------


async def test_the_record_of_who_read_it_outlives_the_content(sessionmaker) -> None:
    """`ADR-0016`: content is readable **because** every read is written down, and *"that record is
    not deleted"*. In this mode it is the only trace of the only people who could look — so a sweep
    that took it with the payload would erase the evidence and keep nothing but the setting.
    """
    await _use_case(sessionmaker, visibility="oversight", retention_days=1)
    await _log(sessionmaker, age_days=2)
    async with sessionmaker() as session:
        session.add(
            PayloadAccess(
                request_log_id="row-1",
                use_case="uc",
                subject="sec",
                username="sec",
                ground="incident",
                roles="it-security",
            )
        )
        await session.commit()

    await RetentionService(sessionmaker).prune(NOW)

    async with sessionmaker() as session:
        reads = list((await session.execute(select(PayloadAccess))).scalars())
    assert [(read.request_log_id, read.ground) for read in reads] == [("row-1", "incident")]
    assert await _payloads(sessionmaker) == [None], "the content itself should be gone"


async def test_record_retention_takes_the_row_itself_in_every_mode(sessionmaker) -> None:
    """The installation-wide clock, which is a different switch and a different act: the row goes,
    not just its payload. A visibility must not keep a row alive either."""
    await _use_case(sessionmaker, visibility="oversight", retention_days=3650)
    await _log(sessionmaker, age_days=400)

    result = await RetentionService(sessionmaker, log_retention_days=365).prune(NOW)

    assert result.rows_deleted == 1
    assert await _payloads(sessionmaker) == []
