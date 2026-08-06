# FRD-601 — Spend and usage reporting

> Phase: 6 · Status: **In progress** · Owner: Vadim Scheibe · Last updated: 2026-08-06
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
  carries requests, tokens (prompt/completion split), spend, and median latency.
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

Median latency is the exception worth naming: it is a percentile, not a sum, so it is computed
with `percentile_cont` rather than averaged. An average latency is dominated by the slowest
request in the window and tells an operator almost nothing.

### 4.3 Unpriced is its own column, not a zero

A request whose model has no price contributes to `unpriced_requests` and to nothing else.
Reporting carries that number in every breakdown row, and the screen says so wherever a spend
figure is shown that has one behind it. This is the same rule as the budget bars, for the same
reason: a total that quietly omits traffic reads as complete.

## 5. Testing & Acceptance

- **Unit**: the scope rule in all three cases (governance, member, neither); breakdown arithmetic
  including the prompt/completion split; unpriced requests counted apart; a period boundary
  including and excluding the right rows; an empty period reported as zeroes rather than absent.
- **Integration (Postgres)**: the aggregates match a hand-computed figure over seeded traffic;
  the index is used rather than a sequential scan; a caller sees exactly the use cases they should.
- **Frontend**: the period selector; the breakdowns rendered; the unpriced caveat shown when and
  only when there is unpriced traffic; a failed load reported rather than an empty screen.
- **e2e**: a governance user sees a use case they are not a member of; a use-case user does not.
- **Mutation**: the visibility rule, the unpriced separation, and the money representation each
  get an entry — the first is the security-relevant one.

## 6. Consequences & follow-ups

- The gateway must be reachable for the report to show anything. It degrades the same way the
  consumption view does: the screen says the gateway could not be reached rather than showing
  zeroes, because zero spend and unknown spend are different statements.
- Reporting reads a table whose rows retention keeps by default. An installation that switches
  `AIRA_LOG_RETENTION_DAYS` on shortens its own reporting horizon; that trade is already
  documented in `FRD-404` and is worth repeating where the horizon becomes visible.
- **Follow-ups**: charts once the comparison people actually make is known; per-request browsing
  after `FRD-406`; export; threshold alerting.
