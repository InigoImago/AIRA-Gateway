# FRD-603 — What a use case consumed, with or without a budget

> Phase: 6 (Governance & Analytics) · Status: **Done** · Owner: Vadim Scheibe
>
> Origin: the owner, looking at the smoke-test use case (`FRD-504`) and finding neither a token
> count nor a figure of money on its page.
> Related: `FRD-402` (budget UI), `FRD-403` (cost), `FRD-601` (reporting), `FRD-602` (export),
> `FRD-122` (audit).

## 1. Problem

The question as it was asked: *"wird dann, wenn kein Budget gesetzt wurde, nichts kalkuliert?"*

Nearly, and the near-miss is the interesting part. **Everything is calculated.** Measured on the
running stack on 2026-08-09, before any change:

| | `request_logs` | `budget_usage` | `GET /v1beta/usage/smoke-test` | Budgets tab |
|---|---|---|---|---|
| `smoke-test` | 59 requests, 10,664 tokens, 3,674,900 nanos | **no row** | `[]` | "No budgets yet" |

Every request had been recorded since `FRD-103` and priced since `FRD-403`. What did not exist was
a **reader**. Two places conspired:

- `BudgetService.usage()` iterates the use case's **budget rows** and reports consumption per
  budget. With no budget there is nothing to iterate, so the endpoint answers `[]` — correctly, for
  what it is: an enforcement counter, not a ledger.
- `budgets-tab.html` rendered every figure **inside a budget card**, as a fraction of a limit. No
  limit, no denominator, no number — not even the numerator, which existed.

So a use case deliberately left unlimited — which the smoke-test use case is, and which any
low-volume internal use case will be — showed a page on which nothing appeared to be counted. That
reads as "this system does not track what I spend", which is the opposite of what it does.

**The general shape**, recorded because this project has now met it several times: two correct
halves and nothing carrying the fact from one to the other. Here the halves were even in different
services.

## 2. Goals & Non-Goals

**Goals**
- A use case's own page states what it consumed **whether or not anybody set a limit**.
- Per month, because that is the period a spend question is asked in — and per day beside it,
  because the month figure cannot say whether something is running away right now.
- The figure comes from the **request log**, the same record reporting and the audit trail are
  built from, so three screens cannot disagree about one month.
- One visibility rule, in the place it already lives.

**Non-Goals**
- A second aggregation path. The arithmetic exists (`ReportingService`); this is a reader for it.
- Turning `budget_usage` into a ledger. It is a counter that resets with a period and exists per
  limit; making it also the record of consumption would give one number two owners.
- Per-request browsing on this tab. That is `FRD-505`, has its own access rules and its own access
  record, and is reached from the Requests view.
- Charts or a history of previous months. The reporting screen has the period picker; this panel
  answers "now" and links there.

## 3. User Stories
- As a **use-case administrator**, I want to see what my use case has cost this month without first
  inventing a budget for it.
- As **IT Steuerung**, I want the figure on the use case's own page to be the same figure the
  reporting screen shows, because being asked to reconcile two of them is how a report stops being
  believed.

## 4. Functional Requirements

- **FR-1 One endpoint.** `GET /v1beta/reporting?use_case=<slug>` narrows the existing report. No
  new endpoint: `visible_scope` is one function and a second entry point is a second chance to
  forget it (`FRD-602` §5.3, learned there and applied here without being paid for twice).
- **FR-2 A filter narrows, never widens.** The parameter intersects with what the caller may
  already see. A caller who names a use case outside their scope gets an **empty report**, not a
  refusal and not somebody else's figures.
- **FR-3 An empty report says why it is empty.** `in_scope: false` distinguishes "not yours to
  see" from "nothing happened here". Both are zero rows; only one of them is a measurement.
- **FR-4 Unknown is never rendered as zero.** A gateway that could not be reached, or a report the
  caller was not entitled to fill, is shown as unknown. A page that prints `0.00` because a request
  failed has stated something nobody measured.
- **FR-4a One window failing does not hide the other.** The two periods are two requests, and
  *partial* is a third state — not a variety of *unavailable* (§5.7).
- **FR-4b The reason is the server's own words** (`core/api/error-message.ts`, `CLAUDE.md` §3).
  "The gateway did not answer" is a guess, and the wrong one for every failure that is not a
  timeout.
