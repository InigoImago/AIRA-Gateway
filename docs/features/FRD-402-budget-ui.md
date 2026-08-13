# FRD-402 — Budget UI (set limits + view consumption)

> **Extended by [FRD-403](FRD-403-cost-budgets.md)**: the tab leads with a spend
> limit and a spend bar, and names traffic whose cost is unknown. Prices are maintained under
> **Models & prices**.

> Phase: 4 · Status: **Done** · Owner: Vadim Scheibe
> Builds on FRD-400 (definitions) + FRD-401 (usage accounting).

## 1. Summary
A **Budgets** tab in the use-case detail to set use-case and per-member budgets and **see current
consumption** against them, with a threshold warning.

## 2. Design
- CRUD via `UseCaseService` (`budgets` list/create/delete) → FRD-400 endpoints.
- **Consumption**: the gateway exposes current usage per scope+period; the SPA reads it (via `/gw`
  proxy or a management passthrough) and renders progress bars (used / limit) with a colour when a
  configurable threshold (e.g. 80%) is crossed. Since ADR-0007 `GET /v1beta/usage/{use_case}`
  requires an authenticated caller entitled to that use case; when the gateway does not accept the
  SPA's token the tab still renders the configured limits, without consumption.
- **Visibility rule** (verified end-to-end, see the ADR-0007 addendum): "entitled" means the
  caller belongs to the Keycloak group `/use-cases/<slug>` — the same membership the data plane
  uses (FRD-102). A use case created in Management has no such group yet, so its consumption
  stays hidden; the tab then names the missing group instead of implying the gateway is down.
- Admin edits; members read.

### 2.1 Added 2026-08-11 — a window, a currency, and a figure that may not exist
- **Creation opens a window** (`core/ui/modal.ts`), not a form unfolding under the list. The same
  three screens (budgets, rate limits, anomaly rules) each had their own; the fifth copy is where
  the Escape handler and the focus move start to differ.
- **Every monetary label names its currency.** The spend limit was a bare number; every provider
  behind this gateway prices in dollars and the catalog is dollars per million tokens, so a budget
  in anything else would be a conversion nobody performed — and the reader would assume otherwise.
- **A per-person budget has no single figure.** `usage` answers with the *reader's own* and says so
  (`measured_for`); to a reader the row does not bind it answers `null`, and the card draws **no
  bar** and says why. Zero is what an untouched allowance looks like, so rendering the unknown as
  zero would be a confident wrong statement rather than a missing one (`FRD-603`).

## 3. Testing & Acceptance
- Vitest: service budget calls; component renders limits + consumption bars; threshold styling.
- Acceptance: set a budget, make requests, watch the bar fill and warn near the limit; over the limit
  the gateway 429s (FRD-401) and the UI shows it exhausted.
