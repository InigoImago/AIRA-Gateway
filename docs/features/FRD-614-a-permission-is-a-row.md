# FRD-614 — A permission is a row, not a predicate

> Phase: 7 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner asked whether AIRA could have what enterprise software has: permissions granted
> individually per role, a Global Administrator who holds everything, fine-tuning from there, and
> roles an installation defines for itself. The owner then asked for it to be built. The
> permissions should be collected in one place, turned into an engine that tidies today's rules, and
> exposed in the console. There, an installation can create roles bound to a Keycloak group.
>
> Decided by: [`ADR-0025`](../adr/ADR-0025-aira-defines-what-a-role-may-do.md).
>
> Related: [`ADR-0017`](../adr/ADR-0017-a-role-is-held-through-a-group.md) (a role is held through a
> group, and only through a group), [`ADR-0009`](../adr/ADR-0009-gateway-knows-roles.md) (one shared
> role definition, both planes), [`ADR-0007`](../adr/ADR-0007-security-hardening-baseline.md)
> (visibility never implies the right to act), [`FRD-209`](FRD-209-access-by-group.md) (grants),
> [`FRD-206`](FRD-206-console-truthfulness.md), [`FRD-613`](FRD-613-one-person-one-identity.md),
> [`FRD-622`](FRD-622-platform-administration-and-content-reads.md) (the platform area).

## 1. Why

Authorisation lives in three mechanisms that nothing brings together:

| Mechanism | What | Where |
| --- | --- | --- |
| Three organisation-wide roles | a Python enum, conferred by a configured Keycloak group | `aira_common.roles` |
| A grant per use case | `admin` \| `user`, to a group or a person | `FRD-209` |
| **More than forty hand-written checks** | `may_manage`, `is_oversight`, `visible_scope`, `MayRunTests`, `mayCatalogue`, … | the gateway (about 14), Management (about 30) and the console, in three languages |

The third line is the finding. **No place says who may do what.** Each check picks one of four role
sets or writes a Global Administrator test by hand. The inventory of 2026-09-13 found:

- two helpers that are defined and never called (`Principal.is_governance`, `IsITSteuerung`);
- `docs/ROLES.md` saying a use-case administrator may create a use case, while its own prose and the
  code say only a Global Administrator may;
- demo mode passing some checks (suspensions, providers, the content-read log, `visible_scope`) and
  not others (model diagnostics, content reads);
- role labels written twice by hand in the console.

## 2. Constraints

`ADR-0017`: a role is held through a group, and only through a group. AIRA never writes to the
directory. `ADR-0025` keeps that and adds the cut:

> **Keycloak decides who holds a role. AIRA decides what a role may do.**

Further constraints, each settled elsewhere:

- **The gateway may not ask Management on the request path** (`FRD-204`). Roles are configuration,
  so they travel the way budgets, limits and grants do: Kafka into a read model, cached briefly, and
  refusing rather than admitting when unreadable.
- **Nobody may lock everybody out.** The Global Administrator and IT Security are defined in code
  and never read from the database.
- **Visibility never implies the right to act** (`ADR-0007`). Acting inside every use case is a
  permission of its own.

## 3. The owner's decisions (2026-09-13)

| Question | Decision |
| --- | --- |
| Engine or legibility? | The engine, with custom roles in the console. |
| What may a custom role carry? | **Installation-wide permissions only.** Inside one use case, the grants `admin` and `user` stay as they are. |
| Who manages roles? | **Only a Global Administrator.** IT Security and IT Steuerung see the roles page read-only. Managing roles is never a checkbox. |
| May a custom role hold sensitive permissions? | **Yes, marked as sensitive**, and every change is recorded. |
| How many groups per role? | **Exactly one.** |
| Which roles are fixed? | **Global Administrator and IT Security.** IT Steuerung and every custom role can be changed. |
| Explicit deny? | **No.** Permissions add up over all of a person's roles. |

## 4. The catalogue

`aira_common.permissions.Permission`, read by both planes and, through `/api/v1/me`, the console.
The column on the right is what the built-in roles hold, which is exactly what today's checks allow.
GA is the Global Administrator, SEC is IT Security, STG is IT Steuerung. Stages 1–3 must leave
every cell unchanged.

