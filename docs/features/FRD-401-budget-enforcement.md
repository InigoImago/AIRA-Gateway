# FRD-401 — Budget enforcement & usage accounting

> **Extended by [FRD-403](FRD-403-cost-budgets.md)**: the gateway prices every
> request from the model catalog and enforces cost limits with the same 429. Consumption that
> cannot be priced is counted apart, never as zero.

> Phase: 4 · Status: **Done** · Owner: Vadim Scheibe
> Builds on FRD-400 (budget definitions in the gateway read-model), FRD-102/103 (attribution + logs).

## 1. Summary
The gateway accounts usage per scope+period and **rejects requests that would exceed a budget**,
before dispatch, with a shaped `429 RESOURCE_EXHAUSTED`. *As built, what refuses is a counter
already at its limit* (`budgets/ledger.py`), spent plus reserved: a request larger than what is
left is admitted and settled at what it cost, so a remainder can go negative (`FRD-610` §3.4,
`FRD-139` FR-4).

## 2. Design
- **Usage accounting**: a gateway table `budget_usage` keyed by `(scope_key, period_key)` holding
  running `tokens` + `requests`. `scope_key` = `uc:<slug>` or `member:<slug>:<subject>`; `period_key`
  = `YYYY-MM` (month) or `YYYY-MM-DD` (day). Incremented after each dispatch (async, off the hot path
  where possible). *As built:* a third key, `installation:`, for requests that name no use case
  (`FRD-610` §3.1); the member key is the **person** — the username where the credential carries
  one, the subject otherwise (`ADR-0019`); and the row carries `cost_nanos` and
  `unpriced_requests` beside `tokens` and `requests` (`FRD-403`).
- **Check (pre-dispatch)**: resolve the applicable budgets for the request's use case + subject; for
  each, read the current period's usage; if a limit is already met, reject with `429` (message names
  the scope + period). Request-count limits block the request itself; token limits block when usage ≥
  limit (tokens are only known after the call, so token budgets are enforced as "stop once exceeded").
- **Reset**: implicit via `period_key` — a new period starts a fresh counter row.
- Decisions traced (`aira.budget.*`). Enforcement toggle (`enforce_budgets`), default on. *As
  built, no `aira.budget.*` span attribute exists: a refusal is on the audit row as
  `budget_exceeded` (`FRD-122`), and its message names the scope, the period and the limit that
  ran out (`budgets/keys.py`).*
- **A lifted budget counts.** A budget switched off (`enabled` false) refuses nothing and is not
  reserved against, and every request is still booked into its counter — in Postgres and in the
  shared counter, which outlives the switch. So it behaves like the installation's own toggle:
  the figures stay true, and putting the budget back caps spending from what was spent. It once
  stopped counting as well, and putting it back handed out a fresh allowance for the rest of the
  period (`test_a_lifted_budget_still_counts.py`).

## 3. Testing & Acceptance
- Hermetic: a request-count budget blocks the N+1th request; a token budget blocks once exceeded;
  usage rolls over at the period boundary; disabled/unlimited axes don't block. Route-level 429.
- Acceptance: with a 3-request/day budget on `demo-uc`, the 4th request returns 429; next day resets.
