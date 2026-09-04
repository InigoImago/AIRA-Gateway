# FRD-203 — Angular shell: OIDC login, role-aware nav, use-case views

> Phase: 2 · Status: **Done (Phase 2)** · Owner: Vadim Scheibe
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

## 7. When signing in again is not the answer (2026-09-04)

*Reported from use: you can authenticate against Keycloak, the installation still refuses the token
it issues, and the page flickers through the login round trip — throwing an error each time — until
the account is locked out.*

**The mechanism.** `authInterceptor` treats every `401` on a first-party call as *this session is
over* and calls `AuthService.reauthenticate`, which redirects to Keycloak. Keycloak still holds a
valid SSO session, so it answers the authorization request without asking anybody anything and
redirects straight back. The console exchanges the code, calls the API, is refused for the same
reason as before, and goes round — at page-navigation speed, with no backoff.

Nothing in that chain is wrong on its own. A `401` really is usually an expired session, and a
redirect really is usually the only available action. What was missing is that **a login cannot fix
a refusal that is not about the login**: a mismatched audience or issuer, a clock too far apart
(`FRD-134`), a session this deployment no longer recognises. A fresh token has the same properties
as the last one.

**Why the guard that existed could not see it.** `reauthenticate` already had a `reauthenticating`
flag, so that five panels each getting a `401` would not start five logins. That is a real guard for
a real case — and it lives *in the service*, while the thing it would have to survive is a full-page
navigation that destroys the service. Its own comment said as much: *"the flag is never cleared,
because the only thing that follows is a full-page navigation to Keycloak."* Correct about the case
it was written for, structurally blind to the one that hurts. `LESSONS.md` §1's shape again.

**The fix.** Attempts are counted in `sessionStorage` — outside the object, because that is what
survives the redirect — and past **three within two minutes** the console stops redirecting and says
so instead. Three because one is an ordinary expiry, two is that plus a race between panels, and a
third inside two minutes is the same refusal coming back. Keycloak's own brute-force default trips
at thirty; the point of this number is to be reached long before an account is locked.

The counter is cleared by the first first-party call that **answers**. That is the only evidence
worth trusting: everything the console can check about itself — a token that parses, an expiry in
the future — was just as true on every pass through the loop.

**And the way out ends the session at the provider.** `oauth.logOut()` rather than `logOut(true)`:
the local-only form clears the tokens here and leaves the SSO session standing, so the next
navigation signs the reader straight back in. Keycloak is the half that keeps saying yes, and the
escape has to reach it. The shell renders the explanation instead of the routes, like
`startupError`, because every screen behind it needs the token that is being refused.

**Testing.** Six Vitest cases in `auth.service.spec.ts` drive the loop across freshly built services
— the closest a test gets to four page loads — covering the stop, the reset on success, the window
rather than a lifetime, unreadable storage, and that the escape is the provider-side logout. Six
more in `tools/tests/test_the_console_cannot_loop_through_the_login.py` assert the *shape*, because
`tools/mutation_check.py` runs pytest and nothing else: a mutation reintroducing the defect would
otherwise be reported as caught by no test the harness can run. Mutations `LOOP1` and `LOOP2`.

**What this does not do.** It does not diagnose *why* the token is refused — the console cannot see
the server's reason, and guessing would be worse than the sentence it prints. The gateway's own
`AIRA_MAX_AUTH_FAILURES_PER_MINUTE` (60) bounds the other side of the same event; what had no brake
was the redirect.

