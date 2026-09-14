# Roles: who may do what

Every statement on this page is taken from the code that enforces it. Where a rule lives in one
function, that function is named, so a reader can check the claim rather than trust it.

There are **two independent axes**, and almost every misunderstanding about AIRA's permissions comes
from collapsing them:

1. **Roles across the installation.** A role is a set of **permissions**
   (`aira_common.permissions`, `FRD-614`), held **through a Keycloak group**. Three roles are built
   in; an installation may define more.
2. **Grants on a use case** — `admin` or `user`, held per use case. They say what somebody may do
   _inside_ one, and they are given to a person or to a Keycloak group.

A role never grants access inside a use case by being a role, and a grant never confers a
permission across the installation. `ADR-0007` states the rule:

> Read visibility must never imply the right to act inside a use case.

Acting inside every use case is a permission of its own, `usecase.manage_all`, which a role holds
only if somebody gave it that permission.

**Keycloak decides who holds a role; AIRA decides what a role may do** (`ADR-0025`). Every check in
the gateway, in Management and in the console asks one function, `allows()`, for one permission.
None of them asks for a role.

---

## 1. The built-in roles

Group membership decides who holds a role. Both services read the same **`groups`** claim from the
same token and resolve it through `AIRA_ROLE_GROUPS` (`ADR-0017`).

> **A Keycloak realm role grants nothing.** Neither plane reads `realm_access.roles`, so assigning
> one directly has no effect anywhere. `tools/tests/test_the_roles_document_names_the_roles_that_exist.py`
> holds this page to that.

| Role                      | Name in `AIRA_ROLE_GROUPS` | Can it be changed?                        | In one sentence                                                    |
| ------------------------- | -------------------------- | ----------------------------------------- | ------------------------------------------------------------------ |
| Global Administrator      | `global-admin`             | **No.** Holds every permission, always.   | Runs the installation, and the only one who manages roles.         |
| IT Security               | `it-security`              | **No.** A fixed set.                      | Investigates and stops. Sees every use case, reads content.        |
| IT Steuerung (Governance) | `it-steuerung`             | Its permissions, not its name or group.   | Oversees. Sees every use case and every figure, reads no content.  |

The two fixed roles are defined in code and never read from a database, so a broken or empty role
table cannot lock anybody out. The Global Administrator holds every permission **by construction**,
including one added in a later release.

### Roles an installation defines

In the console under **Platform → Roles**, a Global Administrator creates a role with:

- **exactly one Keycloak group**, which Management checks exists before it is bound. An unknown
  group is refused, and so is any group while the directory cannot be asked;
- **a name**;
- **its permissions**, as checkboxes grouped by area. The sensitive ones are marked.

A group confers at most one role. IT Security and IT Steuerung see the roles and their change log
and change nothing. Every creation, change and deletion is recorded with who made it, when, and the
role before and after.

**Permissions add up.** Somebody in two groups holds everything both roles give. There is no
explicit deny.

---

## 2. What each role may do

The catalogue is `aira_common.permissions.Permission`. The columns on the right are what the
built-in roles hold; IT Steuerung's column is its default, which an installation may change.
⚠ marks a sensitive permission.

