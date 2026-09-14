# ADR-0025 — Keycloak decides who holds a role; AIRA decides what a role may do

- **Status:** Accepted
- **Date:** 2026-09-13
- **Deciders:** Vadim Scheibe
- **Amends:** [ADR-0017](ADR-0017-a-role-is-held-through-a-group.md). A role is still held
  through a group and only through a group, and AIRA still never writes to the directory. What
  changes is that the **three roles are no longer the whole vocabulary**: an installation may define
  further roles, each bound to one Keycloak group, and may change what `IT Steuerung` may do.
- **Realised by:** [FRD-614](../features/FRD-614-a-permission-is-a-row.md).

## Context

Authorisation is decided in more than forty places across the gateway, Management and the console,
in three languages. Each place asks one of a handful of role sets (`GOVERNANCE_ROLES`,
`OVERSIGHT_ROLES`, `INCIDENT_ROLES`, `CATALOG_ROLES`) or a Global Administrator check written by
hand. Nothing states, in one place, who may do what. Two helpers are defined and never called, and
`docs/ROLES.md` contradicts itself about who may create a use case.

The owner asked for two things:

1. collect the permissions in one place and turn them into an engine, so today's rules are tidy;
2. let an installation create its own roles in the console. A role gets a label, one Keycloak group
   that is checked before it may be bound, and a list of permissions ticked as checkboxes. The
   Global Administrator and IT Security cannot be changed; every other role can.

`ADR-0017` says group membership is the only source of a role and the mapping lives in AIRA's
configuration. An editable role table is a second place that shapes authority, so the relationship
between the two has to be written down rather than assumed.

## Options considered

- **Keep roles as code, add more enum values.** Every new role is a release. It does not meet the
  requirement, which is roles an installation defines for itself.
- **Let AIRA assign roles to people.** Contradicts `ADR-0017` and the owner's rule that the
  directory is the single point of truth about people. Rejected.
- **Keycloak assigns roles; AIRA defines what a role may do.** The directory still decides who is in
  which group. AIRA decides what that group means here. Chosen.

## Decision

**A permission is a named entry in a closed catalogue, and a role is a set of permissions bound to a
Keycloak group.**

1. **One catalogue, both planes.** `aira_common.permissions.Permission` lists every
   installation-wide permission. The gateway, Management and the console ask one function,
   `allows(permissions, wanted)`. No call site names a role any more.
2. **Installation-wide only.** A role grants permissions across the installation. What somebody may
   do *inside* one use case stays with the grants `admin` and `user` on that use case (`FRD-209`).
   `ADR-0007` holds unchanged: seeing every use case never implies acting inside one, and acting
   inside every use case is a permission of its own (`usecase.manage_all`) that must be granted
   explicitly.
3. **Two roles are fixed in code.**
   - `global-admin` holds every permission by construction, including ones added later, and the one
     reserved permission, `role.manage`.
   - `it-security` holds a fixed set.
   - Their groups come from `AIRA_ROLE_GROUPS` as before. They never depend on the database, so a
     broken or empty role table cannot lock anybody out.
4. **Every other role is data.**
   - `it-steuerung` is built in: its group comes from `AIRA_ROLE_GROUPS`, its permissions are stored
     and editable, and it cannot be deleted.
   - Custom roles have a label, exactly one group path and a permission set.
   - A group path is bound to at most one role.
5. **A group is checked before it is bound.** Management asks Keycloak, through the read-only
   directory client, whether the path exists. An unknown path is refused. So is any path while the
   directory cannot be asked: a binding nobody could verify is refused rather than trusted.
6. **Only a Global Administrator manages roles.** `role.manage` is reserved and never offered as a
   checkbox, so no role can raise itself. IT Security and IT Steuerung read roles through
   `role.read`.
7. **Sensitive permissions may be granted, and say so.** Reading all content, stopping traffic,
   investigating requests across use cases, releasing models and purging are marked as sensitive in
   the console. Every change to a role is recorded: who, when, and the set before and after.
8. **Permissions add up.** A person in several groups holds the union of their roles' sets. There
   is no explicit deny: no requirement asks for one, and a deny rule makes every answer depend on
   the order in which rules are read.
9. **The gateway learns roles the way it learns grants.** Management publishes each role on the
   `aira.roles` topic. The gateway keeps a read model, caches it briefly, and when it cannot read
   it, the stored roles confer nothing. The fixed roles are unaffected. A changed permission takes
   effect on the next request after the cache expires, without a new token. A changed group
   membership still needs a new token, as every group change does.

## Consequences

**Gained.**

- One place says who may do what, and a test compares it with every enforcement point.
- An installation can express roles such as "controlling" or "data protection" without a release.
- The catalogue can grow without anybody re-ticking boxes for the Global Administrator.

**Accepted costs.**

- A second place shapes authority beside the directory. It is a table of meanings, not of people,
  and every change to it is recorded.
- The directory client becomes a requirement for binding a custom role. An installation without one
  can still run with the three built-in roles. It cannot bind a new group until the client is
  configured. Before, the client was optional.
- Two planes must agree on a table that changes at runtime. The gateway degrades towards refusing,
  and the propagation delay is a few seconds, not a login.

**Unchanged.**

- A role is still held only through a group, and a Keycloak realm role still grants nothing.
- Grants on a use case (`admin`, `user`) are not affected.
- Management still refuses to start outside `local` without a group for `global-admin`.
