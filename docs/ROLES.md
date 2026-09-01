# Roles: who may do what

Every statement on this page is taken from the code that enforces it. Where a rule lives in one
function, that function is named, so a reader can check the claim rather than trust it.

There are **two independent axes**, and almost every misunderstanding about AIRA's permissions comes
from collapsing them:

1. **Organisation-wide roles** — three of them, held **through a Keycloak group**. They say what
   somebody is in the organisation: a global administrator, IT Security, governance.
2. **Grants on a use case** — `admin` or `user`, held per use case. They say what somebody may do
   _inside_ one, and they are given to a person or to a Keycloak group.

An organisation-wide role never grants access inside a use case, and a grant never confers an
organisation-wide power. `ADR-0007` states the rule and `apps/usecases/access.py` implements it:

> Read visibility must never imply the right to act inside a use case.

---

## 1. The three organisation-wide roles

Keycloak is the source of truth. Neither service stores a role decision of its own; both read the
same **`groups`** claim from the same token and resolve it through `AIRA_ROLE_GROUPS`
(`ADR-0009`, `ADR-0017`, `libs/src/aira_common/roles.py`).

> **A Keycloak realm role grants nothing.** Neither plane reads `realm_access.roles`; assigning one
> directly has no effect anywhere, and that inertness is the guarantee `ADR-0017` was written to
> obtain rather than an oversight.

This section said the opposite until 2026-08-31 — *"five of them … both read the same
`realm_access.roles` claim"* — which is, word for word, the paragraph
`libs/src/aira_common/roles.py` opens by naming as a defect it had already corrected **in the
module**. The correction never reached the document a reader opens first, and §5 below sent an
operator to configure realm roles that grant nobody anything, silently. *Correct the definition,
then grep for the comparison* (`LESSONS.md` §1) — the grep, here, is
`tools/tests/test_the_roles_document_names_the_roles_that_exist.py`.

| Role                      | Name in `AIRA_ROLE_GROUPS` | In one sentence                                                          |
| ------------------------- | -------------------------- | ------------------------------------------------------------------------ |
| Global Administrator      | `global-admin`             | Runs the installation. The only role that may catalogue and release models. |
| IT Security               | `it-security`              | Investigates and stops. Sees every use case, may act in an incident, reads content. |
| IT Steuerung (Governance) | `it-steuerung`             | Oversees. Sees every use case and every figure, changes nothing, reads no content. |

There is **no** use-case role. Administering or belonging to a use case is a **grant on that use
case** (`ADR-0017`, `FRD-209`), held by a Keycloak group, not a badge a person carries. Two roles
named `use-case-admin` and `use-case-user` existed until 2026-08-09 and were still listed here for
a day after they stopped doing anything; a role somebody can be given that has no effect reads as a
broken system rather than as a boundary (`FRD-206`).

### The three sets that decide almost everything

These are the predicates the code actually asks. They overlap, and the differences are the point.

| Set                | Members                          | Answers                                                                            |
| ------------------ | -------------------------------- | ---------------------------------------------------------------------------------- |
| `GOVERNANCE_ROLES` | `global-admin`, `it-steuerung`   | May see every **figure** across the installation                                   |
| `OVERSIGHT_ROLES`  | the above **plus** `it-security` | May see every **use case** and its configuration                                   |
| `INCIDENT_ROLES`   | `global-admin`, `it-security`    | May **act** across use cases: stop a caller, author a rule that applies everywhere |

Each split was paid for by a defect:

- **Oversight wider than governance.** IT Security was in neither set and got an _empty_ console.
  A role that sees nothing is not a restricted view, it is an absent one.
- **Incident narrower than oversight.** The gateway's kill switch was guarded by the _visibility_
  predicate, so IT Steuerung could stop traffic in one plane while Management correctly refused it a
  rule in the other. Two planes, one question, two answers.

---

## 2. What each role may do

"Own" means a use case this person holds a grant on. A blank cell means no.

### Use cases

|                                                      | Global Admin | IT Security | IT Steuerung | UC Admin |     UC User      |
| ---------------------------------------------------- | :----------: | :---------: | :----------: | :------: | :--------------: |
| See that a use case exists, and its configuration    |     all      |     all     |     all      |   own    |       own        |
| Create a use case                                    |     yes      |             |              |   yes    |                  |
| Change or delete one                                 |     all      |             |              |   own    |                  |
| Grant and revoke access to one                       |     all      |             |              |   own    |                  |
| Change its pipeline, budgets, rate limits, retention |     all      |             |              |   own    |                  |
| Issue an API key for it                              |     all      |             |              |   own    | own, if a member |

Creating a use case needs `global-admin` — a **narrowing** made by `ADR-0017`, since the old
`use-case-admin` role was organisation-wide and therefore let anyone administering one use case
create another. Everything after that is decided per use case by `may_admin` / `may_manage`, which
read `django-guardian` object permissions — **not** the token. That is why the console asks the server what a caller may do with
each use case instead of deciding it from the role (`FRD-206`).

### Models

|                                           | Global Admin | IT Security | IT Steuerung | UC Admin | UC User |
| ----------------------------------------- | :----------: | :---------: | :----------: | :------: | :-----: |
| See the catalog                           |     yes      |     yes     |     yes      |   yes    |   yes   |
| Add a model to the catalog                |     yes      |             |              |          |         |
| Change or remove a declaration or a price |     yes      |             |              |          |         |
| **Approve a model for use**               |     yes      |             |              |          |         |
| Check whether a model is reachable        |     yes      |     yes     |              |          |         |

**Only a catalogued and approved model may be used at all** (`FRD-307`). A model an upstream offers
but nobody catalogued is refused by name; so is a catalogued model nobody approved. The two refusals
are different sentences because they need different actions — add it, or release it.

