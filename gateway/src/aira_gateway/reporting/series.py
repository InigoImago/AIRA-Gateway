"""Time buckets for the usage series (`FRD-626`): what each day or hour of a period did.

The arithmetic lives apart from the queries because all of it is off-by-one territory — a bucket
label, the axis a chart draws, and the cap that stops a year of hours being asked for.

**The bucket expression is portable on purpose.** `FRD-601` §4.2 refused to make a reporting query
dialect-dependent, because the production expression would then be exercised only by the
integration suite while the hermetic tests ran the other one. `date_trunc` is Postgres-only and
`strftime` SQLite-only, so neither is available; what *both* dialects do identically is render a
timestamp as ``YYYY-MM-DD HH:MM:SS…``, and a prefix of that text names the bucket. Postgres renders
a ``timestamptz`` in the **session** time zone, which `db.base.build_engine` pins to UTC for
exactly this reason (`ADR-0028`).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import String, case, cast, func, literal
from sqlalchemy.sql.elements import ColumnElement

from aira_gateway.db.models import RequestLog

#: How wide a bucket is. Two, not a free-form interval: these are the periods budgets and the
#: period picker already use, and every extra one is another axis label format to get right.
Granularity = Literal["day", "hour"]

#: How many leading characters of the rendered timestamp name the bucket.
#: ``2026-10-01`` is ten, ``2026-10-01 14`` is thirteen.
_WIDTH: dict[str, int] = {"day": 10, "hour": 13}

#: What a window must be shorter than for hours to be offered: eight days of them.
#:
#: A limit on the **chart**, not on the query. 192 columns is dense and readable on a desktop; a
#: month of hours is 744, which at phone width is under half a pixel each — a wall of slivers that
#: answers no question and that nobody would choose on purpose. Past this the answer is a day
#: bucket, and it is refused by name rather than silently coarsened (`FRD-626` FR-5).
MAX_BUCKETS = 192

#: Beyond this many hours an unasked-for granularity is a day. Two days of hours is a shape
#: somebody can read; a week of them is 168 columns.
_AUTO_HOURS = 48

#: Which column the series is split into coloured bands by. Not free-form: each one is a column
#: with an index on it, and a split by something else is a different feature.
SPLITS: dict[str, Any] = {
    "model": RequestLog.model,
    "use_case": RequestLog.use_case,
    "outcome": RequestLog.outcome,
}

#: Where the tail goes once the chart is full (`FRD-626` FR-6). A generated ninth hue is
#: indistinguishable from an existing one, so the ninth series is not a hue — it is this row.
OTHER = "(other)"

#: A NULL group is a real group — a request with no use case, a row from before `outcome` existed —
#: and is labelled so that it stays in the total. The same label `ReportingService._grouped` uses.
NONE = "(none)"

#: How many bands get a colour of their own before the rest fold into :data:`OTHER`.
DEFAULT_TOP = 7


class TooManyBuckets(Exception):
    """The window asked for more buckets than a chart can show.

    Named rather than silently coarsened: a granularity quietly changed under the caller is a chart
    whose axis means something other than what was asked for.
    """


def resolve(start: datetime, end: datetime, asked: str) -> Granularity:
    """The granularity to use for ``[start, end)``.

    ``auto`` reads the window; a named one is honoured, or refused when it would not fit.
    """
    hours = (end - start) / timedelta(hours=1)
    if asked == "hour":
        if hours > MAX_BUCKETS:
            raise TooManyBuckets(
                f"An hourly series may span at most {MAX_BUCKETS} hours "
                f"({MAX_BUCKETS // 24} days); this window is {round(hours)} hours. Ask for 'day'."
            )
        return "hour"
    if asked == "day":
        return "day"
    return "hour" if hours <= _AUTO_HOURS else "day"


def step(granularity: Granularity) -> timedelta:
    """One bucket's width. Exact in UTC, which is the zone every figure here is in: a local day
    changes length twice a year and a chart whose columns are 23 hours wide is a chart that lies."""
    return timedelta(days=1) if granularity == "day" else timedelta(hours=1)


def label(moment: datetime, granularity: Granularity) -> str:
    """What bucket ``moment`` falls in, as the axis and the rows both spell it.

    ``T`` rather than the space the databases render, so a label is an ISO-8601 prefix a client can
    parse without knowing which store produced it.
    """
    if granularity == "day":
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%dT%H")


def normalise(rendered: str, granularity: Granularity) -> str:
    """A label as the database rendered it, in the form :func:`label` writes.

    One place, because the two halves have to agree exactly: a bucket spelled differently from the
    axis would be a column the chart cannot find a home for.
    """
    return rendered[: _WIDTH[granularity]].replace(" ", "T")


def axis(start: datetime, end: datetime, granularity: Granularity) -> list[str]:
    """Every bucket in ``[start, end)``, including the empty ones.

    **The empty ones are the point.** A histogram drawn only from buckets that have rows puts
    Monday beside Friday and calls it a week; a quiet day is a reading.
    """
    width = step(granularity)
    cursor = _floor(start, granularity)
    labels: list[str] = []
    while cursor < end:
        labels.append(label(cursor, granularity))
        cursor += width
    return labels


def _floor(moment: datetime, granularity: Granularity) -> datetime:
    """The start of the bucket ``moment`` falls in.

    A window beginning mid-bucket still gets that whole bucket's label on the axis, and the column
    holds only what the window contains — the figures are the window's, the label is the clock's.
    """
    if granularity == "day":
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return moment.replace(minute=0, second=0, microsecond=0)


def bucket(granularity: Granularity) -> ColumnElement[str]:
    """The SQL expression naming a row's bucket — identical on SQLite and on Postgres.

    See the module docstring for why it is a text prefix and not `date_trunc`.
    """
    return func.substr(cast(RequestLog.created_at, String), 1, _WIDTH[granularity])


def band(split: str, kept: list[str]) -> ColumnElement[str]:
    """The SQL expression naming a row's coloured band, with everything outside ``kept`` folded.

    Folded **in the database**: the alternative is reading every band's rows into the process to
    add them up here, which is the thing `FRD-601` §4.2 refused for the breakdowns and would be no
    better for a series with a bucket per band.
    """
    # `nullif` as well as `coalesce`, because the breakdown this folding is ranked against labels an
    # empty string `(none)` too (`_grouped` uses `row.key or NONE`). Without it a band the legend
    # calls `(none)` would have its rows folded into `(other)`, and the chart would contradict the
    # table beside it. Both functions are standard SQL and spelled the same in either store.
    named = func.coalesce(func.nullif(SPLITS[split], literal("")), literal(NONE))
    if not kept:
        # Nothing to keep means nothing to fold: an empty `IN ()` would fold every row of an empty
        # result into `(other)` and put a band on the legend that has no rows behind it.
        return named
    return case((named.in_(kept), named), else_=literal(OTHER))