- **FR-5 The panel lives on the overview**, beside the tiles that say how the use case is
  configured, with no progress bar — there is no denominator and inventing one would be the
  original defect in the other direction. It was first built into the budgets tab; the owner moved
  it on 2026-08-09, and the move is the more honest placement (§5.6).
- **FR-6 The unpriced caveat travels with the figure.** `FRD-403`'s rule: unpriced traffic is
  counted apart and the spend is stated as a lower bound wherever there is any.
- **FR-7 A malformed slug is refused by name**, with the surface's own error envelope.
- **FR-8 The export follows the filter.** CSV is a renderer over the same result, so a narrowed
  report produces a narrowed file by construction.
- **FR-9 Each figure says what it counts.** An "i" beside Spend, Requests and Tokens, because
  spend that excludes unpriced traffic and a request count that includes refusals are exactly the
  two figures somebody would otherwise reconcile against an invoice and give up on (`FRD-206`).

## 5. Design & Architecture

### 5.1 Why the request log and not the budget counter

They answer different questions and only one of them is a record.

`budget_usage` is keyed by `(scope_key, period_key)`, exists only where a limit exists, is seeded
from Postgres into Redis, and expires after five minutes so drift cannot outlive the period
(`FRD-405`). It is exactly right as an enforcement counter and exactly wrong as evidence: it is
absent when nobody set a limit, and it is by design allowed to be approximate between reconciliations.

`request_logs` is the audit trail. It has a row per request, priced, with the outcome, and it is
what `FRD-601` and `FRD-602` already read. Using it here means the use case page, the reporting
screen and the export are three views of one number.

### 5.2 The filter is the security-relevant line

`visible_scope(principal)` returns `None` for oversight, the caller's memberships otherwise, and
`()` for neither. The parameter may only intersect:

```python
if scope is None or use_case in scope:
    scope = (use_case,)
else:
    scope, in_scope = (), False
```

Written as `scope = (use_case,)` — which is the natural way to write it — this reads as a narrowing
and **is a widening**: any member of any use case could then name any other and be told its spend,
from the one endpoint whose whole job is keeping one team's figures out of another's screen. That
is mutation `N55`, and it was shown to fail before it was shown to pass.

### 5.3 Two windows, and the timezone rule that was already paid for

The month is the figure somebody reports; the day says whether something is running away now. Both
are computed as **local** days, from the shared `windowFor` helper that the reporting screen has
used since `FRD-601`. The helper moved from that screen into `core/ui/periods.ts` rather than being
restated, because the rule inside it is a bug that only appears in the evening: `toISOString()`
converts to UTC first, so east of Greenwich "today" becomes yesterday for part of the day.

### 5.4 A failed load is not a page failure

Consumption is a figure the panel offers, not something the reader asked for. It reports its own
absence **in the panel, in the backend's own wording**, and does not raise the page's single
`PageFeedback` banner — a red banner
across the page would say the use case failed to load, and the next thing doubted is every other
figure on the screen. Same argument as `FRD-206`'s: what a reader concludes from an error is part
of the error's design.

### 5.5 What it deliberately does not do

It does not restrict the aggregate by `restrict_members_to_own_requests` (`FRD-505` FR-4). That
switch is about **requests and their content**, which is what a member could otherwise read about a
colleague; a use case's total spend is not that, and the reporting screen has never restricted it
either. Applying it here and nowhere else would produce two numbers for one month.

### 5.6 On the overview, not in the budgets tab

It shipped inside the budgets tab, which was the shape of the defect it fixes — consumption living
with limits. Moved on the owner's reading: *"consumption passt eher in overview rein, dann hat man
alles im Blick."*

That is the right cut. The overview already answers *where does this use case stand*, with tiles
for members, keys, budgets and retention — how it is **configured**. Consumption is what it has
**done**, and the two belong on one screen. Leaving it beside the budgets kept implying the
relationship the whole feature exists to break.

It is a **child component** (`consumption-panel`), not another block in the parent, per
`CLAUDE.md` §3: the page owns the load, the panel owns the rendering. Its load is called from the
page's `load()` and deliberately **not** from `loadBudgets()` — hanging it off the budget load
would mean adding a budget refetched it, and removing the budgets panel one day silently removed it.

