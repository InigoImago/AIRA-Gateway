# FRD-601 — Spend and usage reporting

> Phase: 6 · Status: **Done** · Owner: Vadim Scheibe
> Builds on `FRD-103` (request logs), `FRD-403` (cost per request), `FRD-404` (retention).
> The visibility boundary is decided in `ADR-0009`.

## 1. Problem

Every dispatched request has been recorded since `FRD-103`, and since `FRD-403` each one carries
what it cost. Nothing shows any of it.

The only figures visible anywhere are the consumption bars beside a budget: the current period,
one use case, three numbers. So the questions the product exists to answer cannot be answered from
it — what did last month cost, which use case or model is responsible, is anything growing, is
anyone being throttled. An installation collects the data and reads it, if at all, with `psql`.

That is most acute for the **IT Steuerung** role, which the PRD defines around exactly this kind
of oversight and which today has a read-only list of use cases and nothing else.

## 2. Goals & Non-Goals

**Goals**
- Spend, requests, tokens and latency over a chosen period, broken down by use case, by model and
  by member.
- **Governance sees everything; everyone else sees their own use cases** — the same rule
  Management already applies, from one shared definition of what governance means (ADR-0009).
- Unpriced traffic stays visible as unpriced. A spend figure that silently omits requests is
  worse than one that admits what it cannot account for (`FRD-403` §4.4).
- Answers that stay correct as the table grows: the reporting reads a table retention keeps rows
  in indefinitely.

**Non-Goals**
- **Browsing individual requests and their prompts.** Aggregates need no payloads; a request
  viewer would show stored prompts to people who are deliberately *not* members of the use case
  that produced them. That is what content redaction (`FRD-406`, deferred) exists to make safe,
  and this view waits for it — see ADR-0009's follow-up.
- Charts. The first cut is figures and bars; a chart is worth adding once someone has said which
  comparison they actually make.
- Export, scheduled reports, alerting. Budget threshold alerting is its own backlog item.
- Retention of the reporting itself: it reads `request_logs`, whose horizon is
  `AIRA_LOG_RETENTION_DAYS` (off by default).

## 3. Functional Requirements

- **FR-1 Period**: a caller asks for a window (default: the current calendar month). Day and
  month granularity, because those are the periods budgets already use.
- **FR-2 Breakdowns**: totals plus a breakdown by use case, by model, and by member. Each row
  carries requests, tokens (prompt/completion split), spend, and latency (average and maximum —
  see §4.2 for why not a percentile).
- **FR-3 Visibility**: a caller holding a governance role sees every use case; anyone else sees
  only the use cases they are a member of. A caller with no memberships and no governance role
  gets an empty report, not an error — having nothing to see is not a failure.
- **FR-4 Unpriced traffic**: requests on a model with no price are counted and reported
  **separately**, never summed into spend as zero.
- **FR-5 Money**: spend crosses the API as an exact decimal string and as integer nano-units,
  the same pair `FRD-403` established — the string is what a human reads, the integer is what a
  bar can divide without a float touching a monetary figure.
- **FR-6 Failed requests count**: a request that failed still consumed a rate limit, possibly an
  upstream call, and is part of what happened. Status is reported alongside, not filtered out.
- **FR-7 Bounded work**: the query is bounded by the period and by an index; a report over a year
  of traffic must not read the table sequentially.

## 4. Design

### 4.1 Served by the gateway, authorized by the token

`request_logs` lives in the gateway's database, so the gateway serves the report — the same shape
as the consumption endpoint, and the reason ADR-0009 gives the gateway a notion of roles rather
than copying the figures into Management.

The scope is resolved once, at the edge:

```
governance role  → every use case
otherwise        → the caller's Keycloak group memberships
neither          → nothing, reported as an empty period rather than a refusal
```

An empty report and a refusal say different things, and only one of them is true for a use-case
user who happens to have no traffic yet.

### 4.2 Aggregated in the database, not in Python

The breakdowns are `GROUP BY` over a bounded window. Pulling rows into the process to sum them
would move the whole period across the wire to compute four numbers from it, and would grow
linearly with traffic the installation cannot control.

Latency is the exception worth naming, and it is a compromise. A percentile is the figure an
operator actually wants — an average is pulled around by a single slow request — but
`percentile_cont` is Postgres-specific, and the hermetic tests run on SQLite. Making the query
dialect-dependent would leave the production expression exercised only by the integration suite,
which is precisely the shape of thing that breaks quietly.

So the first cut reports **average and maximum**, both portable, and says so rather than calling
either one a percentile. Neither is a good tail indicator on its own; together they at least show
whether the spread is wide. A real percentile is a follow-up, and it needs the query to stop being
portable — a decision worth making deliberately rather than in passing.

### 4.3 Unpriced is its own column, not a zero

