"""Give the usage chart a history to draw (`FRD-626`, `FRD-130` §4f).

`demo_traffic.py` drives eleven real requests and they all arrive **now**, which is the right thing
for a consumption bar and useless for a histogram: one column, and no answer to the question the
chart exists for — *is this growing, and which model is responsible*.

So this drives more real traffic through the gateway and then **moves the clock on the rows it just
created**, spreading them over the past few weeks in a working-week shape.

**What is real and what is not, stated plainly**, because a demonstration that blurs this is worse
than one with an empty screen:

- *Real*: every request. It went through authentication, the pre-dispatch sequence, the pipeline, a
  model that actually answered, the pricing in the catalogue and the audit trail — exactly as
  production traffic does. Every token count, latency and figure of money is the gateway's own.
- *Not real*: **when** it happened, and how much of it happened on which day. The timestamps are
  moved and the daily volume follows a shape this file chooses.

That is a smaller lie than inserting rows (`FRD-130` §4: *real traffic, not inserted rows*) and a
bigger one than nothing, so it is confined to the demo: it only ever updates rows belonging to the
demo's own use cases that **this run** created, and it says what it did.

Each simulated day gets its own budget period, so the counters are cleared between days — otherwise
the showcase's deliberately tight budgets (`FRD-130` FR-3, sized so a handful of requests moves a
bar) refuse everything after the first day and the history is a wall of 429s.

    make showcase-history            # on its own, against a running showcase
    uv run python tools/demo_history.py --days 28 --per-day 4
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import UTC, datetime, timedelta

import demo_reset_usage
import demo_traffic
import httpx

#: How far back the history reaches, and how much traffic a working day carries. Defaults sized for
#: `make showcase`: a month of columns, and a run that finishes while somebody is still watching.
DEFAULT_DAYS = int(os.environ.get("AIRA_DEMO_HISTORY_DAYS", "28"))
DEFAULT_PER_DAY = int(os.environ.get("AIRA_DEMO_HISTORY_PER_DAY", "3"))

#: The window a working day's traffic is spread over, in UTC hours. Office hours rather than
#: midnight-to-midnight, so an hourly view of one day has a shape too.
WORKDAY = (7, 18)

#: Fixed, so two runs of the showcase produce the same shape. A demo whose chart looks different
#: every time is a demo somebody has to re-learn before every walkthrough.
SEED = 20260626

#: Which use cases the history is made of, and how much of it each carries. `coding-assistant` is
#: left out: an agent's traffic is one instruction becoming many calls, and faking that ratio here
#: would put a shape on screen that the use case's own limits were not sized for.
WEIGHTS: dict[str, int] = {"kundenservice": 3, "entwicklung": 2, "personalwesen": 1}

#: Every use case this script may touch. Named rather than "the demo", because the clock is being
#: moved and a mistake here edits an audit trail.
DEMO_SLUGS = tuple(WEIGHTS)

#: How often the injection attempt is repeated, in days. The refusal is what makes the *outcome*
#: split worth looking at — a chart whose every band is `served` demonstrates no control at all.
INJECTION_EVERY = 5

#: How often the embedding batch runs. Less than daily: the demonstration is that a cheap model and
#: an expensive one are both governed and priced, and a second band is enough to show it.
EMBEDDING_EVERY = 3

#: Slack between this process's clock and the database's, which stamps `created_at` itself. Without
#: it a few seconds of skew in either direction would leave a day's rows unmatched and unmoved —
#: reported rather than silent, but still a hole in the chart for no reason.
_CLOCK_MARGIN = timedelta(seconds=5)

POSTGRES = os.environ.get(
    "AIRA_DEMO_RESET_DSN", "postgresql+psycopg://aira:aira-local@localhost:5432/aira_gateway"
)


def _plan(days: int, per_day: int) -> list[tuple[int, int]]:
    """How many requests each day back from today carries: ``(days_ago, requests)``.

    Weekdays vary around ``per_day`` and weekends are quiet, because a flat histogram says nothing
    about the thing a histogram is for. One day is deliberately the busiest, so a walkthrough has
    something to point at.
    """
    dice = random.Random(SEED)  # noqa: S311 - a demo's shape, not a secret
    today = datetime.now(UTC)
    plan: list[tuple[int, int]] = []
    for days_ago in range(days, 0, -1):
        weekday = (today - timedelta(days=days_ago)).weekday()
        if weekday >= 5:
            # 0 or 1, independently — so some weekends are a gap and some are a thin column, and a
            # walkthrough can see both. The empty ones are not a shortcoming of the data: a bucket
            # with nothing in it is a column of zero height, which is the property `FRD-626` FR-3
            # exists for, and the demo is the one place a reader meets it.
            plan.append((days_ago, dice.choice([0, 1])))
        else:
            plan.append((days_ago, max(1, round(per_day * dice.uniform(0.6, 1.4)))))

    if plan:
        peak = max(range(len(plan)), key=lambda index: plan[index][1])
        plan[peak] = (plan[peak][0], plan[peak][1] * 3)
    return plan


def _slugs_for(count: int, dice: random.Random) -> list[str]:
    """Which use case sends each of a day's requests, in the declared proportions."""
    weighted = [slug for slug, weight in WEIGHTS.items() for _ in range(weight)]
    return [dice.choice(weighted) for _ in range(count)]