### Requests, figures and content

|                                        | Global Admin | IT Security | IT Steuerung | UC Admin | UC User |
| -------------------------------------- | :----------: | :---------: | :----------: | :------: | :-----: |
| Spend and usage reporting              |     all      |     all     |     all      |   own    |   own   |
| Export the report as CSV               |     all      |     all     |     all      |   own    |   own   |
| The request list (traces)              |     all      |     all     |     all      |   own    |  own*   |
| Filter traces by source address        |     yes      |     yes     |              |          |         |
| The cross-use-case **Requests** screen |     yes      |     yes     |              |          |         |
| **Read a stored prompt and response**  |     yes      |     yes     |    **no**    |   own    |  own*   |

\* A use case may be set to show its **users** only the requests they made themselves
(`restrict_members_to_own_requests`). Its administrators always see all of them.

Two rules worth stating plainly:

- **IT Steuerung sees every figure and no content.** Visibility and content are different answers.
- **Every content read is recorded** — who, which request, when, and on what authority — before the
  content is returned (`ADR-0016`). That record is the condition on which the view was granted at
  all.

Reading a payload also requires that the use case stores payloads and that retention has not removed
them. "Storage is off", "it expired" and "this request never reached a model" are three different
answers, never one.

### Security and incidents

|                                           | Global Admin | IT Security | IT Steuerung | UC Admin | UC User |
| ----------------------------------------- | :----------: | :---------: | :----------: | :------: | :-----: |
| The security console                      |     yes      |     yes     |     yes      |          |         |
| See findings                              |     all      |     all     |     all      |   own    |   own   |
| Author a rule that applies **everywhere** |     yes      |     yes     |              |          |         |
| Author a rule for one use case            |     all      |             |              |   own    |         |
| Stop a caller, a credential or a use case |     yes      |     yes     |              |          |         |
| Lift a suspension                         |     yes      |     yes     |              |          |         |

A global rule's effects land on use cases its author cannot otherwise touch, which is why authoring
one is an incident power and reading one is everybody's.

### Operations

|                                                                   | Global Admin | IT Security | IT Steuerung | UC Admin | UC User |
| ----------------------------------------------------------------- | :----------: | :---------: | :----------: | :------: | :-----: |
| `/healthz`, `/readyz` verdict                                     |    public    |   public    |    public    |  public  | public  |
| `/readyz` **detail** (hosts, upstreams, fallbacks, secret source) |     yes      |     yes     |     yes      |   yes    |   yes   |

The verdict is unauthenticated so a load balancer can probe it. The body that names hosts and
upstreams needs a credential: it describes the deployment, and a probe is not a reader.

---

## 3. Grants: what happens inside one use case

A grant binds a **principal** — a person or a Keycloak group — to a use case with a role
(`FRD-209`, `libs/src/aira_common/access.py`).

| Grant role | May                                                                                            |
| ---------- | ---------------------------------------------------------------------------------------------- |
| `user`     | call the gateway attributed to this use case; see it, its figures and its requests             |
| `admin`    | additionally change what happens inside it: access, keys, pipeline, budgets, limits, retention |

Two rules the code enforces so that neither plane has to restate them:

- **Routes are a union.** Somebody who is granted access twice — personally and through a group — is
  granted access.
- **Where two grants differ, the stronger wins.** An access decision that depended on which row was
  read first is not a decision anybody can review.

**AIRA never writes to the directory.** Groups come from Keycloak; AIRA reads them and grants
against them. If the token carries no `groups` claim, no group grant can match — the mapper has to
be configured on every client that reaches AIRA, including service accounts.

---

## 4. How a request is attributed

A caller reaches the gateway with one of two credentials, and the difference matters:

| Credential                             | Carries a use case     | Selector                                                  |
| -------------------------------------- | ---------------------- | --------------------------------------------------------- |
| **API key** (`aira_<prefix>_<secret>`) | yes, bound at issuance | none needed; a mismatched selector is refused             |
| **OIDC bearer token**                  | no                     | `/uc/<slug>` in the path, or the `X-AIRA-Use-Case` header |

For a bearer token, membership comes from the Keycloak group `/use-cases/<slug>` and from grants
distributed to the gateway. A caller who names a use case they are not in is refused — **an empty
membership list means nothing is permitted, not that anything is** (the defect that ended
`ADR-0015`'s security round).

An **unbound** API key is break-glass and deliberately unrestricted. It is the one exception, and it
exists so an installation can be recovered when the control plane is unavailable.

---

## 5. Setting the roles up

**A role is held through a group, and `AIRA_ROLE_GROUPS` is what maps one to the other**
(`ADR-0017`). The dev realm under `deploy/compose/keycloak/realms/` creates the three groups the
shipped default names — `/aira/global-admins`, `/aira/it-security`, `/aira/it-steuerung` — and the
demo users that belong to them:

```
AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung
```

The paths are yours: `/it/security-team` is as good as `/aira/it-security`, and one role may be
conferred by several groups (`it-security=/a/one,/b/two`). The match is **exact** — a sub-group does
not inherit — and a name that is not one of the three roles is refused at start-up rather than
ignored.

Two things the realm has to provide, and both fail silently if they are missing: a
**group-membership mapper** emitting `groups` with **full paths**, on every client that reaches
AIRA, and a group named in `AIRA_ROLE_GROUPS` for `global-admin` — Management refuses to start
outside `local` without one. [`INTEGRATIONS.md`](INTEGRATIONS.md) §2 is the full list.

Management syncs the roles a caller's **groups** confer onto Django groups on every request
(`FRD-201`, `rbac.sync_user_roles`), so the directory remains the only place a role is granted or
removed — and every role is *removed* as well as added, so leaving the group takes effect on the
caller's next token.
