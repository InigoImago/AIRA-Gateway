# FRD-203 — Angular shell: OIDC login, role-aware nav, use-case views

> Phase: 2 · Status: **Draft** · Owner: Vadim Scheibe · Last updated: 2026-08-04
> Related: `docs/PRD.md` §4.2; `docs/ROADMAP.md` Phase 2; builds on FRD-200/201/202; `ADR-0001`

## 1. Summary
Turn the Angular shell into a working management UI: **OIDC login** against Keycloak, an
authenticated API client that sends the bearer token to the DRF backend, **role-aware navigation**,
and the first real screens — a use-case list/detail with membership management.

## 2. Goals & Non-Goals
**Goals**
- OIDC (`angular-oauth2-oidc`) code-flow + PKCE against the `aira` realm; token stored, refreshed.
- HTTP interceptor attaching `Authorization: Bearer <token>` to `/api/v1` calls; 401 → re-login.
- Role-aware navigation (from `me`): show Security/Governance/Admin sections per role.
- Use-case list + detail (create/edit, manage members) wired to FRD-202 endpoints.

**Non-Goals**
- Pipeline builder, budgets, anomaly/security consoles (Phases 3–5). API-key issuance UI (FRD-205).

## 3. Functional Requirements
- **FR-1 Auth**: login/logout, silent refresh, guarded routes; unauthenticated → login.
- **FR-2 API client**: typed services for `me` and `use-cases`; bearer interceptor; error handling
  surfacing the backend error envelope.
- **FR-3 Nav**: sidebar/menu items shown by role (from `me` roles).
- **FR-4 Use-case screens**: list (scoped), detail, create/edit form, member add/remove.
- **FR-5 UX**: loading/empty/error states; forms validate against backend rules.
  *Delivered in full on 2026-08-05* (see DEVLOG): every load and mutation reports its outcome via
  the shared `errorMessage()` unwrapper, loading states are distinct from empty ones, invalid
  submits are disabled with the reason inline, and destructive actions confirm first.
- **FR-6 Accessibility & layout** (added 2026-08-05): tablist/tabpanel semantics, a label for
  every control, accessible names on icon-only buttons, visible focus rings; no horizontal page
  overflow — wide tables scroll inside their card, long identifiers break, nav/tab strips scroll
  on small screens.

## 4. Design & Architecture
- `core/auth` (OIDC config, guard, interceptor), `core/api` (typed clients + `errorMessage`),
  `core/ui` (`ConfirmService`), `features/use-cases` (list/detail/form components). Standalone
  components + signals (Angular 22).
- **All mutable component state is a signal.** The app runs *zoneless* (no zone.js), so a plain
  property changed from code — resetting a form in an HTTP callback, switching a form's scope —
  schedules no re-render and the UI silently keeps the old value. Two-way `[(ngModel)]` is
  therefore written as `[ngModel]="x()" (ngModelChange)="x.set($event)"`.

## 5. Testing & Acceptance
- Vitest unit tests (browserless): guard redirects unauthenticated; interceptor adds the header;
  use-case list renders scoped data (mocked API); nav reflects roles. Keep green.
- **Coverage gate** (2026-08-05): `angular.json` enforces 90% statements / 92% branches / 93%
  lines / 75% functions, mirroring the Python gate. Tests exercise the rendered DOM and real
  click/keyboard interactions, not just component methods — that is what catches the zoneless
  re-render bugs described below.
- **Acceptance**: log in via Keycloak, see role-appropriate nav, create a use case and add a member.

## 6. Dependencies & Risks
- Builds on FRD-200/201/202. Risk: OIDC redirect/CORS config → align realm client redirect URIs +
  backend CORS. Risk: token/refresh handling → use the library's flows, test the guard/interceptor.
