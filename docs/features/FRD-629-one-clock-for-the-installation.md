# FRD-629 — One clock for the installation

> Phase: 6 (Governance & Analytics) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner, in daily use: the time zones should be settable. Asked how far: one zone for
> everything — budgets, reports, the console, the plugin — set at deployment.
> Decision: `ADR-0049`. Related: `ADR-0028` (the bucket, and the session that stays UTC),
> `FRD-400` (budget periods), `FRD-601`/`FRD-626` (reports and the series), `FRD-603` (consumption),
> `FRD-139`/`FRD-144` (a caller's allowance, in OpenCode), `FRD-146` (KIRA usage).

## 1. Summary

`AIRA_TIME_ZONE` names the installation's clock — `Europe/Berlin`, say — and every period begins on
it: a budget's day and month, a report's days, the series' days and hours, the week and day a caller
is shown, and every timestamp the console prints. Unset, the clock is UTC, as before.

## 2. Goals & Non-Goals

**Goals**
- One setting, both planes, default UTC.
- Budgets, reports, charts, the console and the OpenCode plugin agree on where a day begins, and say
  which clock it is.
- Correct on the days the clocks change.

**Non-Goals**
- A reader's own display zone, apart from the installation's.
- Changing the zone at runtime in the console (`ADR-0049` §Options).
- Zones a part of an hour off UTC (see FR-1).

## 3. User Stories

- As a **developer in Germany**, I want my daily budget to reset at my midnight, so that 00:30 is
  today and not yesterday.
- As **IT Steuerung**, I want a report's days and the console's timestamps on one clock, so that a
  figure and the requests behind it can be put side by side.

## 4. Functional Requirements

- **FR-1 The setting.** `AIRA_TIME_ZONE`, an IANA name, read by both planes, default `UTC`. Start-up
  refuses, by name, a name the time-zone database does not know, and a zone whose offset from UTC is
  ever a part of an hour (2000–2050), because the series folds UTC hours into the zone's days.
- **FR-2 Budget periods.** A budget's day and month begin at the zone's midnight; the counter key is
  the zone's date; a refusal's reset time and a caller's `period_ends` are that midnight, written
  with the zone's offset.
- **FR-3 A caller's own figures.** `/v1beta/me/budget`'s today, this week (from Monday) and this month
  are the zone's, and the answer names the zone (`time_zone`).
- **FR-4 Reports.** A window's bare date, or a time without an offset, is read on the zone's clock;
  the default window is the zone's current month. The answer's `from` and `to`, the register's, and
  an export's file name and header are written on it.
- **FR-5 The series.** Buckets are the zone's days and hours. The database still counts by the UTC
  hour (`ADR-0028`); the process folds hours into the zone's buckets. An hour label carries the
  zone's offset where the zone is not UTC, so the hour the clocks go back is two columns, not one;
  a day label is the zone's date. The answer names the zone.
- **FR-6 KIRA usage.** `startDatum`/`endDatum` without an offset are read on the zone's clock.
- **FR-7 The console.** `/v1/me` names the zone; every timestamp is printed on it, the header names
  it once beside the reader's name, and every period and bucket is labelled with it. A named period
  — today, this month, the last 7 days — is the zone's, asked for once `/v1/me` has answered. A
  calendar date (a notice's edition) is shown as the day it names.
- **FR-8 The plugin.** OpenCode's budget plugin reads a reset's date as the gateway wrote it and says
  which clock the figures are on.
- **FR-9 Management's API.** `TIME_ZONE = "UTC"`: every timestamp it answers carries `+00:00` — not
  Django's default of America/Chicago, which it carried before.
- **FR-10 Management's own periods.** The privacy notice's monthly reminder is due on the zone's
  first of the month; a pipeline-test export is dated, and its moments written, on the zone.

## 5. Design

`aira_common.clock` holds the arithmetic both planes share: `zone_named` (validated, cached),
and `day_starts`/`day_ends`, `week_*`, `month_*`, each working from the zone's **date** and
returning an instant in UTC — the one form in which subtracting two boundaries gives the time
between them.

## 6. Data Model

None. A changed zone moves the boundary of the period in progress once: a counter keyed by the old
clock's date is read until the new clock's date differs from it.

## 7. API / Interface Contract

`docs/API.md`: `/v1/me`'s `time_zone`, `/v1beta/me/budget`'s `time_zone`, the series' `time_zone`,
and `CONFIGURATION.md`'s `AIRA_TIME_ZONE`.

## 8. Security & Privacy

No processing changes; the notice is unaffected.

## 9. Observability

None new.

## 10. Testing & Acceptance Criteria

- `libs/tests/test_clock.py` — Berlin across both clock changes, a week from Monday, a month across
  the year, refusals.
- Gateway: budget periods and resets, the caller's windows, report windows, the series and its labels
  around a clock change, KIRA usage — in Europe/Berlin.
- Management: `/v1/me`'s zone, the serializer's UTC, the notice's month and the test export in Berlin.
- Console: the zone from `/me`, timestamps (`ZonedPipe`, `inZone`), periods, chart labels and the
  header on it; the OpenCode plugin's reset date and footer.
- Mutations `TZ-*`.

**Acceptance**

- *Given* `AIRA_TIME_ZONE=Europe/Berlin`, *when* a request is made at 22:30 UTC on 14 July, *then* it
  counts against the day budget of 15 July, and the day resets at 22:00 UTC on 15 July.

## 11. Dependencies & Risks

- The time-zone database ships with Python; a rule a government changes after a release is read
  from the release's copy until the next.

## 12. Rollout / Demo

Images and one optional setting, on both planes. Unset changes nothing.
