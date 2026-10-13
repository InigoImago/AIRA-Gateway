"""Reporting over the real service and real Postgres (FRD-601).

Two things only show up here. The aggregation is SQL, and SQLite answers some of it differently —
integer division, null handling in `sum`, and the ordering of a `GROUP BY` with a `coalesce` in
its `ORDER BY` are all places where a hermetic pass proves less than it looks like it does. And
the visibility rule is only meaningful against a token the realm actually issued: the hermetic
tests construct a `Principal` directly, which cannot show that the role survives the round trip.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.money import to_nanos

from . import installation
from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration


@pytest.fixture
async def window_slug(engine: AsyncEngine):
    """A use case name for rows written into a future window, and those rows removed afterwards.

    The windows are years ahead so that nothing else writes into them — which also makes every row
    left there the newest in the audit trail until that year arrives: retention prunes by age and
    never reaches it, and a newest-first view opens on it.
    """
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    installation.remember("read_model", slug)
    yield slug
    await installation.delete_gateway_rows(engine, slug)


async def _log(
    engine: AsyncEngine,
    *,
    use_case: str,
    subject: str,
    when: datetime,
    cost: int | None,
    model: str = "mock-1",
    status: int = 200,
    latency: int = 40,
    outcome: str | None = None,
) -> None:
    """One audit row as the writer records it — with an outcome, which every row since `FRD-122`
    carries: `served`, or the refusal's own (a rate limit unless another is named)."""
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO request_logs (id, created_at, subject, auth_method, use_case, api,"
                " operation, model, status, prompt_tokens, completion_tokens, total_tokens,"
                " cost_nanos, latency_ms, outcome)"
                " VALUES (:id, :ts, :subject, 'api_key', :uc, 'gemini', 'generateContent',"
                " :model, :status, 10, 20, 30, :cost, :latency, :outcome)"
            ),
            {
                "id": f"rep-{uuid.uuid4().hex[:12]}",
                "ts": when,
                "subject": subject,
                "uc": use_case,
                "model": model,
                "status": status,
                "cost": cost,
                "latency": latency,
                "outcome": outcome or ("served" if status == 200 else "rate_limited"),
            },
        )


async def _report(token: str, start: datetime, end: datetime) -> dict:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get(
            "/v1beta/reporting",
            params={"from": start.isoformat(), "to": end.isoformat()},
            headers={"authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_the_figures_match_what_was_recorded(
    engine: AsyncEngine, governance_token: str, window_slug: str
) -> None:
    """Hand-computed against real Postgres: the sums, the split, the unpriced count, and the
    money — all of which SQLite could get right for reasons Postgres does not share."""
    slug = window_slug
    start = datetime(2031, 3, 1, tzinfo=UTC)  # a window nothing else in the stack writes into
    end = start + timedelta(days=31)

    await _log(engine, use_case=slug, subject="alice", when=start, cost=to_nanos("1.50"))
    await _log(
        engine, use_case=slug, subject="bob", when=start + timedelta(days=1), cost=to_nanos("2.25")
    )
    await _log(engine, use_case=slug, subject="bob", when=start, cost=None)  # unpriced
    await _log(engine, use_case=slug, subject="bob", when=start, cost=None, status=429)
    await _log(
        engine,
        use_case=slug,
        subject="bob",
        when=start,
        cost=None,
        status=502,
        outcome="upstream_error",
    )

    report = await _report(governance_token, start, end)
    mine = next(row for row in report["by_use_case"] if row["key"] == slug)

    assert mine["requests"] == 5
    assert mine["prompt_tokens"] == 50
    assert mine["completion_tokens"] == 100
    assert mine["total_tokens"] == 150
    assert mine["cost"] == "3.75"
    assert mine["cost_nanos"] == to_nanos("3.75")
    # One: the served request with no price. The refusal and the failure have no cost either,
    # because nothing was served, and counting them would make "spend is a lower bound" permanent
    # for any use case with a limit.
    assert mine["unpriced_requests"] == 1, "unpriced traffic must not be summed as free"
    # One: the upstream that failed. The refusal is a decision, counted under its outcome, and
    # counting it here as well reported one request as both refused and failed (`FRD-601` FR-6).
    assert mine["failed_requests"] == 1


async def test_the_window_excludes_what_falls_outside_it(
    engine: AsyncEngine, governance_token: str, window_slug: str
) -> None:
    slug = window_slug
    start = datetime(2031, 5, 1, tzinfo=UTC)
    end = start + timedelta(days=31)

    await _log(engine, use_case=slug, subject="a", when=start, cost=to_nanos("1.00"))
    await _log(engine, use_case=slug, subject="a", when=end, cost=to_nanos("99.00"))  # excluded
    await _log(
        engine,
        use_case=slug,
        subject="a",
        when=start - timedelta(seconds=1),
        cost=to_nanos("99.00"),
    )  # excluded

    report = await _report(governance_token, start, end)
    mine = next(row for row in report["by_use_case"] if row["key"] == slug)

    assert mine["requests"] == 1
    assert mine["cost"] == "1.00"


async def test_a_caller_without_oversight_does_not_see_another_use_case(
    engine: AsyncEngine, member_token: str, governance_token: str, window_slug: str
) -> None:
    """The security-relevant one, over the real token. The member client holds no
    organisation-wide role (`ADR-0017`) and nothing grants it this use case, which exists only as
    rows in a window nothing else writes into — so it must see an empty report while oversight sees
    the traffic.

    Both halves in one test on purpose: separately, the first would pass against an endpoint that
    is simply broken and returns nothing to anyone.
    """
    slug = window_slug
    start = datetime(2031, 7, 1, tzinfo=UTC)
    end = start + timedelta(days=31)
    await _log(engine, use_case=slug, subject="somebody", when=start, cost=to_nanos("5.00"))

    by_member = await _report(member_token, start, end)
    by_oversight = await _report(governance_token, start, end)

    assert by_member["scope"] == "use_cases"
    assert by_member["totals"]["requests"] == 0, "a non-member saw traffic it has no claim to"

    assert by_oversight["scope"] == "all"
    assert any(row["key"] == slug for row in by_oversight["by_use_case"])


async def test_the_reporting_window_is_indexed_in_the_real_schema(engine: AsyncEngine) -> None:
    """FR-7. A report is bounded by its window, and the window is only cheap if it is indexed —
    on a table retention deliberately keeps rows in, an unindexed scan is a promise that expires
    quietly as the installation grows.

    Asserted against `pg_indexes`, not `EXPLAIN`: on a test database of a few hundred rows the
    planner correctly prefers a sequential scan whatever the schema says, so an `EXPLAIN` here
    would be measuring how much traffic the stack happened to have, not what was built.
    """
    async with engine.connect() as connection:
        indexed = {
            row[0]
            for row in (
                await connection.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE tablename = 'request_logs'")
                )
            ).all()
        }

    assert any("created_at" in definition for definition in indexed), (
        f"the reporting window has no index to stand on: {indexed}"
    )
    assert any("use_case" in definition and "created_at" in definition for definition in indexed), (
        f"a scoped report still scans the window of every use case: {indexed}"
    )


async def test_the_endpoint_refuses_an_unauthenticated_caller() -> None:
    """Spend across an installation carries no payloads and is still not public."""
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=15.0) as client:
        assert (await client.get("/v1beta/reporting")).status_code == 401