| Permission | May | Sensitive | GA | SEC | STG (default) |
| --- | --- | :-: | :-: | :-: | :-: |
| `usecase.create` | Create a use case | | ✓ | | |
| `usecase.read_all` | See every use case and its configuration | | ✓ | ✓ | ✓ |
| `usecase.manage_all` | Administer every use case as if it were its administrator, which includes seeing them | ⚠ | ✓ | | |
| `usecase.read_retired` | See retired use cases | | ✓ | | ✓ |
| `usecase.purge` | Purge a retired use case for good | ⚠ | ✓ | | |
| `catalog.write` | Declare, price and release models; read the providers' offerings | ⚠ | ✓ | | |
| `budget.installation.write` | Set the installation's own budget | | ✓ | | |
| `report.read_all` | Every figure: reporting, the register, usage, the installation budget | | ✓ | ✓ | ✓ |
| `trace.read_all` | The request list of every use case (metadata, never content) | | ✓ | ✓ | ✓ |
| `anomaly.read_all` | The security console and the findings of every use case | | ✓ | ✓ | ✓ |
| `anomaly.rule.global.write` | Author anomaly rules that apply everywhere | | ✓ | ✓ | |
| `incident.suspend` | Stop and resume callers, credentials and use cases | ⚠ | ✓ | ✓ | |
| `incident.investigate` | The Requests screen, source addresses, and requests a use case restricts to their makers | ⚠ | ✓ | ✓ | |
| `payload.read_any` | Read the stored content of any use case, which includes reaching the request | ⚠ | ✓ | ✓ | |
| `content_read.read` | The content-read log (`FRD-622`) | | ✓ | ✓ | ✓ |
| `operations.diagnose` | Check whether a model answers; the detail of `/readyz` | | ✓ | ✓ | |
| `smoketest.author` | Write the question catalogue | | ✓ | ✓ | |
| `smoketest.run_any` | Run the catalogue on any use case this person may call | | ✓ | ✓ | |
| `directory.search` | Search people and groups in the directory | | ✓ | | |
| `role.read` | See the roles and what each may do | | ✓ | ✓ | ✓ |
| `role.manage` | Create, change and delete roles. **Reserved**, never offered as a checkbox | ⚠ | ✓ | | |

Two things stay outside the catalogue on purpose:

- **Grants on a use case.** `admin` and `user` are relationships to one use case (`FRD-209`) and
  keep their own checks. A custom role cannot express "administers use case X".
- **The unbound API key and demo mode.** Both are ways of running without an identity, not roles.
  They keep today's behaviour, and the engine does not model them.

## 5. Requirements

- **FR-1 — One decision point.** Every installation-wide decision in the gateway, Management and the
  console asks `allows()` with a `Permission`. No call site names a role, a role set, or the
  literal of a role. Role sets like `OVERSIGHT_ROLES` and helpers like `has_role` for decisions are
  removed.
- **FR-2 — Fixed roles.** `global-admin` holds every permission by construction, `role.manage`
  included. `it-security` holds the set in §4. Both come from code and `AIRA_ROLE_GROUPS` and are
  refused as targets of any change.
- **FR-3 — Stored roles.**
  - `it-steuerung` is stored with the default set in §4. Its permissions can be changed; its group
    comes from `AIRA_ROLE_GROUPS`; it cannot be deleted.
  - A custom role has a slug, a label, exactly one group path and a permission set.
  - A group path is bound to at most one role, built-in groups included.
- **FR-4 — Group check.** Before a group is bound, Management asks Keycloak whether the path
  exists (`group-by-path`, through the read-only directory client).
  - An unknown path is refused with a message naming the path.
  - When the directory cannot be asked, the binding is refused with a message saying so. It is
    never admitted unverified.
  - The console offers the check before saving, and the server checks again on save.
- **FR-5 — Who manages.** Creating, changing and deleting roles needs `role.manage`, which only the
  Global Administrator holds. Reading roles and their change log needs `role.read`.
- **FR-6 — Audit.** Every creation, change and deletion writes a row: who, when, which role, and the
  label, group and permissions before and after. The roles page shows the log.
- **FR-7 — Union.** A person's permissions are the union over every role whose group their token
  carries. There is no deny.
- **FR-8 — The gateway.**
  - Management publishes `role.upserted` and `role.removed` on `aira.roles`. The gateway keeps a
    `roles` read model and resolves a caller's permissions from their token's groups.
  - The read model is cached for at most five seconds. When it cannot be read, stored roles confer
    nothing and the fixed roles are unaffected.
  - A changed permission takes effect without a new token.
- **FR-9 — The console.**
  - `/api/v1/me` returns the caller's `permissions`. `core/auth/roles.ts` becomes a single
    containment check over them.
  - Platform administration gets a second entry, **Roles**:
    - a list of roles, where Global Administrator and IT Security are shown locked;
    - a form with the group path (with a check button), the label, and the permissions as
      checkboxes grouped by area, with sensitive ones marked;
    - the change log, paged at the server one page of 25 at a time with its total; a change
      returns it to the first page, where the new entry is.
