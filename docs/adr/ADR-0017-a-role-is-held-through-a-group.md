# ADR-0017 — A role is held through a group, and only through a group

- **Status:** Accepted
- **Date:** 2026-08-09
- **Deciders:** Vadim Scheibe
- **Amends:** [ADR-0009](ADR-0009-gateway-knows-roles.md) — its shared role *definition*
  stands unchanged; where it says both services read `realm_access.roles`, they now read the
  configured group mapping. **Supersedes nothing else.**

## Context

AIRA has had **two** ways of learning who somebody is, and they were never reconciled:

| What | Where it came from |
|---|---|
| The five AIRA roles | Keycloak **realm roles** (`realm_access.roles`), read by both planes |
| Access to a use case | Keycloak **groups**, mapped onto Django groups and guardian object permissions (`FRD-209`) |

The second was introduced deliberately: `FRD-209` exists because membership had been granted one
person at a time *and* had two answers that disagreed. Its conclusion — a grant binds a
**principal**, and a group is a principal — was never applied one level up, to the roles.

The owner's statement settles it: **group memberships are the single point of truth, and
individual memberships are set in Keycloak or by an external system that drives Keycloak.** AIRA
never writes to a directory (`FRD-209`), so anything it learns about a person has to arrive in
their token.

Two ways to reach that with Keycloak, and they are genuinely different:

- **Map the realm role onto the group.** Keycloak derives; the token is unchanged; AIRA's code is
  unchanged. The single point of truth is a *convention* — nothing stops an administrator also
  assigning the role directly to a person, and AIRA can only report that, never prevent it.
- **AIRA maps group path → role.** Realm roles are not read at all. A direct assignment is
  structurally inert.

## Decision

**Group membership is the only source of a role, and the mapping lives in AIRA's configuration.**

Three organisation-wide roles are named by configuration:

```
AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung
```

The other two roles **cease to exist as roles**. `use-case-admin` and `use-case-user` are not
organisation-wide facts about a person — they are a relationship between a group and one use case,
and that relationship already has a home in `UseCaseGroupGrant` (`FRD-209`). A Global Administrator
creates a use case and names the group that administers it; administrators name the groups that
are its members.

So the model is:

| Role | Held how |
|---|---|
| Global Administrator | member of the configured group |
| IT Security | member of the configured group |
| IT Steuerung | member of the configured group |
| Use-case administrator | a group grant with role `admin` **on that use case** |
| Use-case user | a group grant with role `user` **on that use case** |

**A role the configuration does not name is held by nobody**, and Management **refuses to start
outside `local`** when no group is named for `global-admin`: an installation with no administrator
cannot be repaired through its own console, so booting into that state is a production outage that
announces itself hours later as "nobody can log in properly".

The refusal is **environment-shaped**, not unconditional, because `ADR-0015` already decided this
exact question for `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` and the OIDC audience — and a second
refusal policy with different rules would be its own ambiguity. Locally an unset mapping means
nobody holds a role, which is loud enough: the console says so, on the page where roles are shown,
rather than the service failing to start on a fresh checkout. A hardening pass that breaks the demo
gets reverted.

**An unknown role name in the mapping refuses to start.** The vocabulary is closed
(`aira_common.roles.Role`), and a typo that silently grants nothing is the failure mode this
project has recorded four times.

## Why not the Keycloak-native option

It is the better *fit* and the worse *guarantee*, and the requirement was the guarantee.

Its real advantages are worth stating, because they are being given up: no diff in the
authorization path, no second model for an IAM administrator to learn, and service accounts keep
working without a group-membership mapper — the trap `FRD-209` already hit live.

What it cannot do is make the single point of truth true. A role mapped onto a group is still a
role that can be assigned to a person, and "the console shows you when somebody did" is a report,
not a rule. The owner's requirement was **no ambiguity**, and a convention that a screen watches is
an ambiguity with a witness.

## Consequences

**Accepted costs.**

- The `groups` claim becomes load-bearing for *all* authorization, not only for use-case access. A
  token without it grants nothing at all. This is the trap `FRD-209` found live on service accounts,
  and its blast radius is now larger — which is why the group-membership mapper on **every** client
  is a documented requirement and a startup-visible one.
- A Keycloak administrator assigning the realm role achieves nothing. That is the intent, and it is
  a *silent* non-effect — the class of failure this project keeps recording. Mitigated by naming
  it in `INTEGRATIONS.md` and by the console stating which group confers each role.
- One wrong path in the mapping is an outage for that role. Local, and visible: the console shows
  the configured paths beside what the token actually carries.
- Migration is a realm change, not a code change, for every installation: the three groups must
  exist and carry the people who hold the roles.

**Gained.**

- One mechanism. Group membership decides roles and use-case access, and `sync_user_roles` and
  `sync_user_groups` read the same claim.
- The answer to "which group makes somebody a Global Administrator" is a **setting** — displayable,
  dumped into `CONFIGURATION.md` by the existing settings dump, and available without the Keycloak
  admin API, which matters because that API is unreachable exactly when somebody is debugging
  access.
- Portability. Any identity provider that emits a groups claim can drive AIRA, which is the
  precondition for `FRD-118` and for a customer on Entra ID or Okta.
- Two fewer roles. `use-case-admin` and `use-case-user` as realm roles were always a second, weaker
  statement of what the object grant already said — and `IsUseCaseUser` was defined and used
  nowhere, which is what a role nobody needs looks like.

## The smaller half nobody expected

The gateway never used the two use-case roles. Its whole role vocabulary is `is_governance`,
`is_oversight` and `may_act_on_incidents`, all built from the three organisation-wide roles — so
the data plane's change is **one function**: where it read `realm_access.roles`, it reads the
mapping. Everything else in the gateway is untouched.

In Management, three call sites named a use-case role, and each is re-derived from what it actually
meant:

| Site | Was | Becomes |
|---|---|---|
| Create a use case | `global-admin` or `use-case-admin` | **`global-admin`** — the owner's rule, and a narrowing |
| Directory search | `global-admin` or `use-case-admin` | `global-admin`, or **administers at least one use case** |
| Run a model test | `global-admin`, `it-security`, `use-case-admin` | `global-admin`, `it-security`, or **belongs to at least one use case** — `FRD-504`'s own rule: whoever may call a model may test one |