def _spread(days_ago: int, count: int, dice: random.Random) -> list[datetime]:
    """``count`` instants inside one working day, in order.

    Ordered, because the rows are matched to them in the order they were created — the pairing is
    arbitrary either way, and an ordered one keeps a day's latencies from reading as time travel.
    """
    midnight = (datetime.now(UTC) - timedelta(days=days_ago)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start, end = WORKDAY
    seconds = sorted(dice.randrange(start * 3600, end * 3600) for _ in range(count))
    return [midnight + timedelta(seconds=offset) for offset in seconds]


async def _move(rows: list[tuple[str, datetime]]) -> int:
    """Set each row's ``created_at``. The one write this script makes, and the whole of the lie."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(POSTGRES)
    try:
        async with engine.begin() as connection:
            for row_id, when in rows:
                await connection.execute(
                    text("UPDATE request_logs SET created_at = :when WHERE id = :id"),
                    {"when": when, "id": row_id},
                )
        return len(rows)
    finally:
        await engine.dispose()


async def _created_since(marker: datetime) -> list[str]:
    """The ids of demo rows written since ``marker``, oldest first.

    Bounded three ways, because this is the query whose answer gets its clock changed: by time, by
    the demo's own use cases, and by `created_at >= marker` rather than by any notion of "recent".
    A row somebody else's traffic wrote cannot be in it.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(POSTGRES)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT id FROM request_logs "
                    "WHERE created_at >= :marker AND use_case = ANY(:slugs) "
                    "ORDER BY created_at, id"
                ),
                {"marker": marker, "slugs": list(DEMO_SLUGS)},
            )
            return [row[0] for row in result]
    finally:
        await engine.dispose()


async def _drive(
    client: httpx.AsyncClient, days_ago: int, count: int, dice: random.Random
) -> tuple[int, int]:
    """One day's traffic, sequentially. Returns ``(requests sent, of which served)``.

    Sequential although it is slower: the showcase sets rate limits as well as budgets, and a burst
    would be refused by the limiter — which is a control worth demonstrating and not worth building
    a whole month out of.
    """
    codes: list[int] = []
    for index, slug in enumerate(_slugs_for(count, dice)):
        # Round-robin over the people who may call this use case, the same way `demo_traffic` does,
        # so the history has more than one person in it wherever the seed made more than one member.
        callers = demo_traffic.CALLERS[slug]
        codes.append(
            await demo_traffic.ask(
                client,
                slug,
                dice.choice(demo_traffic.CONVERSATIONS[slug]),
                callers[index % len(callers)],
            )
        )

    if days_ago % EMBEDDING_EVERY == 0:
        codes.append(await demo_traffic.embed(client, demo_traffic.EMBEDDING_USE_CASE))
    if days_ago % INJECTION_EVERY == 0:
        # Expected to be refused by the pipeline, which is the point: a chart whose every band is
        # `served` shows no control at all.
        codes.append(
            await demo_traffic.ask(client, demo_traffic.INJECTION_USE_CASE, demo_traffic.INJECTION)
        )
    return len(codes), sum(code == 200 for code in codes)


