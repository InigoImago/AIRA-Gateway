import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.service import BudgetService
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead, BudgetUsage

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
NEXT_DAY = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


async def _budgets(sessionmaker: async_sessionmaker) -> list[BudgetRead]:
    """The configured budgets, as `guard` would have resolved them."""
    async with sessionmaker() as session:
        return list((await session.execute(select(BudgetRead))).scalars())


async def _add(sessionmaker: async_sessionmaker, **fields: object) -> None:
    defaults = {
        "use_case": "uc",
        "scope": "use_case",
        "subject": "",
        "period": "day",
        "limit_tokens": None,
        "limit_requests": None,
        "enabled": True,
    }
    defaults.update(fields)
    async with sessionmaker() as session:
        session.add(BudgetRead(**defaults))  # type: ignore[arg-type]
        await session.commit()


async def test_request_budget_blocks_after_limit(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=2)
    service = BudgetService(sessionmaker)

    for _ in range(2):
        budgets = (await service.guard("uc", "bob", NOW)).budgets
        await service.record(budgets, 10, now=NOW)

    with pytest.raises(BudgetExceeded, match="Request budget"):
        await service.guard("uc", "bob", NOW)


async def test_token_budget_blocks_once_exceeded(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_tokens=100)
    service = BudgetService(sessionmaker)

    budgets = (await service.guard("uc", "bob", NOW)).budgets  # 0 < 100
    await service.record(budgets, 60, now=NOW)  # 60
    budgets = (await service.guard("uc", "bob", NOW)).budgets  # 60 < 100 still allowed
    await service.record(budgets, 60, now=NOW)  # 120

    with pytest.raises(BudgetExceeded, match="Token budget"):
        await service.guard("uc", "bob", NOW)


async def test_member_scope_only_applies_to_subject(sessionmaker) -> None:
    await _add(sessionmaker, id=1, scope="member", subject="bob", limit_requests=1)
    service = BudgetService(sessionmaker)

    # alice is not the budget's subject → not applicable
    assert (await service.guard("uc", "alice", NOW)).budgets == []

    budgets = (await service.guard("uc", "bob", NOW)).budgets
    await service.record(budgets, 5, now=NOW)
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "bob", NOW)


async def test_period_rolls_over(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=1)
    service = BudgetService(sessionmaker)
    await service.record((await service.guard("uc", "bob", NOW)).budgets, 1, now=NOW)
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "bob", NOW)
    # next day → fresh counter
    assert (await service.guard("uc", "bob", NEXT_DAY)).budgets != []


async def test_disabled_budget_is_ignored(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=1, enabled=False)
    service = BudgetService(sessionmaker)
    assert (await service.guard("uc", "bob", NOW)).budgets == []


async def test_enforce_off_returns_empty(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=1)
    service = BudgetService(sessionmaker, enforce=False)
    assert (await service.guard("uc", "bob", NOW)).budgets == []


async def test_no_use_case_returns_empty(sessionmaker) -> None:
    service = BudgetService(sessionmaker)
    assert (await service.guard(None, "bob", NOW)).budgets == []


async def test_record_no_budgets_is_noop(sessionmaker) -> None:
    service = BudgetService(sessionmaker)
    await service.record([], 100, now=NOW)  # must not raise


async def test_month_period_key(sessionmaker) -> None:
    await _add(sessionmaker, id=1, period="month", limit_requests=1)
    service = BudgetService(sessionmaker)
    await service.record((await service.guard("uc", "bob", NOW)).budgets, 1, now=NOW)
    # same month, next day → still counts (monthly window)
    with pytest.raises(BudgetExceeded):
        await service.guard("uc", "bob", NEXT_DAY)


async def test_usage_reports_current_period(sessionmaker) -> None:
    await _add(sessionmaker, id=1, limit_requests=5)
    service = BudgetService(sessionmaker)
    await service.record((await service.guard("uc", "bob", NOW)).budgets, 30, now=NOW)
    assert await service.usage("uc", NOW) == [
        {
            "id": 1,
            "measured_for": "",
            "used_tokens": 30,
            "used_requests": 1,
            "used_cost_nanos": 0,
            "used_cost": "0.00",
            "unpriced_requests": 1,
        }
    ]


