# ADR-0028 — A time bucket is portable, and the database session is UTC

- **Status:** Accepted
- **Date:** 2026-10-01
- **Deciders:** Vadim Scheibe

## Context

`FRD-626` draws usage over time: a column per day or hour, each split by model. That needs one thing
the reporting queries have never needed — a **bucket**, computed in SQL so the aggregation stays a
`GROUP BY` over a bounded window rather than a table pulled into the process (`FRD-601` §4.2).

Every obvious way to write one is dialect-specific. `date_trunc` is PostgreSQL, `strftime` is
SQLite, `CAST(x AS DATE)` means "parse the leading digits" on SQLite and is therefore worse than an
error. And `FRD-601` §4.2 already refused to make a reporting expression dialect-dependent, in as
many words, when a percentile was wanted:

> Making the query dialect-dependent would leave the production expression exercised only by the
> integration suite, which is precisely the shape of thing that breaks quietly.

That argument has not weakened. The hermetic suite runs on SQLite and is what a contributor runs;
the live suite runs on Postgres and is what nobody runs before pushing. A bucket written twice would
be a chart that is right in CI and wrong in production, and no test in the fast suite could tell.

There is one expression both stores spell identically: a timestamp rendered as text is
``YYYY-MM-DD HH:MM:SS…`` in both, so `substr(cast(created_at as text), 1, 10)` names a day and
`…, 1, 13` names an hour, in standard SQL, on either store.

It has one catch, and it is the whole of this decision. **Postgres renders a `timestamptz` in the
session's time zone.** The stored instants are UTC; what `CAST(… AS text)` produces depends on
whatever `TimeZone` the session happens to carry, which is the server's default unless somebody set
it. SQLAlchemy's psycopg driver does not set it. So on a Postgres started in `Europe/Berlin`, a
request made at 23:30 UTC renders as `01:30` the next day and lands in the next day's column —
while the same row on SQLite lands in this day's.

## Options considered

- **A bucket per dialect** (`date_trunc` on Postgres, `strftime` on SQLite) — each expression is the
  natural one for its store and neither needs a session setting. It is also exactly the shape
  `FRD-601` §4.2 declined: the production half would be covered only by the live suite, and the two
  can drift without any fast test noticing.
- **Bucket in Python** — read the window's rows and group them in the process. Portable by
  construction and correct. It also moves a month of rows across the wire to compute a few hundred
  numbers, and grows with traffic the installation cannot control. `FRD-601` §4.2 again.
- **A portable text prefix, with the session's zone left as it is** — one expression, and a chart
  whose buckets silently depend on how somebody started Postgres. Rejected: the failure is invisible
  (the columns look plausible) and only wrong near midnight.
- **A portable text prefix, with the session pinned to UTC** — one expression, exercised by both
  suites, and the zone it depends on stated in one place and asserted by a test.

## Decision

**The bucket is a text prefix of the rendered timestamp, and the gateway's Postgres sessions are
pinned to UTC.**

The pin is a libpq connection option — `options: -c timezone=UTC`, in
`aira_gateway.db.base.build_engine` — rather than a statement on a `connect` event, so it is in force
before the first statement on every connection the pool opens, including one it re-opens later.

This makes true at the storage layer something the surface has always claimed. `api.reporting.common`
reads a naive timestamp as UTC and says so in its own docstring — *"the zone every figure is in"* —
and that was a statement about the **values** and not about the **session**. It is now both.

## Consequences

- **Positive.** One bucket expression, exercised by `make test` on SQLite and by
  `make test-integration` on Postgres, with no branch between them. `tests/integration/
  test_usage_series.py` asserts `SHOW TIME ZONE` on the connection the application actually makes,
  and that a row at 23:30 lands in its own UTC day — the two things no hermetic test can see.
- **Positive.** "Every figure is UTC" stops being a convention a reader has to infer. A deployment
  may start its Postgres in any zone and the figures do not move.
- **Negative.** The bucket is a string operation on a timestamp, which an index cannot help with.
  It is bounded by the window — which *is* indexed (`FRD-601` FR-7) — so the grouping runs over
  what the window already narrowed, and a report is capped at `MAX_WINDOW_DAYS`.
- **Negative.** Buckets are **UTC days**, not local ones. A reader in Berlin sees a day that begins
  at 02:00 their time in summer. That is the same compromise the window parameters already make —
  the period picker sends local days and the surface reads them as UTC — and making the chart
  disagree with the window it was asked for would be worse than either. Per-installation display
  zones are a follow-up, and they belong to the whole surface rather than to this chart.
- **Negative.** The pin is specific to the Postgres branch of `build_engine`. A store this project
  does not configure gets no option it may not understand, and would also get no guarantee; nothing
  here configures one.
- **Follow-up.** If a percentile ever becomes worth a dialect-dependent expression (`FRD-601` §4.2's
  own open question), this ADR is the precedent for *not* doing it — and the text-prefix trick is
  the precedent for looking for the portable spelling first.
