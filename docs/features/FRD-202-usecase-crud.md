# FRD-202 — Use-case CRUD & membership (self-service)

> Phase: 2 · Status: **Done (Phase 2)** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §5 (FR-MG-1/2); `docs/ROADMAP.md` Phase 2; builds on FRD-200/201

## 1. Summary
The core self-service feature: create and manage **use cases** and their **members**. A use case has
a name/slug, description, and processing-logic notes; members are users assigned a use-case role
(admin/user). Assigning a member grants the object-level permissions from FRD-201, and (later)
membership is reflected into Keycloak groups so the gateway authorizes accordingly.

## 2. Goals & Non-Goals
**Goals**
- `UseCase` + `UseCaseMembership` models; slug used as the gateway selector (FRD-102).
- CRUD API under `/api/v1/use-cases/` with RBAC + object scoping (FRD-201).
- Membership management (add/remove members, set use-case role) by a use-case admin / global admin.
- Emit change events (hook for FRD-204 Kafka distribution).

**Non-Goals**
- Pipeline/budget/anomaly config (Phases 3–5). Kafka wiring itself (FRD-204). UI (FRD-203).

## 3. Functional Requirements
- **FR-1 Models**: `UseCase(id, slug, name, description, processing_notes, created_at, ...)`;
  `UseCaseMembership(use_case, user, role, created_at)` with a unique (use_case, user).
- **FR-2 CRUD**: list/create/retrieve/update/delete use cases; scoped by FRD-201 (users see theirs,
  governance sees all). Slug unique + validated (matches the gateway selector charset).
- **FR-3 Membership**: add/remove members, set use-case role; grants/revokes guardian object perms.
- **FR-4 Self-service**: a use-case admin manages their own use case's members; a global admin any.
- **FR-5 Change hook**: on create/update/membership change, publish a domain event (in-process hook
  now; Kafka topic in FRD-204) carrying the use-case + membership snapshot.
- **FR-6 Validation**: friendly, contextual errors (duplicate slug, unknown user, forbidden action).

## 4. Design & Architecture
- A `usecases` Django app: models, DRF serializers/viewsets, guardian perm assignment on membership.
- Slug is the stable identifier used by the gateway `/uc/<slug>` selector and the Keycloak group
  `/use-cases/<slug>` (FRD-204 keeps Keycloak group membership in sync).

## 5. Testing & Acceptance
- Hermetic tests: CRUD honours RBAC/scoping; membership grants object perms; slug uniqueness +
  validation; change hook fires. Coverage gate stays green.
- **Acceptance**: a use-case admin creates a use case, adds a member; the member can see it, a
  non-member cannot; a global admin sees all.

## 6. Dependencies & Risks
- Builds on FRD-200/201. Feeds FRD-203 (UI) and FRD-204 (Kafka). Risk: slug/selector charset drift →
  validate against the gateway's rules.
