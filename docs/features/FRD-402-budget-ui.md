# FRD-402 — Budget UI (set limits + view consumption)

> Phase: 4 · Status: **Done** · Owner: Vadim Scheibe · Last updated: 2026-08-04
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

## 3. Testing & Acceptance
- Vitest: service budget calls; component renders limits + consumption bars; threshold styling.
- Acceptance: set a budget, make requests, watch the bar fill and warn near the limit; over the limit
  the gateway 429s (FRD-401) and the UI shows it exhausted.
