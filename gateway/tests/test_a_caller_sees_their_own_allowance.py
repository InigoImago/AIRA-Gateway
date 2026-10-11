"""A caller can ask what they are allowed and what is left (`FRD-139`).

**Why it exists.** Agentic coding clients spend money in a loop. The owner's use cases run
OpenCode, which can show a budget in its status bar but only tallies its *own* session — and for a
custom OpenAI-compatible provider its cost display does not work at all. So the only figure worth
showing a developer is the one that will actually refuse them, and until now the only way to read
it was the console: a browser, a login, and a permission a key does not have.

`GET /v1beta/me/budget` answers from the credential that asked. No slug in the path — an agent was
configured with a URL and a key and does not know one — and no permission beyond being
authenticated, because it is the caller's own figure.

**Two figures, two sources, labelled apart.** `allowances` are the counters enforcement reads;
`this_period` is what the audit trail records. They answer different questions and may differ, and
a client that showed one as the other would be showing a number nobody enforces.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import anyio
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_common.money import to_nanos
from aira_gateway.app import create_app
from aira_gateway.auth.dependencies import require_principal
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all
from aira_gateway.db.models import BudgetRead, RequestLog

URL = "/v1beta/me/budget"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

#: An API key's subject **is** its owner's username (`scopes.person`), which is why a key and a
#: login land on the same allowance. Spelled out in the doubles so the test cannot pass by using
#: one shape everywhere.
KEY = Principal(subject="erika", method="api_key", use_cases=("coding",), credential="aira_ab12")
TOKEN = Principal(
    subject="2b9f-uuid", username="erika", method="oidc", use_cases=("coding",), roles=()
)
COLLEAGUE = Principal(subject="max", method="api_key", use_cases=("coding",))


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


def _client(principal: Principal, *, enforce: bool = True) -> TestClient:
    """The app with one thing replaced: who the caller is.

    **The first version of this helper also installed a middleware that wrote the attribution**,
    and that is why these tests passed against an endpoint that answered `use_case: null` to every
    real request: `require_attribution` is mounted on the Gemini router and this endpoint is not
    under it, so nothing resolved a use case at all. The live stack found it in one call.

    So the only stand-in here is the credential. Which use case that credential is *about* is
    resolved by the surface's own dependency, which is the thing under test.
    """
    app = create_app(
        GatewaySettings(auth_required=False, enforce_budgets=enforce, log_queue_size=0)
    )
    app.dependency_overrides[require_principal] = lambda: principal
    return TestClient(app)


#: What a console token sends to say which use case it means (`FRD-102`). An API key needs none —
#: it is bound to one — and the difference is why both are exercised below.
SELECTOR = {"X-AIRA-Use-Case": "coding"}


async def _budget(
    sessions,
    *,
    scope: str,
    limit_cost: str | None = "50.00",
    limit_tokens: int | None = None,
    limit_requests: int | None = None,
    period: str = "month",
    subject: str = "",
    use_case: str = "coding",
) -> int:
    async with sessions() as session:
        row = BudgetRead(
            use_case=use_case,
            scope=scope,
            subject=subject,
            period=period,
            limit_cost_nanos=None if limit_cost is None else to_nanos(limit_cost),
            limit_tokens=limit_tokens,
            limit_requests=limit_requests,
            enabled=True,
        )
        session.add(row)
        await session.commit()
        return int(row.id)


async def _spent(sessions, *, subject: str, username: str | None, cost: str, use_case="coding"):
    async with sessions() as session:
        session.add(
            RequestLog(
                subject=subject,
                username=username,
                auth_method="api_key",
                use_case=use_case,
                api="gemini",
                operation="generateContent",
                model="mock-1",
                status=200,
                outcome="served",
                prompt_tokens=100,
                completion_tokens=200,
                total_tokens=300,
                cost_nanos=to_nanos(cost),
                created_at=NOW,
            )
        )
        await session.commit()


def _seed(client: TestClient, coroutine) -> None:  # noqa: ANN001
    with anyio.from_thread.start_blocking_portal() as portal:
        portal.call(lambda: coroutine(client.app.state.db_sessionmaker))


# == what a developer actually asks ==============================================================


def test_a_key_is_told_its_own_limit_and_what_is_left() -> None:
    """The case the feature was asked for: one shared pot, a key, and a number to put in a status
    bar without a browser or a permission."""
    with _client(KEY) as client:
        _seed(client, lambda s: _budget(s, scope="use_case", limit_cost="50.00"))
        _seed(client, lambda s: _spent(s, subject="erika", username=None, cost="3.40"))
        body = client.get(URL).json()

    assert body["use_case"] == "coding"
    assert body["person"] == "erika"
    assert body["enforced"] is True
    (allowance,) = body["allowances"]
    assert allowance["scope"] == "use_case"
    assert allowance["limit_cost"] == "50.00"
    assert allowance["period"] == "month"
    assert allowance["period_ends"] == "2026-11-01T00:00:00+00:00"
    # Nothing has been booked against the counter yet, so what is left is the whole limit — and
    # the audit trail already records the spend. Two sources, and this is the difference they are
    # labelled apart for.
    assert allowance["remaining_cost"] == "50.00"
    assert body["this_period"]["cost"] == "3.40"


def test_the_dimensions_a_budget_does_not_cap_are_absent_rather_than_infinite() -> None:
    """`None` and not a large number: *"tokens are not limited here"* and *"you have plenty of
    tokens left"* are different answers, and a bar drawn from the second invents a limit."""
    with _client(KEY) as client:
        _seed(
            client,
            lambda s: _budget(s, scope="use_case", limit_cost=None, limit_requests=1_000),
        )
        (allowance,) = client.get(URL).json()["allowances"]

    assert allowance["limit_cost"] is None
    assert allowance["remaining_cost"] is None
    assert allowance["limit_requests"] == 1_000
    assert allowance["remaining_requests"] == 1_000
    assert allowance["limit_tokens"] is None
    assert allowance["remaining_tokens"] is None