| Permission                  | May                                                                           |     | Global Admin | IT Security | IT Steuerung |
| --------------------------- | ----------------------------------------------------------------------------- | :-: | :----------: | :---------: | :----------: |
| `usecase.create`            | Create a use case                                                             |     |      ✓       |             |              |
| `usecase.read_all`          | See every use case and its configuration                                      |     |      ✓       |      ✓      |      ✓       |
| `usecase.manage_all`        | Administer every use case as if it were its administrator, which includes seeing them | ⚠ | ✓ |   |              |
| `usecase.read_retired`      | See retired use cases                                                         |     |      ✓       |             |      ✓       |
| `usecase.purge`             | Purge a retired use case for good                                             |  ⚠  |      ✓       |             |              |
| `catalog.write`             | Declare, price and release models; read the providers' offerings              |  ⚠  |      ✓       |             |              |
| `report.read_all`           | Every figure: reporting, the register, usage, the installation budget         |     |      ✓       |      ✓      |      ✓       |
| `budget.installation.write` | Set the installation's own budget                                             |     |      ✓       |             |              |
| `trace.read_all`            | The request list of every use case (metadata, never content)                  |     |      ✓       |      ✓      |      ✓       |
| `content_read.read`         | The log of who read stored content                                            |     |      ✓       |      ✓      |      ✓       |
| `payload.read_any`          | Read the stored content of any use case, reaching the request by its id       |  ⚠  |      ✓       |      ✓      |              |
| `anomaly.read_all`          | The security console and the findings of every use case                       |     |      ✓       |      ✓      |      ✓       |
| `anomaly.rule.global.write` | Author anomaly rules that apply everywhere                                    |     |      ✓       |      ✓      |              |
| `incident.suspend`          | Stop and resume callers, credentials and use cases                            |  ⚠  |      ✓       |      ✓      |              |
| `incident.investigate`      | The Requests screen, source addresses, and requests a use case restricts to their makers | ⚠ | ✓ | ✓ |        |
| `operations.diagnose`       | Check whether a model answers; the detail of the readiness probe              |     |      ✓       |      ✓      |              |
| `smoketest.author`          | Write the question catalogue                                                  |     |      ✓       |      ✓      |              |
| `smoketest.run_any`         | Run the question catalogue on any use case this person may call               |     |      ✓       |      ✓      |              |
| `directory.search`          | Search people and groups in the directory                                     |     |      ✓       |             |              |
| `role.read`                 | See the roles and what each may do                                            |     |      ✓       |      ✓      |      ✓       |
| `role.manage`               | Create, change and delete roles — **reserved**, never a checkbox              |  ⚠  |      ✓       |             |              |

Some rules that follow from the table:

- **IT Steuerung sees every figure and no content.** Visibility and content are different
  permissions.
- **Every content read is recorded**: who, which request, when, on what ground and with which
  roles, before the content is returned (`ADR-0016`, `FRD-622`). Reading also needs the use case to
  store content and retention not to have removed it.
- **Only a catalogued and approved model may be used** (`FRD-307`), and only `catalog.write` changes
  the catalogue.
- **A global anomaly rule** lands on use cases its author cannot otherwise touch, which is why
  writing one is a permission and reading one is everybody's.

---

## 3. Grants: what happens inside one use case

A grant binds a **principal** — a person or a Keycloak group — to a use case with a role
(`FRD-209`, `libs/src/aira_common/access.py`).

| Grant role | May                                                                                            |
| ---------- | ---------------------------------------------------------------------------------------------- |
| `user`     | call the gateway attributed to this use case; see it, its figures and its requests; issue an API key for itself |
| `admin`    | additionally change what happens inside it: access, keys, pipeline, budgets, limits, retention, and retire it |

Two rules the code enforces so that neither plane has to restate them:

- **Routes are a union.** Somebody who is granted access twice — personally and through a group — is
  granted access.
- **Where two grants differ, the stronger wins.** An access decision that depended on which row was
  read first is not a decision anybody can review.

A use case may show its **users** only the requests they made themselves
(`restrict_members_to_own_requests`); its administrators always see all of them.

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
membership list means nothing is permitted, not that anything is**. No role bypasses this: seeing
every use case is not calling one.

An **unbound** API key is break-glass and deliberately unrestricted. It is the one exception, and it
exists so an installation can be recovered when the control plane is unavailable.

---

## 5. Setting the roles up

**The built-in roles** are held through the groups `AIRA_ROLE_GROUPS` names. The dev realm under
`deploy/compose/keycloak/realms/` creates the three groups the shipped default names and the demo
users that belong to them:

```
AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung
```

The paths are yours, one role may be conferred by several groups (`it-security=/a/one,/b/two`), the
match is **exact** — a sub-group does not inherit — and a name that is not one of the three is
refused at start-up rather than ignored. Management refuses to start outside `local` without a
group for `global-admin`.

**Custom roles** need the directory client (`AIRA_DIRECTORY_CLIENT_ID`,
`AIRA_DIRECTORY_CLIENT_SECRET`, and `AIRA_DIRECTORY_URL` where Management reaches Keycloak by
another address). Without it no group can be checked, so none is bound. [`INTEGRATIONS.md`](INTEGRATIONS.md)
§2 is the full list.

**How fast a change takes effect.**

- Management applies a role change on the next request.
- The gateway learns it over `aira.roles` and caches its role table for five seconds. A withdrawn
  permission therefore holds within seconds, without a new login.
- A change of **group membership** in Keycloak takes effect with the caller's next token, as every
  group change does.
- If the gateway cannot read its role table, stored roles confer nothing and the two fixed roles are
  unaffected.
