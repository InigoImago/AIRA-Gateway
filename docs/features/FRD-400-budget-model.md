# FRD-400 — Budget model & distribution

> **Extended by [FRD-403](FRD-403-cost-budgets.md)**: a budget may now cap **spend**
> in the installation currency, not only tokens and request counts. A token limit could not
> express cost — a token differs in price by more than an order of magnitude between models.

> Phase: 4 · Status: **Done** · Owner: Vadim Scheibe
> Related: `docs/ROADMAP.md` Phase 4; builds on FRD-102 (attribution), FRD-103 (request logs),
> FRD-202/204 (use-case CRUD + Kafka distribution). Companion: FRD-401 (enforcement), FRD-402 (UI).

## 1. Summary
Define usage **budgets** per use case and per head, authored in Management and distributed to the
gateway (like use cases / pipelines). This FRD covers the **model + CRUD + distribution** only;
enforcement (FRD-401) and UI (FRD-402) follow.

## 2. Concept
- A **budget** caps usage for a **scope** over a **period**.
  - **Scope**: `use_case` (the whole use case) or `each_member` (every person in it, counted
    separately). A third naming one person was removed on 2026-08-14 — see §2.1.
  - **Period**: `day` or `month`. Usage resets at the boundary, and the boundary is **UTC** —
    midnight UTC for a day, the first of the month for a month. One clock for an installation
    whose callers, models and operators sit in several, and the counter key is literally
    `strftime("%Y-%m-%d")` / `%Y-%m` of `datetime.now(UTC)`.

    Stated because it is a couple of hours away from the reader's calendar, in both directions,
    and the difference is invisible until it is expensive: in central Europe traffic at 00:30
    local counts against yesterday's daily budget, and a monthly budget that ran out goes on
    refusing for the first hours of the new month. The console's Period control says so; it said
    nothing until 2026-08-19, which is how a figure somebody is accountable for gets misread
    without anything being wrong with it.
  - **Limits**: `limit_tokens` and/or `limit_requests` (either/both; null = unlimited on that axis).
- Cost-based limits need per-model pricing (not yet available) → **tokens + request count first**;
  monetary budgets are a later extension.

### 2.1 Two scopes, and why the third was removed (2026-08-14)
`use_case` is a **shared pot**: the first caller to arrive can spend all of it, which is the right
shape for a cap on what the organisation is willing to spend and the wrong one for fairness.

`each_member` is *a fair share per head*: one configured row, one counter per person, applying to
whoever turns up including people who join afterwards.

A third scope, `member`, bounded **one named person**. It is gone on the owner's decision:
singling somebody out is not a governance decision this product should make easy, and it only ever
answered the question after somebody had already caused a problem — while needing a row per person,
kept up to date as people join and leave. Existing rows are deleted by a migration in each plane
rather than left in place: a stored scope that no longer resolves is a rule enforced by nothing and
visible in nothing.

The two that remain are not shorthands for each other. A per-person cap of 100 does not cap the use
case at 100 — with forty members it caps it at 4 000 — so an installation that wants both sets both,
and the gateway takes them **all-or-nothing** the way it already does for rate limits (`FRD-405`
FR-4).

Three consequences worth writing down:
- **No row names anybody.** A `subject` sent with one is dropped rather than refused: keeping it
  would key the uniqueness constraint on a name the row does not honour, so a second edit would
  create a second budget instead of replacing the first.
- **The counter key is the caller**, and its stored shape is unchanged by the removal — a figure
  written before it keeps being found, which is the whole reason `usage_key` is not free to move.
- **Consumption has no single figure.** `GET /v1beta/usage/{use_case}` therefore reports the
  *reader's own* number and says so (`measured_for`), and answers `null` — never zero — to a reader
  the row does not bind. Zero is what an untouched allowance looks like, and this is the one place
  where the two would be indistinguishable (`FRD-603`).

### 2.2 What the removal took with it
The `member` scope was the only place the two alphabets a credential answers "who is this" in were
ever reconciled: an API key's subject *is* its owner's username (`FRD-604`), an OIDC token's is the
directory's user id, and a row written by typing a name matched either. Nothing reconciles them now,
so **one person using both a browser and a key has two per-head allowances rather than one**.

That was already true of `each_member` before the removal — it has always keyed on the caller — and
it is recorded here and in `aira_gateway.scopes` rather than lost with the tests that covered the
named scope. The fix, if it is ever wanted, is a stable identity for a person across credentials,
not a scope that names one.

## 3. Functional Requirements
- **FR-1**: A use-case admin defines budgets for their use case: one `use_case`-scoped budget per
  period, and any number of `member`-scoped budgets (one per member per period).
- **FR-2**: Validation — at least one limit set; positive integers; `member` scope requires a
  `subject`; `use_case` and `each_member` **drop** any subject sent with them; uniqueness on
  `(use_case, scope, subject, period)`.
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
