# FRD-102 — Request attribution & use-case selection (OIDC)

> Phase: 1 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §5 (FR-GW-3); `docs/ROADMAP.md` Phase 1; builds on FRD-101; `ADR-0006`

## 1. Summary
An OIDC bearer token authenticates the **identity** (`sub`, groups) but does not say **which use
case** a request targets — a user can belong to several. This FRD adds an explicit **use-case
selector** per request and authorizes it against the caller's **Keycloak group** membership carried
in the token, then attaches an **attribution** (subject + method + use case) to the request for
later persistence/budget/anomaly work. Focus is OIDC; API-key-bound use cases come with issuance in
Management (Phase 2, FRD-205).

## 2. Goals & Non-Goals
**Goals**
- Select the target use case via **path prefix** `/uc/<use-case>/v1beta/...` **or** header
  `X-AIRA-Use-Case`; the **header overrides** the path.
- Model use-case membership as Keycloak **groups** (`/use-cases/<slug>`); the token carries a
  `groups` claim; the Gateway authorizes `use_case ∈ groups` (403 otherwise).
- Attach `Attribution(subject, method, use_case)` to `request.state`; add OTel span attributes.
- Optional `require_use_case` policy (reject OIDC requests without a use case).

**Non-Goals**
- Use-case *config* (pipelines, budgets, anomaly rules) — those come from Management via Kafka
  (Phase 2). This FRD only resolves + authorizes + records the use case.
- API-key → use-case binding (Phase 2, FRD-205). API-key/demo requests are attributed to the
  provided use case **without** the group check (their binding arrives later).

## 3. User Stories
- As a **user in several use cases**, I pick the target use case per request (base URL or header),
  and AIRA authorizes it against my group membership.
- As **operations**, every stored request is attributed to a subject + use case for cost/audit.

## 4. Functional Requirements
- **FR-1 Path selector**: an ASGI middleware strips a leading `/uc/<slug>` and stashes the slug so the
  normal Gemini routes still match.
- **FR-2 Header selector**: `X-AIRA-Use-Case: <slug>` takes precedence over the path slug.
- **FR-3 Membership (OIDC)**: the OIDC validator extracts `groups`; groups under `/use-cases/` become
  the principal's accessible use-case slugs. For `method == oidc`, a requested use case must be in
  that set, else **403 PERMISSION_DENIED**.
- **FR-4 Attribution**: build `Attribution(subject, method, use_case)` and set it on `request.state`;
  add span attributes `aira.subject`, `aira.use_case`, `aira.auth_method`.
- **FR-5 Policy**: `require_use_case` (default false) — when true, an authenticated non-demo request
  without a use case is rejected **400 INVALID_ARGUMENT**.
- **FR-6 Non-OIDC**: API-key/demo requests attribute the provided use case as-is (no group check yet).

## 5. Design & Architecture
```
request ─▶ UseCasePathMiddleware (strip /uc/<slug>) ─▶ require_principal (FRD-101)
        ─▶ require_attribution:
              use_case = header X-AIRA-Use-Case  ??  path slug
              if oidc & use_case ∉ principal.use_cases → 403
              request.state.attribution = Attribution(subject, method, use_case)
```
- Membership source is **Keycloak groups** (no Management DB / Kafka needed yet) — consistent with
  "Keycloak = SSO + role/group source". Management later enriches use-case *config* via Kafka.
- `Principal` gains `use_cases: tuple[str, ...]` (OIDC: from groups; api_key/demo: empty for now).

## 6. Data Model
- None persisted here. Attribution is request-scoped; persistence is FRD-103.

## 7. API / Interface Contract
- Selector: `/uc/<use-case>/v1beta/...` and/or `X-AIRA-Use-Case` header. Errors use the Gemini
  envelope: 403 `PERMISSION_DENIED` (not a member), 400 `INVALID_ARGUMENT` (missing when required).

## 8. Security & Privacy
- Authorization is least-privilege: membership strictly from signed token groups. No subject/secret
  in logs; only subject id + use case in span attributes.

## 9. Observability
- Span attributes `aira.subject`, `aira.use_case`, `aira.auth_method`; these flow to Grafana (FRD-001).

## 10. Testing & Acceptance Criteria
- **Tests** (hermetic): group→use-case extraction; selector precedence (header over path);
  middleware path stripping; membership 200 (member) / 403 (non-member); `require_use_case` 400;
  api-key/demo attributed without group check. Coverage gate stays green.
- **Acceptance**:
  - **Given** an OIDC token whose groups include `/use-cases/demo-uc`, **when** I call
    `/uc/demo-uc/v1beta/models/mock-1:generateContent`, **then** 200 and attribution `use_case=demo-uc`.
  - **When** I request `/uc/other-uc/...` (not a member), **then** 403.
  - **When** the header and path disagree, **then** the header wins.

## 11. Dependencies & Risks
- Builds on FRD-101 (auth). Feeds FRD-103 (persist attribution). Keycloak realm needs a **groups
  mapper** + the `/use-cases/<slug>` groups (added to the realm import).
- Risk: group naming drift → restrict to the `/use-cases/` prefix; slugs are the last path segment.
