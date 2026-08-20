"""The residual bucket: an allowance for spend that belongs to no use case (`FRD-610`).

Asked by the owner after finding that the console's model checks spent money invisibly: *"every
request should be auditable and budgetable"*, and then *"think out a concept for how we can do it
so that money does not leak away from us"*.

The audit half came first. This is the other one, and its rule is a sentence:
**nothing spends outside a bucket.**

Measured before it existed: a request naming no use case returned from `guard()` at the first line
— `if not self._enforce or not use_case` — so break-glass keys, demo traffic and the console's own
checks reserved nothing, settled nothing, and no allowance could ever see them. 59 such rows in the
audit trail of a running installation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from aira_gateway.budgets.service import Amounts, BudgetExceeded, BudgetService
from aira_gateway.db.models import Base, BudgetRead
from aira_gateway.scopes import INSTALLATION, USE_CASE, Scope

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


@pytest.fixture
async def sessionmaker():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _budget(maker: async_sessionmaker[AsyncSession], **fields: object) -> None:
    defaults = {
        "id": 1,
        "use_case": "",
        "scope": INSTALLATION,
        "subject": "",
        "period": "month",
        "enabled": True,
    }
    async with maker() as session:
        session.add(BudgetRead(**{**defaults, **fields}))
        await session.commit()


# == the scope ====================================================================================


def test_an_installation_scope_binds_a_request_that_names_no_use_case() -> None:
    assert Scope.applying(scope=INSTALLATION, use_case="", caller=None) is not None


def test_it_does_not_bind_a_request_that_has_a_use_case() -> None:
    """**Not a global cap.** A use case's traffic books against its own budgets and nothing else;
    this one takes what the others cannot. Making it apply to everything would be a different and
    also useful feature, and would silently halve every use case's allowance."""
    assert Scope.applying(scope=INSTALLATION, use_case="kundenservice", caller=None) is None


def test_its_counter_key_is_its_own_prefix() -> None:
    """**Not `uc:` with an empty name**, and the second reason is the one that would have hurt: the
    key is stored, so `uc:` would be indistinguishable from a use case whose slug somehow emptied —
    and `_delete_usecase` sweeps counters by the `uc:{slug}` prefix, which with an empty slug is
    every counter in the table."""
    scope = Scope.applying(scope=INSTALLATION, use_case="", caller=None)
    assert scope is not None
    assert scope.usage_key == "installation:"
    assert scope.usage_key != "uc:"
    assert scope.label == "installation"


# == what it bounds ===============================================================================


async def test_unattributed_spend_is_now_reserved_against_it(sessionmaker) -> None:
    """The hole, closed. `guard()` used to return at its first line for a request naming no use
    case, so the traffic this budget exists for reserved nothing at all."""
    await _budget(sessionmaker, limit_requests=10)
    service = BudgetService(sessionmaker, enforce=True)

    reservation = await service.guard(None, "someone", estimated=Amounts(requests=1), now=NOW)

    assert reservation.budgets, "an unattributed request must find the installation budget"


async def test_it_refuses_once_it_is_exhausted(sessionmaker) -> None:
    await _budget(sessionmaker, limit_requests=1)
    service = BudgetService(sessionmaker, enforce=True)

    first = await service.guard(None, "someone", estimated=Amounts(requests=1), now=NOW)
    await service.settle(first, tokens=10, cost_nanos=100, now=NOW)

    with pytest.raises(BudgetExceeded) as caught:
        await service.guard(None, "someone", estimated=Amounts(requests=1), now=NOW)
    # Named as the installation's, not as "use case (month)" — a refusal that names the wrong
    # owner sends somebody to edit a budget that was never involved.
    assert "installation" in str(caught.value)


async def test_a_use_case_request_does_not_touch_it(sessionmaker) -> None:
    """The separation, from the other side: an installation budget of one request must not refuse
    the second request a use case makes."""
    await _budget(sessionmaker, limit_requests=1)
    service = BudgetService(sessionmaker, enforce=True)

    first = await service.guard("kundenservice", "a", estimated=Amounts(requests=1), now=NOW)
    await service.settle(first, tokens=10, cost_nanos=100, now=NOW)
    second = await service.guard("kundenservice", "a", estimated=Amounts(requests=1), now=NOW)

    assert second.budgets == [] or not second.budgets
    assert not first.budgets


async def test_an_installation_with_no_such_budget_behaves_as_before(sessionmaker) -> None:
    """**Non-breaking on purpose.** Removing the exemption made unattributed traffic *visible* to
    budgets; it does not make it refused. An installation that has configured none finds nothing
    applicable and its break-glass keys keep working exactly as they did.

    The stronger rule — *a request that fits no bucket does not run* — is a deliberate switch, not
    a side effect of this one (`FRD-610` §3.1).
    """
    service = BudgetService(sessionmaker, enforce=True)

    reservation = await service.guard(None, "someone", estimated=Amounts(requests=1), now=NOW)

    assert not reservation.budgets


async def test_enforcement_off_still_reserves_nothing(sessionmaker) -> None:
    """The master switch keeps its meaning. Worth pinning because the line this feature changed is
    the same line that reads it, and a rewrite that dropped `self._enforce` would turn a switch
    somebody deliberately set into no switch at all."""
    await _budget(sessionmaker, limit_requests=1)
    service = BudgetService(sessionmaker, enforce=False)

    assert not (await service.guard(None, "x", estimated=Amounts(requests=1), now=NOW)).budgets


# == the two scopes keep separate books ===========================================================


async def test_the_two_counters_do_not_share_a_key(sessionmaker) -> None:
    """A use case's spend and the installation's must not accumulate in the same row, or exhausting
    one would exhaust the other — and the audit trail would say two different things about where
    the money went."""
    installation = Scope.applying(scope=INSTALLATION, use_case="", caller=None)
    use_case = Scope.applying(scope=USE_CASE, use_case="kundenservice", caller=None)

    assert installation is not None and use_case is not None
    assert installation.usage_key != use_case.usage_key
    assert installation.bucket_key != use_case.bucket_key