def test_the_figures_are_the_callers_own_and_not_the_use_cases() -> None:
    """`this_period` is **this caller's** spend, never the use case's total.

    Two reasons it must be, and they point the same way: a status bar showing the team's spend as
    yours is wrong, and a use case may withhold other people's figures altogether (`FRD-622`
    FR-1c) — an endpoint that answered with the sum would route around that on the one surface
    nobody looks at.
    """
    with _client(KEY) as client:
        _seed(client, lambda s: _spent(s, subject="erika", username=None, cost="3.40"))
        _seed(client, lambda s: _spent(s, subject="max", username=None, cost="99.00"))
        body = client.get(URL).json()

    assert body["this_period"]["cost"] == "3.40"
    assert body["this_period"]["requests"] == 1


def test_a_use_case_with_no_budget_still_says_what_was_used() -> None:
    """The owner's choice (2026-10-11): usage without a limit rather than an empty answer. A
    developer on a use case nobody capped still wants to know what they have spent — and a client
    showing *"3.40 € this month"* with no bar beside it is telling the truth."""
    with _client(KEY) as client:
        _seed(client, lambda s: _spent(s, subject="erika", username=None, cost="3.40"))
        body = client.get(URL).json()

    assert body["allowances"] == []
    assert body["this_period"]["cost"] == "3.40"
    assert body["this_period"]["requests"] == 1


def test_a_per_member_budget_answers_with_the_callers_own_counter() -> None:
    """One configured row, one counter per head (`FRD-401`). The figure must be the caller's, and
    `measured_for` says whose it is — a status bar showing a colleague's remainder would be worse
    than showing none."""
    with _client(KEY) as client:
        _seed(client, lambda s: _budget(s, scope="each_member", limit_cost="10.00"))
        (allowance,) = client.get(URL).json()["allowances"]

    assert allowance["scope"] == "each_member"
    assert allowance["measured_for"] == "erika"


def test_a_colleague_asking_gets_their_own_counter_and_not_erikas() -> None:
    """The same configured budget, a different caller: the endpoint is keyed on who asked."""
    with _client(COLLEAGUE) as client:
        _seed(client, lambda s: _budget(s, scope="each_member", limit_cost="10.00"))
        body = client.get(URL).json()

    assert body["person"] == "max"
    assert body["allowances"][0]["measured_for"] == "max"


def test_a_key_and_a_login_land_on_the_same_allowance() -> None:
    """`scopes.person`: an API key's subject is its owner's username, an OIDC token's is a
    directory id. Keying on the subject gave one human two allowances; this asserts the figure a
    developer sees in their terminal is the figure the console shows them."""
    seen = []
    for principal, headers in ((KEY, {}), (TOKEN, SELECTOR)):
        with _client(principal) as client:
            _seed(client, lambda s: _budget(s, scope="each_member", limit_cost="10.00"))
            _seed(client, lambda s: _spent(s, subject="erika", username="erika", cost="1.00"))
            body = client.get(URL, headers=headers).json()
        seen.append((body["use_case"], body["person"], body["allowances"][0]["measured_for"]))

    # The key needed no selector and the token sent one; both land on one human's allowance.
    assert seen == [("coding", "erika", "erika"), ("coding", "erika", "erika")]


def test_the_answer_says_when_a_limit_is_not_enforced() -> None:
    """`FRD-610` §3.3: with `AIRA_ENFORCE_BUDGETS` off nothing refuses, and a client drawing a bar
    that implies a stop would be wrong in the direction that matters. The console says nothing
    about this today; a caller's own answer does."""
    with _client(KEY, enforce=False) as client:
        _seed(client, lambda s: _budget(s, scope="use_case", limit_cost="50.00"))
        body = client.get(URL).json()

    assert body["enforced"] is False
    # The limit is still reported: it is what somebody configured, and it will refuse the moment
    # enforcement is switched on.
    assert body["allowances"][0]["limit_cost"] == "50.00"


def test_the_period_a_day_budget_rolls_over_is_the_next_midnight() -> None:
    """The second question a refused caller asks is *when does it come back*. UTC, and the first
    moment of the next period rather than the last of this one."""
    with _client(KEY) as client:
        _seed(client, lambda s: _budget(s, scope="use_case", period="day", limit_cost="5.00"))
        (allowance,) = client.get(URL).json()["allowances"]

    assert allowance["period"] == "day"
    assert allowance["period_ends"].endswith("T00:00:00+00:00")


