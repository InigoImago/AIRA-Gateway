# FRD-400 — Budget model & distribution

> **Extended by [FRD-403](FRD-403-cost-budgets.md)**: a budget may now cap **spend**
> in the installation currency, not only tokens and request counts. A token limit could not
> express cost — a token differs in price by more than an order of magnitude between models.

> Phase: 4 · Status: **Done** · Owner: Vadim Scheibe
> Related: `docs/ROADMAP.md` Phase 4; builds on FRD-102 (attribution), FRD-103 (request logs),
> FRD-202/204 (use-case CRUD + Kafka distribution). Companion: FRD-401 (enforcement), FRD-402 (UI).

## 1. Summary
Define usage **budgets** per use case and per member, authored in Management and distributed to the
gateway (like use cases / pipelines). This FRD covers the **model + CRUD + distribution** only;
enforcement (FRD-401) and UI (FRD-402) follow.

## 2. Concept
- A **budget** caps usage for a **scope** over a **period**.
  - **Scope**: `use_case` (the whole use case), `each_member` (every person in it, counted
    separately) or `member` (one named person).
  - **Period**: `day` or `month` (calendar; usage resets at the period boundary).
  - **Limits**: `limit_tokens` and/or `limit_requests` (either/both; null = unlimited on that axis).
- Cost-based limits need per-model pricing (not yet available) → **tokens + request count first**;
  monetary budgets are a later extension.

### 2.1 Why three scopes and not two (added 2026-08-11)
`use_case` is a **shared pot**: the first caller to arrive can spend all of it, which is the right
shape for a cap on what the organisation is willing to spend and the wrong one for fairness.
`member` bounds one named person, which only answers the question after somebody has already caused
a problem — and it needs a row per person, kept up to date as people join and leave.

`each_member` is the scope an administrator asks for most: *a fair share per head*. One configured
row, one counter per person, applying to whoever turns up including people who join afterwards.

It is not a shorthand for the other two, and neither substitutes for it. A per-person cap of 100
does not cap the use case at 100 — with forty members it caps it at 4 000 — so an installation that
wants both sets both, and the gateway takes them **all-or-nothing** the way it already does for
rate limits (`FRD-405` FR-4).

Three consequences worth writing down:
- **The row names nobody.** A `subject` sent with it is dropped rather than refused: keeping it
  would key the uniqueness constraint on a name the row does not honour, so a second edit would
  create a second budget instead of replacing the first (the defect already recorded for
  `use_case`).
- **The counter key is the caller.** It is exactly the key a `member` row naming that person would
  have used, so narrowing one individual later does not move their accumulated history to a
  different key mid-period.
- **Consumption has no single figure.** `GET /v1beta/usage/{use_case}` therefore reports the
  *reader's own* number and says so (`measured_for`), and answers `null` — never zero — to a reader
  the row does not bind. Zero is what an untouched allowance looks like, and this is the one place
  where the two would be indistinguishable (`FRD-603`).

### 2.2 Added 2026-08-11 — what a named member row matches
A `member` row is written by an administrator **typing a name**, and the two credentials answer
"who is this" in different alphabets: an API key's subject *is* its owner's username (`FRD-604`),
an OIDC token's is the directory's user id. So the rule bound API-key traffic and bound **nothing
at all** for the same person over OIDC — measured on the live stack as a request limit of one
serving four calls, while the console showed the budget as active. `FRD-125`'s badge-wearing
absent control, one identity system over.

A member row now matches **either** the caller's subject or the name that caller is known by
(`preferred_username`, carried on the `Principal` and the `Attribution`). Three properties:
- **The name is never an identity.** `subject` remains what every audit row records and what every
  counter is keyed on, because a username can be reassigned to somebody else and a subject cannot.
- **The key is the row's own subject** whichever name matched, so a person is one counter rather
  than two and every figure already in `budget_usage` keeps being found — that shape is stored.
- **Matching a name is not matching anyone.** An empty subject binds nobody, and somebody else's
  name is somebody else.

`each_member` is unaffected: it never names anybody, which is one more reason to prefer it.

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
