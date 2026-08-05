# FRD-400 — Budget model & distribution

> **Extended by [FRD-403](FRD-403-cost-budgets.md) (2026-08-05)**: a budget may now cap **spend**
> in the installation currency, not only tokens and request counts. A token limit could not
> express cost — a token differs in price by more than an order of magnitude between models.

> Phase: 4 · Status: **Done** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/ROADMAP.md` Phase 4; builds on FRD-102 (attribution), FRD-103 (request logs),
> FRD-202/204 (use-case CRUD + Kafka distribution). Companion: FRD-401 (enforcement), FRD-402 (UI).

## 1. Summary
Define usage **budgets** per use case and per member, authored in Management and distributed to the
gateway (like use cases / pipelines). This FRD covers the **model + CRUD + distribution** only;
enforcement (FRD-401) and UI (FRD-402) follow.

## 2. Concept
- A **budget** caps usage for a **scope** over a **period**.
  - **Scope**: `use_case` (the whole use case) or `member` (a specific person in the use case).
  - **Period**: `day` or `month` (calendar; usage resets at the period boundary).
  - **Limits**: `limit_tokens` and/or `limit_requests` (either/both; null = unlimited on that axis).
- Cost-based limits need per-model pricing (not yet available) → **tokens + request count first**;
  monetary budgets are a later extension.

## 3. Functional Requirements
- **FR-1**: A use-case admin defines budgets for their use case: one `use_case`-scoped budget per
  period, and any number of `member`-scoped budgets (one per member per period).
- **FR-2**: Validation — at least one limit set; positive integers; `member` scope requires a
  `subject`; uniqueness on `(use_case, scope, subject, period)`.
- **FR-3**: CRUD nested under the use case: `GET/POST /api/v1/use-cases/{slug}/budgets`,
  `DELETE …/budgets/{id}` (admins; members may read). RBAC via the existing object perms.
- **FR-4 Distribution**: `budget.upserted` / `budget.deleted` via the transactional outbox → Kafka
  `aira.budgets` → gateway idempotent consumer → `budgets` read-model.

## 4. Design
- Management `budgets` app: `Budget` model `{use_case FK, scope, subject, period, limit_tokens,
  limit_requests, enabled}` + serializer/validation; nested viewset actions on the use-case viewset.
- Events reuse `usecases.events.emit`; outbox topic map + `aira.budgets` in `aira_common.kafka`.
- Gateway `BudgetRead` `{id, use_case, scope, subject, period, limit_tokens, limit_requests,
  enabled}`; consumer `budget.upserted/deleted`; Alembic migration.

## 5. Testing & Acceptance
- Hermetic: create/list/delete budgets scoped to admins; validation rejects empty/negative limits and
  member scope without subject; events carry the definition; gateway consumer upserts/deletes the
  read-model. Coverage gate stays green.
- Acceptance: define a monthly 100k-token budget for `demo-uc` and a per-member budget for `bob`; both
  appear in the gateway `budgets` read-model after propagation. (Enforcement lands in FRD-401.)