### 5.6a Not being in the group is not a warning

The out-of-scope state is a plain callout, not a warning. Nothing is wrong when it appears: the
reader is not in the Keycloak group, which is the ordinary condition of a use case they created a
minute ago (`FRD-209` — AIRA never writes to the directory).

Found by the browser suite, and not as a design note: the retention test broke on a **strict-mode
violation**, because the overview now had two elements matching `.callout--warning`. The selector
was fragile and has been given an id, but the more useful reading is the one the failure pointed
at — every new use case was being greeted by two alarms about nothing, and a page that cries wolf
twice teaches the reader to skip the third.

### 5.7 Partial is a third state, and the first version got it wrong

Two windows are two requests, and the first version tracked their failure in **one boolean written
by both**. So the month would arrive, clear the flag, and then the day's request would fail and set
it again — hiding a figure that had already been fetched. Whichever request finished last decided
what the reader saw, which is not a rule anybody chose.

It is a **count** of failed windows now, and the panel distinguishes three cases: nothing arrived
(*unavailable*), something arrived and something did not (*partial* — what is known is shown, the
rest is an em dash with a note), and everything arrived. Found by reading the code back rather than
by a failing test, which is why the test was written to fail against it first.

## 6. Data Model

None. No table, no column, no migration — which is the point: the data was already there.

## 7. API / Interface Contract

`GET /v1beta/reporting?use_case=<slug>&from=&to=` — the existing report, narrowed. Response gains
two keys on every call, filtered or not:

| Key | Meaning |
|---|---|
| `use_case` | the slug it was narrowed to, or `null` |
| `in_scope` | `false` when the report is empty because the caller may not see that use case |

SPA: a **Consumption** card on a use case's **Overview**, below the configuration tiles, with
*This month* and *Today*, each stating spend, requests and tokens with an "i" saying what each
counts, and a link to the full reporting screen.

## 8. Security & Privacy

- Aggregates only — no prompt, no response, no per-request row. Nothing here is reachable that
  `FRD-601` did not already serve to the same caller.
- The narrowing is an intersection (§5.2), guarded by `N55`.
- `in_scope` reveals nothing: a caller learns only that a slug they typed is not theirs, which they
  already knew from their own membership list.

## 9. Observability

None added. The endpoint is the one already traced.

## 10. Testing & Acceptance Criteria

- **Unit (gateway)** — a use case with no budget reports what it consumed; a filter narrows an
  oversight report; a member's filter cannot widen; an empty report says whether it was allowed to
  be full; the CSV is narrowed by the same filter; a malformed slug is refused by name.
- **Unit (frontend, panel)** — the figures render with **no budget present** (the defect, asserted
  on the DOM); unknown renders as unknown and not as zero; out-of-scope renders differently from
  unreachable; one window arriving without the other shows what arrived; a use case that really
  consumed nothing renders zero; each figure's "i" carries text (asserted as *rendered* text — the
  hint takes projected content, and passing it as an attribute is silently ignored, which is how
  three hints on the requests screen came to say nothing at all).
- **Unit (frontend, page)** — both windows are requested; a failure reports in the panel and not
  through the page banner; a window that arrived survives the other one failing (written to fail
  against the shared-flag version, §5.7).
- **Mutation** — `N55` (the filter cannot widen), `N56` (an empty report says whether it was
  allowed to be full). Both shown to fail against the broken code first.
- **Live** — run against the stack's own Postgres: the smoke-test use case reports its 59 requests,
  10,664 tokens and 0.0037, and a member of another use case asking for it gets zeroes with
  `in_scope: false`.

**Acceptance**
- *Given* a use case with **no budget at all** and traffic in the current month, *when* its
  **overview** is opened, *then* the tokens and the spend are shown; and its budgets tab still says
  that no limit is set and carries no consumption figure at all.

## 11. Dependencies & Risks

- Depends on `FRD-601` (the aggregation and the visibility rule) and `FRD-403` (pricing).
- **Risk — two numbers for one month.** Mitigated by using the same service and the same window
  helper. The way this goes wrong is somebody adding a second query "just for this panel"; §5.1
  says why not.
- **Risk — the figure is read as complete.** Unpriced traffic is counted apart and the caveat is
  rendered with the figure (FR-6).