# == the counter is accumulated in the database, not in Python (found live) =======================


async def test_two_concurrent_first_writes_do_not_collide(sessionmaker) -> None:
    """Found by twenty concurrent requests against a fresh budget on the live stack.

    ``record`` read the counter, then inserted it when absent, then committed. Two requests
    arriving as the **first** of a period both found no row and both inserted one; the loser got a
    `UniqueViolation` that reached the caller as a **500** — for a request the gateway had already
    served and charged for.
    """
    await _add(sessionmaker, id=1, limit_requests=100)
    service = BudgetService(sessionmaker)
    budgets = await _budgets(sessionmaker)

    await asyncio.gather(*[service.record(budgets, tokens=1, cost_nanos=10) for _ in range(8)])

    async with sessionmaker() as session:
        usage = (await session.execute(select(BudgetUsage))).scalars().all()
    assert len(usage) == 1, "the counter was written more than once for one scope+period"


async def test_no_increment_is_lost_when_writes_overlap(sessionmaker) -> None:
    """The quieter half of the same defect, and the one with no error attached.

    ``record.tokens += n`` reads the loaded value and writes an **absolute** new one, so two
    overlapping writes discard one increment. There is nothing to see: the counter that is meant
    to be the system of record simply drifts below the truth, in the direction that spends money,
    under exactly the load that makes a budget matter.
    """
    await _add(sessionmaker, id=1, limit_requests=1000)
    service = BudgetService(sessionmaker)
    budgets = await _budgets(sessionmaker)

    await asyncio.gather(*[service.record(budgets, tokens=10, cost_nanos=5) for _ in range(20)])

    async with sessionmaker() as session:
        usage = (await session.execute(select(BudgetUsage))).scalars().one()
    assert usage.tokens == 200, f"increments were lost: {usage.tokens} instead of 200"
    assert usage.requests == 20
    assert usage.cost_nanos == 100


async def test_an_unpriced_request_is_still_counted_apart_by_the_upsert(sessionmaker) -> None:
    """The rule the rewrite had to preserve: unpriced is not free (`FRD-403`)."""
    await _add(sessionmaker, id=1, limit_requests=1000)
    service = BudgetService(sessionmaker)
    budgets = await _budgets(sessionmaker)

    await service.record(budgets, tokens=5, cost_nanos=None)
    await service.record(budgets, tokens=5, cost_nanos=7)

    async with sessionmaker() as session:
        usage = (await session.execute(select(BudgetUsage))).scalars().one()
    assert (usage.cost_nanos, usage.unpriced_requests) == (7, 1)


async def test_one_per_person_row_gives_every_caller_their_own_counter(sessionmaker) -> None:
    """The scope an administrator wants far more often than either of the others: a fair share per
    head, without a list of heads to keep up to date, and it keeps applying to people who join
    later.

    Asserted as **two callers against one configured row**, which is the whole claim — and the
    thing a `use_case` row cannot do: that one is a shared pot, where the first caller to arrive
    can spend all of it.
    """
    await _add(sessionmaker, id=1, scope="each_member", subject="", limit_requests=1)
    service = BudgetService(sessionmaker)

    # Alice uses hers up…
    await service.settle(await service.guard("uc", "alice", NOW), 10, now=NOW)
    with pytest.raises(BudgetExceeded, match="Request budget"):
        await service.guard("uc", "alice", NOW)

    # …and Bob still has his, from that same single row.
    assert (await service.guard("uc", "bob", NOW)).budgets


