"""The usage series against real Postgres (`FRD-626`, `ADR-0028`).

**This is the suite that matters for this feature**, and it is the reason the hermetic one is not
enough. The bucket is a text prefix of the stored timestamp, which is the one expression both stores
spell identically — and Postgres renders a `timestamptz` **in the session's time zone**. If that
session is not UTC, a request made at 23:30 lands in the next day's column here and in this day's
column on the SQLite the hermetic tests run against: a chart that is right in CI and wrong in
production, with no test able to tell.

So two things are asserted here and nowhere else: the session **is** UTC, and a row near a day
boundary lands where UTC says it does.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from aira_common.money import to_nanos

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration


async def _log(
    engine: AsyncEngine,
    *,
    use_case: str,
    when: datetime,
    model: str = "mock-1",
    cost: int | None = None,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO request_logs (id, created_at, subject, auth_method, use_case, api,"
                " operation, model, status, prompt_tokens, completion_tokens, total_tokens,"
                " cost_nanos, latency_ms, outcome)"
                " VALUES (:id, :ts, 'alice', 'api_key', :uc, 'gemini', 'generateContent',"
                " :model, 200, 10, 20, 30, :cost, 40, 'served')"
            ),
            {
                "id": f"ser-{uuid.uuid4().hex[:12]}",
                "ts": when,
                "uc": use_case,
                "model": model,
                "cost": cost,
            },
        )


async def _series(token: str, start: datetime, end: datetime, **extra: str) -> dict:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get(
            "/v1beta/reporting",
            params={"from": start.isoformat(), "to": end.isoformat(), "series": "day", **extra},
            headers={"authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200, response.text
    return dict(response.json()["series"])


async def test_the_database_session_is_utc(engine: AsyncEngine) -> None:
    """`ADR-0028`. The bucket is read out of the rendered timestamp, so the zone the session renders
    in *is* the zone the chart buckets in — and `SHOW TIME ZONE` is the only place that is visible.

    Asserted on the connection the application makes, not on the server's `postgresql.conf`: a
    deployment is free to start Postgres in any zone, which is exactly the case this pins.
    """
    async with engine.connect() as connection:
        zone = (await connection.execute(text("SHOW TIME ZONE"))).scalar_one()

    assert str(zone).upper() in {"UTC", "ETC/UTC"}, (
        f"the session renders timestamps in '{zone}', so a request near midnight lands in a "
        "different column here than on the SQLite the hermetic tests run against"
    )


async def test_a_row_lands_in_the_utc_day_it_was_made_in(
    engine: AsyncEngine, governance_token: str
) -> None:
    """The boundary itself, from both sides, in one test.

    23:30 and 00:30 on either side of a midnight: separate columns, each labelled by the UTC day it
    belongs to. Rendered an hour out in either direction, the two collapse into one — which looks
    like an ordinary busy day rather than like a defect.
    """
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    # A window nothing else in the stack writes into, as the sibling suite does.
    start = datetime(2031, 5, 1, tzinfo=UTC)

    await _log(engine, use_case=slug, when=start + timedelta(hours=23, minutes=30))
    await _log(engine, use_case=slug, when=start + timedelta(days=1, minutes=30))

    series = await _series(governance_token, start, start + timedelta(days=3), use_case=slug)
    counted = {point["bucket"]: point["requests"] for point in series["points"]}

    assert counted == {"2031-05-01": 1, "2031-05-02": 1}


async def test_the_axis_is_the_window_and_not_only_the_busy_days(
    engine: AsyncEngine, governance_token: str
) -> None:
    """Including the quiet ones. Computed in the gateway's process and matched against labels the
    database produced — if the two ever spell a bucket differently, every point lands off-axis."""
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    start = datetime(2031, 5, 10, tzinfo=UTC)

    await _log(engine, use_case=slug, when=start + timedelta(days=2, hours=9))

    series = await _series(governance_token, start, start + timedelta(days=5), use_case=slug)

    assert series["buckets"] == [
        "2031-05-10",
        "2031-05-11",
        "2031-05-12",
        "2031-05-13",
        "2031-05-14",
    ]
    assert [point["bucket"] for point in series["points"]] == ["2031-05-12"]


async def test_the_bands_add_up_to_the_totals_over_real_postgres(
    engine: AsyncEngine, governance_token: str
) -> None:
    """The arithmetic, on the store that actually serves it.

    `sum` over NULLs, integer money and a `GROUP BY` with a `coalesce` in it are each places SQLite
    answers differently — and the property a reader cares about is that the chart adds up to the
    figure printed above it.
    """
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    start = datetime(2031, 6, 1, tzinfo=UTC)

    await _log(engine, use_case=slug, when=start, model="chat-1", cost=to_nanos("1.50"))
    await _log(
        engine,
        use_case=slug,
        when=start + timedelta(days=1),
        model="embed-1",
        cost=to_nanos("0.25"),
    )
    await _log(engine, use_case=slug, when=start, model="chat-1", cost=None)  # unpriced

    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=30.0) as client:
        response = await client.get(
            "/v1beta/reporting",
            params={
                "from": start.isoformat(),
                "to": (start + timedelta(days=30)).isoformat(),
                "series": "day",
                "split": "model",
                "use_case": slug,
            },
            headers={"authorization": f"Bearer {governance_token}"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    points = body["series"]["points"]

    assert sum(point["cost_nanos"] for point in points) == body["totals"]["cost_nanos"]
    assert sum(point["requests"] for point in points) == body["totals"]["requests"] == 3
    assert sum(point["unpriced_requests"] for point in points) == 1
    assert body["series"]["keys"] == ["chat-1", "embed-1"]


async def test_a_person_filter_finds_one_person_across_both_their_credentials(
    engine: AsyncEngine, governance_token: str
) -> None:
    """`FRD-626` FR-15, on the store that serves it.

    A person's own card asks for `person=<name>`, and a person has two ways to call: a token whose
    subject is a directory id, and a key whose subject is their username. `coalesce` is spelled the
    same in both stores, but the rows that exercise it are the ones a real stack writes — so the
    pairing is asserted here as well as hermetically.
    """
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    start = datetime(2031, 8, 1, tzinfo=UTC)

    async with engine.begin() as connection:
        for subject, username, cost in (
            ("kc-uuid-1", "erika", to_nanos("1.00")),
            ("erika", "erika", to_nanos("0.50")),
            ("hans", "hans", to_nanos("9.00")),
        ):
            await connection.execute(
                text(
                    "INSERT INTO request_logs (id, created_at, subject, username, auth_method,"
                    " use_case, api, operation, model, status, prompt_tokens, completion_tokens,"
                    " total_tokens, cost_nanos, latency_ms, outcome)"
                    " VALUES (:id, :ts, :subject, :username, 'api_key', :uc, 'gemini',"
                    " 'generateContent', 'mock-1', 200, 10, 20, 30, :cost, 40, 'served')"
                ),
                {
                    "id": f"ser-{uuid.uuid4().hex[:12]}",
                    "ts": start,
                    "subject": subject,
                    "username": username,
                    "uc": slug,
                    "cost": cost,
                },
            )

    series = await _series(
        governance_token, start, start + timedelta(days=5), use_case=slug, person="erika"
    )

    assert sum(point["cost_nanos"] for point in series["points"]) == to_nanos("1.50")


async def test_a_series_outside_the_callers_scope_is_empty_rather_than_somebody_elses(
    engine: AsyncEngine, member_token: str
) -> None:
    """The one security-relevant line, asserted over HTTP with a token the realm issued.

    The hermetic suite builds a `Principal` directly, which cannot show that the role survives the
    round trip — and the chart is a new `select`, so it is a new chance to build the window by hand.
    """
    slug = f"itest-{uuid.uuid4().hex[:8]}"
    start = datetime(2031, 7, 1, tzinfo=UTC)
    await _log(engine, use_case=slug, when=start, cost=to_nanos("9.00"))

    series = await _series(
        member_token, start, start + timedelta(days=30), use_case=slug, split="use_case"
    )

    assert series["points"] == []
    assert series["buckets"], "an empty report still describes the window it was asked about"