def test_it_needs_a_credential_and_no_permission_beyond_it() -> None:
    """A key holds no console role at all. If this needed one, the feature would not exist for the
    callers it was built for — and it must still refuse somebody with no credential."""
    app = create_app(GatewaySettings(auth_required=True, log_queue_size=0))
    with TestClient(app) as client:
        anonymous = client.get(URL)

    assert anonymous.status_code in (401, 403), anonymous.text


def test_a_credential_bound_to_no_use_case_is_answered_rather_than_refused() -> None:
    """A token that selected no use case has no budget to report and still has figures. Answered
    with an empty list rather than a 400: a client polling a status bar must not have to tell a
    configuration mistake from an outage.

    It is the surface's own rule that allows this — a `GET` *"spends nothing"*
    (`must_name_a_use_case`), so the resolver does not insist on a selector here as it does on a
    request that costs money.
    """
    nobody = Principal(subject="wanderer", method="oidc", use_cases=())
    with _client(nobody) as client:
        body = client.get(URL).json()

    assert body["use_case"] is None
    assert body["allowances"] == []
    assert body["this_period"]["requests"] == 0


def test_the_use_case_comes_from_the_surfaces_own_resolver() -> None:
    """**The defect the live stack found**, as a test that would have found it.

    Asserted on a *bound API key with no selector*, which is the shape an agent actually has: the
    answer has to name the use case the key belongs to. The old harness wrote the attribution
    itself, so this held for a reason that had nothing to do with the endpoint.
    """
    with _client(KEY) as client:
        _seed(client, lambda s: _budget(s, scope="use_case", limit_cost="50.00"))
        body = client.get(URL).json()

    assert body["use_case"] == "coding", body
    assert body["allowances"], "no allowance, because no use case was resolved"


def test_a_token_naming_a_use_case_it_may_not_reach_is_refused() -> None:
    """The authorisation the resolver applies, inherited rather than re-implemented: this endpoint
    must not become a way to ask about somebody else's use case by naming it."""
    outsider = Principal(subject="eve", method="oidc", use_cases=("other",))
    with _client(outsider) as client:
        response = client.get(URL, headers=SELECTOR)

    assert response.status_code in (400, 403, 404), response.text


async def test_what_is_left_moves_when_something_is_actually_booked(sessions) -> None:
    """**The property a developer is relying on**, and the one the cases above cannot show: they
    seed audit rows, which are the record, while `remaining_*` reads the **counter** enforcement
    books against. If the two were ever wired to different keys the figures above would still look
    right and a status bar would show a full allowance up to the moment of refusal.

    So this one books through the service — `guard` then `record`, which is what a served request
    does — and asks the same question the endpoint asks.
    """
    from aira_gateway.budgets.service import BudgetService

    async with sessions() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case="coding",
                scope="each_member",
                subject="",
                period="month",
                limit_requests=10,
                limit_cost_nanos=to_nanos("50.00"),
                enabled=True,
            )
        )
        await session.commit()

    service = BudgetService(sessions)
    reservation = await service.guard("coding", "erika", NOW)
    # `subject` is what keys an `each_member` counter — the same name the endpoint asks with.
    await service.record(
        reservation.budgets, 300, now=NOW, cost_nanos=to_nanos("3.40"), subject="erika"
    )

    (allowance,) = await service.usage("coding", NOW, subject="erika")

    assert allowance["used_requests"] == 1
    assert allowance["remaining_requests"] == 9
    assert allowance["used_cost"] == "3.40"
    assert allowance["remaining_cost"] == "46.60"
    # And a colleague's counter is untouched: one configured row, one counter per head.
    (theirs,) = await service.usage("coding", NOW, subject="max")
    assert theirs["used_requests"] == 0
    assert theirs["remaining_requests"] == 10


async def test_a_settle_past_the_limit_is_reported_as_it_is(sessions) -> None:
    """Enforcement refuses *at* the limit, so going past it takes a settle correcting a
    reservation upward — a long answer costing more than the estimate. Reported as a negative
    remainder rather than clamped: "spent to the last cent" and "spent more than allowed" are
    different facts, and the second is the one somebody has to explain.
    """
    from aira_gateway.budgets.service import BudgetService

    async with sessions() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case="coding",
                scope="use_case",
                subject="",
                period="month",
                limit_cost_nanos=to_nanos("1.00"),
                enabled=True,
            )
        )
        await session.commit()

    service = BudgetService(sessions)
    reservation = await service.guard("coding", "erika", NOW)
    await service.record(reservation.budgets, 10, now=NOW, cost_nanos=to_nanos("1.50"))

    (allowance,) = await service.usage("coding", NOW, subject="erika")

    assert allowance["used_cost"] == "1.50"
    assert allowance["remaining_cost_nanos"] == to_nanos("1.00") - to_nanos("1.50")
    assert allowance["remaining_cost"] == "-0.50"