def _extras(days_ago: int) -> int:
    """The two deliberate requests a day may carry on top of its conversations.

    Counted here as well as sent in `_drive`, because the summary said *"79 of 72 served"* on its
    first real run: the plan counted conversations and the tally counted everything. A total smaller
    than the number it is a total of is the kind of line a reader stops trusting the rest of.
    """
    return int(days_ago % EMBEDDING_EVERY == 0) + int(days_ago % INJECTION_EVERY == 0)


async def main(days: int, per_day: int) -> int:
    plan = _plan(days, per_day)
    total = sum(count + _extras(days_ago) for days_ago, count in plan if count)
    # `flush` on every progress line. This runs for minutes and Python block-buffers stdout when it
    # is a pipe or a file — which `make showcase` makes it. Without this the target shows nothing
    # between "building a month of history" and the summary, and a quiet several minutes is
    # indistinguishable from a hang.
    print(
        f"==> building {days} days of history: ~{total} real requests through the gateway, "
        "then their timestamps moved into the past",
        flush=True,
    )

    dice = random.Random(SEED)  # noqa: S311 - a demo's shape, not a secret
    moved = failed = served = 0
    async with httpx.AsyncClient(timeout=300.0) as client:
        for days_ago, count in plan:
            if not count:
                continue
            # Each backdated day is its own budget period. Without this the showcase's own limits —
            # deliberately tight, so a bar moves within a demonstration — refuse everything after
            # the first day, and the history becomes a wall of 429s.
            try:
                await demo_reset_usage.reset()
            except Exception as error:  # noqa: BLE001 - a demo helper reports and does not raise
                print(f"  could not clear the counters: {error}", file=sys.stderr)

            marker = datetime.now(UTC) - _CLOCK_MARGIN
            sent, day_served = await _drive(client, days_ago, count, dice)
            served += day_served
            rows = await _created_since(marker)
            if len(rows) != sent:
                # Said rather than assumed. The count can differ only if something else wrote to a
                # demo use case while this ran, and silently pairing the wrong rows with the wrong
                # instants would move a timestamp that is not ours to move.
                print(
                    f"  day -{days_ago}: sent {sent} request(s) and found {len(rows)} row(s); "
                    "leaving this day where it is",
                    file=sys.stderr,
                )
                failed += 1
                continue

            moved += await _move(list(zip(rows, _spread(days_ago, len(rows), dice), strict=True)))
            print(f"  day -{days_ago:<3} {sent} request(s), {day_served} served", flush=True)

    print(
        f"\nmoved {moved} row(s) into the past; {served} of {total} request(s) were served; "
        f"{failed} day(s) left where they were",
        flush=True,
    )
    if not served:
        # Backdated refusals are a history of a gateway that answered nothing. The figures would be
        # real and the chart would be flat, which is the one outcome worse than an empty screen.
        print(
            "\nnothing was served, so every column will be a refusal.\n"
            "  make showcase-doctor              (checks the chain link by link)",
            file=sys.stderr,
        )
        return 1
    if not moved:
        # The same rule the traffic script learned (`FRD-130` §4c): a demo helper that reports
        # success over an empty screen is worse than one that fails.
        print(
            "nothing was backdated, so the usage chart will show one column.\n"
            "  make showcase-doctor              (checks the chain link by link)",
            file=sys.stderr,
        )
        return 1
    print(
        "The figures are the gateway's own — tokens, latency and price all came from real\n"
        "requests. Only the clock was moved, and only on rows this run created."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--per-day", type=int, default=DEFAULT_PER_DAY)
    options = parser.parse_args()
    raise SystemExit(asyncio.run(main(options.days, options.per_day)))