A request whose model has no price contributes to `unpriced_requests` and to nothing else.
Reporting carries that number in every breakdown row, and the screen says so wherever a spend
figure is shown that has one behind it. This is the same rule as the budget bars, for the same
reason: a total that quietly omits traffic reads as complete.

### 4.4 The screen: a parent and one panel used three times

The three breakdowns carry identical columns and differ only in what their first column is
called, so they are **one** `app-breakdown-table` rendered three times rather than three copies
of a five-column table. Three copies is how the model table ends up with a column the member
table quietly lost. The page owns the period, the load and the single `PageFeedback` banner.

The period is chosen from presets (this month, last month, last 7/30 days, custom). Days are
formatted in **local** time, not via `toISOString()`: converting to UTC first moves "today" by a
day for part of the day, and the resulting off-by-one in the reporting period is only ever
noticed in the evening. The `to` bound is exclusive throughout, and the custom form says which
day that makes the last one included.

## 5. Testing & Acceptance

Delivered (`gateway/tests/test_reporting.py`, `tests/integration/test_reporting.py`, the two
frontend specs under `features/reporting/`, `e2e/tests/reporting.spec.ts`):

- **Unit** — 23 gateway tests: the scope rule in all three cases (governance, member, neither);
  the token direction split; unpriced counted apart; failed requests reported; latency average
  and maximum; money as both string and integer; the half-open window; an empty period as
  zeroes; the breakdowns and their ordering; the endpoint's window validation.
- **Frontend** — 21 tests across the page and the panel: the presets and the local-day
  arithmetic, the breakdowns rendered, the share bar scaled against the largest row, the
  unpriced caveat shown when and only when there is unpriced traffic, a failed load reported
  rather than an empty screen, and a window that ends before it starts refused before it is sent.
- **Integration (Postgres)** — 5 tests: the aggregates match a hand-computed figure over seeded
  traffic; the window excludes what falls outside it; a real `use-case-admin` token sees nothing
  of another use case while a real governance token sees it; the window is indexed in the real
  schema; an unauthenticated caller is refused. The index is asserted against `pg_indexes`, not
  `EXPLAIN` — on a small test database the planner correctly prefers a sequential scan whatever
  the schema says, so an `EXPLAIN` assertion would be measuring the fixture, not the design.
- **e2e** — 4 tests: governance sees a use case it is not a member of and a use-case user does
  not (the same screen, the same period, both halves in one test); the report opens loaded on the
  current month; an impossible window is refused; the five-column tables do not drag a
  390px-wide page sideways.
- **Mutation** — six entries (`N1`–`N6` in `tools/mutation_check.py`), each verified to be
  caught: the two halves of the visibility rule, the unpriced separation, the half-open window,
  the failed-request count and the window bound. Five frontend properties and the e2e visibility
  rule were each broken by hand and confirmed to go red.

## 6. Consequences & follow-ups

- The gateway must be reachable for the report to show anything. It degrades the same way the
  consumption view does: the screen says the gateway could not be reached rather than showing
  zeroes, because zero spend and unknown spend are different statements.
- Reporting reads a table whose rows retention keeps by default. An installation that switches
  `AIRA_LOG_RETENTION_DAYS` on shortens its own reporting horizon; that trade is already
  documented in `FRD-404` and is worth repeating where the horizon becomes visible.
- **Follow-ups**: charts once the comparison people actually make is known; per-request browsing
  after `FRD-406`; export; threshold alerting.


## A classifier and the request count: both answers, in order

`FRD-125` FR-9 booked a pipeline step's model call with `requests=0`, and said why in the
requirement itself: *"the caller made **one** request and counting the classifier as a second would
inflate every request figure."* The budgets honoured it. This report counted rows.

So the two disagreed, and the report was the one out of step: a use case running an LLM injection
filter and a router reported two to three times the traffic it received. On the stack this was
found on, one use case showed **6 requests where 3 were made**, and another **1 where the caller
made none at all** — that row was a dry run's classifier call. The report was narrowed to the
caller's own requests, and both sides agreed again.

**Then the owner decided the other way** (2026-08-15): those calls reach a model and cost money, so
they count. A use case running two steps per request is doing three times the work its request
budget was sized for, and a budget that cannot see that is sized against something that is not
happening. `FRD-125` FR-9 is superseded by FR-9b; `book_side_call` books one request; and this
report counts every row again.

What did **not** change is the rate limit. A bucket measures how fast requests *arrive*, the gate
is taken once before the pipeline on the one request that arrived, and refusing a caller for calls
the gateway made on their behalf would throttle precisely the traffic they did send. Both halves
are pinned by tests, in both directions, because the two rules now differ deliberately and a
reader who finds only one of them would reasonably "fix" the other.

The `pipeline:<step>` operation still names those rows, so `by_model` and the operation give a
reader the split. What is not on offer is a total that quietly means something different from the
budget bar beside it — which is the property both reversals were about, and the only one that held
throughout.
