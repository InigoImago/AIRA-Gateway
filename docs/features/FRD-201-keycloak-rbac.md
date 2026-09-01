# FRD-201 — RBAC: roles + object-level use-case permissions

> Phase: 2 · Status: **Done (Phase 2)** · Owner: Vadim Scheibe
> Related: `docs/PRD.md` §3, §5 (FR-MG-2); `docs/ROADMAP.md` Phase 2; builds on FRD-200; `roles.py`

## 1. Summary
Map the five AIRA roles (from Keycloak) onto Django groups/permissions and enforce them in DRF, plus
**object-level** access to use cases via `django-guardian` (a Use Case User only sees their assigned
use cases; IT Security has scoped, cross-use-case visibility). This is the authorization layer on top
of FRD-200's authentication.

## 2. Goals & Non-Goals
**Goals**
- Sync Keycloak roles → Django groups on login. **Amended by `ADR-0017` (2026-08-09):** the roles are three, and they are resolved from the token's `groups` claim through `AIRA_ROLE_GROUPS` — `realm_access.roles` is not read by either plane. Every sentence below that says *realm role* means *the role a group confers*.
- DRF permission classes for role checks (e.g. `IsGlobalAdmin`, `IsITSecurity`, `IsUseCaseAdmin`).
- Object-level use-case permissions via `django-guardian`: membership grants `view`; use-case admin
  grants `change`/`manage`.
- Queryset scoping helpers so list endpoints return only objects the caller may see.

**Non-Goals**
- The use-case model/CRUD itself (FRD-202) — this defines the permission mechanics it will use.
- IT Security cross-use-case dashboards (Phase 5). Kafka (FRD-204).

## 3. Functional Requirements
- **FR-1 Role sync**: on authentication, ensure the user's Django groups match the realm roles in
  the token (add/remove) so RBAC reflects Keycloak as the source of truth.
- **FR-2 Role permissions**: DRF permission classes per role; `GlobalAdmin` implies all; `ITSteuerung`
  read-only oversight; `ITSecurity` scoped security views; `UseCaseAdmin`/`UseCaseUser` per-object.
- **FR-3 Object-level**: `django-guardian` assigns per-use-case perms (`view_usecase`,
  `change_usecase`, `manage_usecase`) on membership/admin assignment.
- **FR-4 Queryset scoping**: `get_objects_for_user`-style helpers; `GlobalAdmin`/`ITSteuerung` see all
  (governance), others see only permitted objects.
- **FR-5 Least privilege**: default deny; IT Security visibility is metadata-scoped (payload
  redaction respected later).

## 4. Design & Architecture
- Add `django-guardian` to INSTALLED_APPS + its backend. Role slugs come from `aira_management.roles`.
- A small `rbac` module: permission classes + `scope_queryset(user, model, perm)` helper.
- Role→group sync runs in the FRD-200 auth class (claims → groups).

## 5. Testing & Acceptance
- Hermetic tests: token with role X → correct groups; permission classes allow/deny per role;
  guardian object perms grant/deny per use case; queryset scoping returns only permitted objects.
- **Acceptance**: a Use Case User sees only their use cases; a Global Admin sees all; IT Steuerung
  has read-only oversight; unauthorized actions return 403.

## 6. Dependencies & Risks
- Builds on FRD-200; used by FRD-202. New dep: `django-guardian`. Risk: role/group drift → sync from
  the token on every request; treat Keycloak as source of truth.
