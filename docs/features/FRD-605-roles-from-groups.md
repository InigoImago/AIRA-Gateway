# FRD-605 — Roles from groups

> Phase: 2 (Management Foundation, revisited) · Status: **Built (2026-08-09)** · Owner: Vadim Scheibe
> Last updated: 2026-08-09
> Decision: [`ADR-0017`](../adr/ADR-0017-a-role-is-held-through-a-group.md). Read that first — this
> document is how it was carried out and what it cost.
> Related: `FRD-201` (RBAC), `FRD-209` (access by group), `ADR-0009` (the shared role definition),
> `ADR-0015` (environment-shaped refusals).

## 1. Problem

Two mechanisms answered "who is this": realm roles for the five AIRA roles, Keycloak groups for
use-case access. The owner's rule removes the first — **group membership is the single point of
truth**, and individual memberships are set in Keycloak or by an external system that drives it.

## 2. Functional Requirements

- **FR-1** Three organisation-wide roles are conferred by configured groups:
  `AIRA_ROLE_GROUPS=role=/path[,/path];role=/path`.
- **FR-2** `realm_access.roles` is **not read**. Assigning a realm role directly grants nothing.
- **FR-3** The match is **exact**. A sub-group does not inherit, and a longer path that starts with
  a configured one confers nothing.
- **FR-4** `use-case-admin` and `use-case-user` cease to exist as roles. Naming either in the
  mapping **refuses to start** — conferring them by group would grant every use case at once.
- **FR-5** A malformed mapping refuses to start, naming the entry.
- **FR-6** Outside `local`, no group for `global-admin` refuses to start.
- **FR-7** Only a **Global Administrator** creates a use case.
- **FR-8** Predicates that named a use-case role are re-derived from the object grants (§4).

## 3. What it touched, and what it did not

The gateway's whole role vocabulary is `is_governance`, `is_oversight` and
`may_act_on_incidents` — all three organisation-wide. It never read the two use-case roles, so its
change is **one call**: `realm_roles(claims)` became `roles_from_groups(groups, mapping)`. Nothing
else in the data plane moved.

Management needed `sync_user_roles` and three call sites. `may_admin`, `may_manage`, `is_member`
and `scope_queryset` needed **nothing** — they had already been asking the object grants since
`FRD-209`, which is the clearest evidence that the two use-case roles had been redundant for
months.

## 4. The three re-derived predicates

Each was translated from what it *meant*, not from the role it named — a faithful-looking
translation is how a rule quietly changes who it covers.

| Site | Was | Is | Why |
|---|---|---|---|
| Create a use case | `global-admin` or `use-case-admin` | `global-admin` | The owner's rule. A **narrowing**: the old role was organisation-wide, so administering one use case let you create another. |
| Directory search | `global-admin` or `use-case-admin` | `global-admin`, or holds `manage_members` on **any** use case | Taking the picker from the people who add members would be `FRD-206`'s defect inverted — a capability with no way in, and that kind does not announce itself. |
| Run a model test | `global-admin`, `it-security`, `use-case-admin` | `global-admin`, `it-security`, or holds `view_usecase` on **any** use case | `FRD-504`'s own sentence: *whoever may call a model may test one*. Narrowing to administrators would have removed people the old rule included, silently. |

`IsUseCaseUser` was deleted: defined, exported, and used by nothing — which is what a role nobody
needs looks like.

## 5. Testing

- **Shared library** (18 cases): a configured group confers; an unnamed one does not; the match is
  exact (`/aira/global-admins-readonly` and `/aira/global-admins/sub` confer nothing); an unknown
  role, a use-case role, a relative path, the bare realm root and an entry with no group each
  refuse **by name**.
- **Gateway**: a group confers its role; **a realm role on the token confers nothing** — asserted
  by sending one, because reading the code only shows the claim is unused, which is not the same as
  showing it cannot grant; an unconfigured gateway grants nobody oversight.
- **Management**: the same negative, plus a malformed `groups` claim confers nothing rather than
  raising — a realm misconfiguration must stop *authority*, not authentication.
- **Boot**: a production deployment with no `global-admin` group is refused, and `local` is not;
  a malformed mapping is a listed reason rather than an import-time traceback, so a review sees
  every problem at once (`ADR-0015`).

### 5.1 The test suite had to be migrated, and that was the audit

Thirteen Management test files granted roles with `{"realm_access": {"roles": [...]}}`. The shared
helper **refuses `use-case-admin` and `use-case-user` by name** rather than quietly granting
nothing — otherwise the suite would still run, still pass, and no longer exercise the authority its
tests are named after. Every refusal was a site somebody had to look at, and several were wrong in
an interesting way: a blanket rewrite to `global-admin` made *boundary* tests pass for the wrong
reason, because a Global Administrator is refused by nothing. Those now use a caller holding **no**
organisation-wide role, which is exactly what a use-case administrator is at the installation
level.

The same trap was in the frontend harness, whose default role was `use-case-admin` — a default
nobody can hold is a harness testing a different product.

## 6. Migration

A realm change, not a code change, for every installation: create the three groups, put the people
in them, name them in `AIRA_ROLE_GROUPS`. Keycloak imports a realm only if it does not exist, so
the dev realm is **recreated** (`deploy/compose/README.md`) — deleting the realm through the admin
API and restarting the container preserves Postgres, which `make destroy` would not.

**Verified live on 2026-08-09.** A client-credentials token for the seeded admin service account
carries `groups: ['/aira/global-admins']` and `realm_access: None`, and the gateway resolves it to
oversight — the role arrives through the group and through nothing else.

## 6.1 Two things the live round found that reading could not

**`/me` was a third answer.** `MeView` reported `realm_access.roles` straight off the claim —
beside `sync_user_roles` and the permission classes. While all three read the same claim they
agreed *by accident*; the moment roles came from groups they did not, and the console was told the
caller had no roles at all while the server let them through. It surfaced as a Global Administrator
being shown no "New use case" button. It reports the caller's Django groups now, which is what
every permission class compares against. `use_cases` was returning the whole `groups` claim, loose
before and wrong once that claim also carries the role groups — it returns slugs.

**A recreated realm orphans every Django user.** New realm, new subject ids; `OidcIdentity` binds
to the old ones (`ADR-0007`), so the next sign-in provisions `admin-ec05a3db` beside `admin`. The
duplicate owns nothing and the original looks abandoned. Deleting the duplicates and re-provisioning
would have destroyed the keys — `ApiKey.owner` cascades, and one account here owned 323. The repair
is a **rebind**, written into `deploy/compose/README.md` because a real change of identity provider
does exactly the same thing.

## 7. Risks

- **The `groups` claim is now load-bearing for all authorization.** A token without it grants
  nothing at all. All five clients in the dev realm already carry the mapper (`FRD-209` closed that
  live), and `INTEGRATIONS.md` states it as a requirement rather than a recommendation.
- **A realm role now achieves nothing, silently.** That is the intent and it is still a silent
  non-effect — the failure class this project keeps recording. Named in `INTEGRATIONS.md` in a
  callout rather than a sentence.
