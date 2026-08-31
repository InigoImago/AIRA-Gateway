# FRD-614 — A permission is a row, not a predicate

> Phase: 7 · Status: **Draft** · Owner: Vadim Scheibe
>
> Origin: the owner asked whether AIRA could have what enterprise software has — permissions
> granted individually per role, a Global Administrator who holds everything, fine-tuning from
> there, and roles an installation defines for itself.
>
> Related: [`ADR-0017`](../adr/ADR-0017-a-role-is-held-through-a-group.md) (a role is held through a
> group, and only through a group), [`ADR-0009`](../adr/ADR-0009-gateway-knows-roles.md) (one shared
> role definition, both planes), [`ADR-0007`](../adr/ADR-0007-security-hardening-baseline.md)
> (visibility never implies the right to act), [`FRD-209`](FRD-209-access-by-group.md) (grants),
> [`FRD-206`](FRD-206-console-truthfulness.md), [`FRD-613`](FRD-613-one-person-one-identity.md).

**Nothing here is built.** This document is the analysis and the staging, written down so the
decision can be taken against something rather than remembered.

## 1. Why

Authorisation lives in three mechanisms that are nowhere brought together:

| Mechanism | What | Where |
| --- | --- | --- |
| Three organisation-wide roles | a Python enum, conferred by a configured Keycloak group | `aira_common.roles` |
| A grant per use case | `admin` \| `user`, to a group or a person | `FRD-209` |
| **About thirty-three hand-written predicates** | `may_manage`, `is_oversight`, `visible_scope`, `MayRunTests`, `mayCatalogue`, … | both planes **and the console**, in three languages |

The third line is the finding. **There is no place that says who may do what** — there are
thirty-three places, which is exactly the shape this repository keeps paying for (`LESSONS.md`:
*"a rule restated on a second surface"*). `FRD-613` found two of them wrong while they were being
read for something else.

So this feature has two halves, and the first is worth having on its own:

- make "who may do what" **one table** that something checks against reality;
- then let an installation **edit** that table.

## 2. The constraint that shapes the design

`ADR-0017`: a role is held through a group, and only through a group. AIRA never writes to the
directory. An engine that also assigned roles would contradict it.

The cut that does not:

> **Keycloak assigns roles. AIRA defines what a role may do.**

That is not a compromise, it is the cleaner separation: the directory decides who is in which
group, and the installation decides what that group means *here*. Creating a role becomes:
name it, tick its permissions, name the group paths that confer it. It never becomes: name the
people.

Three further constraints, each already settled elsewhere:

- **The gateway may not ask Management on the request path** (`FRD-204`). Role definitions are
  configuration, so they travel the way budgets, limits and grants already do — Kafka into a read
  model, cached briefly, and **refusing rather than admitting** when unreadable, which is what
  `GroupGrantResolver` decided for grants.
- **Permissions are two-dimensional.** *Installation-wide* (what a role grants everywhere) and
  *per use case* (what a grant grants inside one). Folding them together would give an oversight
  role the right to act inside a use case, which `ADR-0007` exists to prevent.
- **Nobody may lock everybody out.** Management already refuses to start without a group named for
  `global-admin`.

## 3. The model

**A permission catalogue** in `aira_common` — a closed vocabulary read by both planes. Named
`Permission`, **not** `Capability`: that word is taken, by the model capabilities in
`aira_common.models`, and two vocabularies under one name is the drift this whole document is
about. Roughly thirty entries, derived from the decision points that exist today:

```
installation-wide                    per use case
  usecase.create                       usecase.edit / usecase.retire
  usecase.read_all                     member.manage / group.manage
  usecase.read_retired / purge         apikey.issue / revoke / issue_for_other
  catalog.read / catalog.write         pipeline.write / budget.write / ratelimit.write
  provider.read                        anomaly.rule.write / model.release
  budget.installation.read / write     trace.read / payload.read / report.read
  anomaly.rule.global.write            gateway.call / smoketest.run
  incident.suspend / incident.lift
  report.read_all / payload.read_any
  trace.filter_by_source_ip
  directory.search
```

**The catalogue is the product.** Everything else is wiring.

**A role becomes a row** — `slug`, `name`, `builtin`, a permission set, and the group paths that
confer it (today an environment variable, `AIRA_ROLE_GROUPS`).

**The Global Administrator is built in and holds everything by construction** — `if builtin_global_admin: return ALL` rather than a stored list. That is the lock-out guard, and it is
also what lets the catalogue grow without an administrator having to re-tick boxes for a permission
that did not exist when they last looked.

**One decision point** replaces thirty-three: `allows(permissions, wanted)`, with two resolvers
(`effective(user)` and `effective_in(user, usecase)`), asked by every DRF permission class, every
gateway predicate, and — through `/api/v1/me` — the console.

## 4. Staging

Deliberately not one change. The first three stages are worth doing **even if custom roles are
never shipped**.

| Stage | What | Behaviour change |
| --- | --- | --- |
| **1** | Define the catalogue; derive the built-in roles' and grant roles' sets from today's predicates; **a test asserting the table answers exactly what the predicates answer, for every (role × permission)** | none |
| **2** | Management asks the engine instead of the predicates | none |
| **3** | `/api/v1/me` returns the caller's effective set; `core/auth/roles.ts` collapses to a containment check | none |
| **4** | Gateway: `Principal.permissions` from a read model over Kafka; degradation decided | none |
| **5** | Roles become data: editable sets, custom roles, group mapping in the database (the environment variable stays as the `global-admin` bootstrap), a console screen, audit rows | **the feature** |
| **6** | Grant roles per use case become data too — *"may read traces, may not change the pipeline"* | optional |

Stage 1 is where it is found out whether the model holds: if the equivalence test goes red, either
the catalogue is cut wrongly **or two of today's predicates already disagree**, and both are things
to learn before Stage 5 rather than after.

## 5. What it costs

- Stages 1–4 are a refactor with no behaviour change, but a **broad** one: they touch every
  authorisation decision in the system.
- **Stage 5 needs an ADR.** A second source of authority appears beside Keycloak's group
  membership, and `ADR-0017` currently says there should be exactly one. §2's cut reconciles them —
  but it has to be written down, not assumed.
- The dangerous failure is not lock-out (the built-in administrator covers it) but **a switch that
  does nothing**: a console offering a permission the gateway does not yet read. That is
  `FRD-125`'s badge-wearing absent control, and it is why Stage 4 comes before Stage 5.

## 6. Open questions for the owner

1. Is the goal the **engine** (Stage 5) or the **legibility** (Stages 1–3)? They have very
   different costs and the second is a prerequisite either way.
2. Should a custom role be able to hold a *per-use-case* permission set (Stage 6), or is the
   `admin`/`user` pair enough for what use-case administrators actually do?
3. Does anything need to *withdraw* a permission — an explicit deny — or is a union of grants
   enough? Deny rules make a permission model much harder to reason about, and no requirement here
   has yet asked for one.