- **FR-11 — A permission reaches what it acts on.** A permission that cannot reach its object is a
  checkbox that does nothing, so:
  - `usecase.manage_all` makes every use case visible as well as administrable;
  - `payload.read_any` opens the content of any request by its id, without the request list,
    which stays with `trace.read_all`.

  The converse never holds: seeing never implies acting (`ADR-0007`).
- **FR-10 — Labels in one place.** Role labels and permission labels come from the server, so the
  console never restates them.

## 6. Testing and acceptance

The owner's condition: the engine is finished only when the **full permission matrix** passes.

1. **Equivalence (hermetic, before any behaviour changes).** For every built-in role and every
   permission, the engine answers what today's checks answer. The expected table is written from
   today's code, not from the catalogue, so a wrong cut shows up as a red cell.
2. **Every permission is enforced somewhere.** The matrix tests keep a probe (an endpoint call) per
   permission. A test fails when the catalogue has a permission without a probe, so a new
   permission nobody checks cannot be added.
3. **The Global Administrator holds everything.** Every probe is allowed, in Management and in the
   gateway.
4. **Growing (live stack, real Keycloak group).** A custom role is bound to a group created for the
   test.
   - It starts empty: every probe is refused.
   - Permissions are added one at a time. After each step, the whole matrix runs: exactly the
     granted permissions are allowed, and every other probe is refused. This holds in Management,
     in the gateway after propagation, and in `/me`.
5. **Changes.** After each of these, the whole matrix runs again, and a withdrawal must hold without
   a new token:
   - withdraw permissions and grant them again;
   - rebind the role to another group, so the old group loses everything;
   - delete the role.
6. **Boundaries.**
   - Changes to `global-admin` and `it-security` are refused, through the API and in the console.
   - An unknown group is refused, and so is any group while the directory is unreachable.
   - Only `role.manage` may write roles.
   - Two roles on one person give exactly the union.
7. **Browser.**
   - A Global Administrator creates a role in the console: check the group, name it, tick the
     boxes.
   - A person in that group sees exactly what the role grants.
   - IT Security and IT Steuerung see the page read-only.
8. **Two groups, pair by pair.** The engine forms a union, and what could still escalate is a check
   that combines permissions. `test_the_pairwise_permission_matrix.py`, in Management and in the
   gateway, runs every probe of that plane through the real enforcement for:
   - every grantable permission beside an empty role, and held by both roles;
   - every one of the 190 pairs;
   - each built-in role beside each custom permission;
   - a role beside an `admin` or `user` grant on another use case.

   Each must answer exactly the union, plus the implications FR-11 documents, and `/me` must list
   exactly the union. An `admin` grant additionally opens only the two doors it documents: the
   question catalogue and the directory. The live matrix samples the combinations that need a real
   token: a built-in role beside a custom one, two roles holding one permission, an empty role
   beside a full one, and a role beside a grant. Mutations PW1 and PW2 each derive a permission
   from two others that no built-in role holds together, so only a pair can find them.
9. **Mutation.** Every enforcement point gets a mutation that allows unconditionally. The matrix
   must turn each of them red.

## 7. Staging

| Stage | What | Behaviour change | State |
| --- | --- | --- | --- |
| **1** | The catalogue, the built-in sets, `allows()`, and the equivalence test | none | built |
| **2** | Management asks the engine | none | built |
| **3** | `/me` returns permissions; the console asks them | none | built |
| **4** | The gateway asks the engine | none | built |
| **5** | Roles become data: stored roles, the group check, the audit, `aira.roles`, the gateway read model, the console's Roles page | **the feature** | built |
| **6** | The matrix of §6 on every layer, the mutations, the documents | FR-11, and the source filter refused first | built |

The matrix changed two answers on purpose, both recorded in FR-11, and one by correction:
- a source-address filter from a caller who sees no use case is refused, where it used to be
  answered with an empty list;
- `usecase.manage_all` sees what it administers;
- `payload.read_any` reaches the request whose content it reads.

The built-in roles are unaffected by all three, because they always held the permissions
together. The DEVLOG entry of 2026-09-13 records how each was found.

Stage 4 comes before stage 5, so the console never offers a permission the gateway does not yet
read (`FRD-125`'s badge-wearing absent control).

## 8. What it costs

- Stages 1–4 touch every authorisation decision in the system without changing one.
- The directory client becomes a requirement for binding a custom role (`INTEGRATIONS.md`). The
  development realm gains one, `aira-directory`, with `query-groups` and `view-users`. Management
  asks it at `AIRA_DIRECTORY_URL` where that is set: the issuer names Keycloak as a browser reaches
  it, which inside a container is not Keycloak.
- A permission table that changes at runtime has to agree across two planes. The gateway degrades
  towards refusing.
