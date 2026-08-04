# FRD-401 — Budget enforcement & usage accounting

> Phase: 4 · Status: **Planned** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Builds on FRD-400 (budget definitions in the gateway read-model), FRD-102/103 (attribution + logs).

## 1. Summary
The gateway accounts usage per scope+period and **rejects requests that would exceed a budget**,
before dispatch, with a shaped `429 RESOURCE_EXHAUSTED`.

## 2. Design
- **Usage accounting**: a gateway table `budget_usage` keyed by `(scope_key, period_key)` holding
  running `tokens` + `requests`. `scope_key` = `uc:<slug>` or `member:<slug>:<subject>`; `period_key`
  = `YYYY-MM` (month) or `YYYY-MM-DD` (day). Incremented after each dispatch (async, off the hot path
  where possible).
- **Check (pre-dispatch)**: resolve the applicable budgets for the request's use case + subject; for
  each, read the current period's usage; if a limit is already met, reject with `429` (message names
  the scope + period). Request-count limits block the request itself; token limits block when usage ≥
  limit (tokens are only known after the call, so token budgets are enforced as "stop once exceeded").
- **Reset**: implicit via `period_key` — a new period starts a fresh counter row.
- Decisions traced (`aira.budget.*`). Enforcement toggle (`enforce_budgets`), default on.

## 3. Testing & Acceptance
- Hermetic: a request-count budget blocks the N+1th request; a token budget blocks once exceeded;
  usage rolls over at the period boundary; disabled/unlimited axes don't block. Route-level 429.
- Acceptance: with a 3-request/day budget on `demo-uc`, the 4th request returns 429; next day resets.