async def test_a_per_person_budget_reports_no_figure_to_somebody_it_does_not_bind(
    sessionmaker,
) -> None:
    """One configured row is N counters, so `usage` has to be asked *whose*.

    An oversight reader is a member of nothing and has no figure here. Reporting zero would be
    indistinguishable from a full, untouched allowance — `FRD-603`'s rule that unknown is never
    rendered as zero, in the one place where zero is also a plausible real answer.
    """
    await _add(sessionmaker, id=1, scope="each_member", subject="", limit_requests=5)
    service = BudgetService(sessionmaker)
    await service.settle(await service.guard("uc", "alice", NOW), 7, now=NOW)

    mine = (await service.usage("uc", NOW, subject="alice"))[0]
    assert (mine["used_tokens"], mine["measured_for"]) == (7, "alice")

    someone_elses = (await service.usage("uc", NOW, subject="bob"))[0]
    assert someone_elses["used_tokens"] == 0, "counters must not be shared between people"

    nobodys = (await service.usage("uc", NOW))[0]
    assert nobodys["used_tokens"] is None and nobodys["measured_for"] is None


async def test_a_pipeline_call_is_booked_against_the_caller_own_per_person_budget(
    sessionmaker,
) -> None:
    """`FRD-125b`'s path, which reaches the counter through a different door.

    `book_side_call` resolves the applicable budgets itself rather than being handed a reservation,
    so it is the one place a per-person row could be booked under the wrong key without any other
    test noticing — and the consequence would be quiet: a use case's governing cost counted against
    nobody, which is exactly what `FRD-125b` was written to stop.
    """
    await _add(sessionmaker, id=1, scope="each_member", subject="", limit_tokens=1000)
    service = BudgetService(sessionmaker)

    await service.book_side_call("uc", "alice", tokens=40, cost_nanos=500, now=NOW)

    mine = (await service.usage("uc", NOW, subject="alice"))[0]
    assert (mine["used_tokens"], mine["used_requests"]) == (40, 0), (
        "the classifier's tokens are consumption; the caller still made one request, not two"
    )
    assert (await service.usage("uc", NOW, subject="bob"))[0]["used_tokens"] == 0


# == reading somebody else's allowance (found live, on the demo's own front page) =================


async def test_a_member_budget_naming_somebody_else_is_readable(sessionmaker) -> None:
    """Opening a use case must not depend on who its budgets are about.

    Found on 2026-08-11 by logging into the console as `admin` and clicking the first use case:
    **HTTP 500**, on the screen a stakeholder walkthrough opens with. `usage()` passed *the
    reader* as the caller for every budget row, so a `member` row naming anybody else resolved to
    no scope at all and `_scope_key`'s assertion fired.

    Introduced with the per-head scope (`744337f`) the same day. The distinction it missed:
    `each_member` is one row per reader and therefore depends on who is asking; a `member` row
    **names its own subject** and does not. A reader may see how much the named member used —
    which is what the panel has always shown, and what it went back to showing.

    The assertion was right to be there. It is an invariant of the *reservation* path, where a row
    that does not bind the caller is a programming error. Reading is a different question, and
    asking it in the reservation's vocabulary is what produced a 500 out of a legitimate view.
    """
    await _add(sessionmaker, id=1, scope="member", subject="alice", limit_tokens=100)
    service = BudgetService(sessionmaker)
    await service.record((await service.guard("uc", "alice", NOW)).budgets, 30, now=NOW)

    # `bob` is not `alice`, and looks at the same use case.
    figures = await service.usage("uc", NOW, subject="bob", username="bob")

    assert len(figures) == 1
    assert figures[0]["measured_for"] == "alice"
    # Alice's consumption, not zero and not an error: the row is about her, and it says so.
    assert figures[0]["used_tokens"] == 30


async def test_a_use_case_budget_is_readable_by_anyone_who_may_see_it(sessionmaker) -> None:
    """The same shape one scope over, and the one an oversight role hits first: a `use_case` row
    names nobody, so it cannot depend on the reader either."""
    await _add(sessionmaker, id=1, limit_tokens=100)
    service = BudgetService(sessionmaker)
    await service.record((await service.guard("uc", "alice", NOW)).budgets, 12, now=NOW)

    figures = await service.usage("uc", NOW, subject="somebody-else", username="somebody-else")

    assert figures[0]["used_tokens"] == 12
