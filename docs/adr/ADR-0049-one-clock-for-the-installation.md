# ADR-0049 — One clock for the installation, set where it is deployed

- **Status:** Accepted
- **Date:** 2026-10-28
- **Deciders:** Vadim Scheibe
- Takes up the follow-up `ADR-0028` named ("per-installation display zones … belong to the whole
  surface rather than to this chart"); the database session stays UTC.

## Context

Everything with a period counted in UTC: a budget's day began at 01:00 or 02:00 in Germany, a
report's day likewise, the consumption panels said "(UTC)", the OpenCode plugin said a budget resets
"in UTC" — while every timestamp the console printed was in the reader's browser zone, unlabelled.
A German installation saw a request at 00:30 counted against yesterday, next to a timestamp that
said today. And Django had no `TIME_ZONE`, so its own default — America/Chicago — gave half of
Management's API answers a −05:00 offset.

The owner asked for the time zone to be settable. Three readings were offered: one zone for
everything, set at deployment; the display only, periods staying UTC; or one zone for everything,
changeable in the console. The owner chose the first.

## Options considered

- **Display only.** A reader sees local times, and a budget still resets at 02:00 local — the
  mismatch the owner noticed, moved rather than removed.
- **One zone, changeable in the console.** Both planes would have to take a change at runtime, and a
  change moves the boundary of every period in progress; a deployment setting is the honest place
  for a decision of that weight, as `ADR-0036` placed strict data mode.
- **One zone for everything, a deployment setting** — the owner's choice.

## Decision

1. **`AIRA_TIME_ZONE`**, an IANA name read by both planes, **default `UTC`** — an installation that
   sets nothing counts exactly as before. Refused at start-up, by name, when the time-zone database
   does not know it, or when it is ever a part of an hour off UTC (the next point).
2. **Every period begins on that clock**: a budget's day and month (and the counters they key), the
   week and day a caller is shown, a report's default month, a bare date a window is given, a
   series' days and hours, and the KIRA usage window. A boundary is a **date** in the zone turned
   into an instant — never 24 hours added to the last one, since two days a year are not 24 hours
   long — and every boundary is carried as an instant in UTC, the only form in which subtracting
   two of them gives the time between them.
3. **The database session stays UTC** (`ADR-0028`). A series counts by the UTC hour in SQL — the
   portable text prefix — and folds those hours into the zone's days and hours in the process. That
   is exact only where every local midnight falls on a UTC hour, which is why a zone half an hour
   off is refused rather than counted a little wrong at every midnight.
4. **Every timestamp the console shows is on the installation's clock and says which**: the zone
   reaches it through `/v1/me`, the header names it once — beside each of hundreds of timestamps
   it would be noise — and every period and bucket is labelled with it.
5. **APIs keep instants unambiguous.** Management's API answers in UTC (`TIME_ZONE = "UTC"`); the
   gateway's period ends carry the zone's offset, so a client reading the date of a reset reads the
   installation's date.

## Consequences

- Positive: a German installation's budget day, report day, chart and console clock agree, and say
  which clock they are on.
- Negative / trade-offs: changing the setting moves the boundary of the period in progress once —
  a day or month that began on the old clock ends on the new one. Said where the setting is
  documented.
- Negative: zones a part of an hour off UTC cannot be the installation's clock.
- Negative: an hour of the day the clocks go back is two buckets with one wall-clock hour; their
  labels carry the offset to tell them apart.
- Follow-ups: a reader's own display zone, apart from the installation's, is not built.
