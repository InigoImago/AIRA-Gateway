# Roles: who may do what

Every statement on this page is taken from the code that enforces it. Where a rule lives in one
function, that function is named, so a reader can check the claim rather than trust it.

There are **two independent axes**, and almost every misunderstanding about AIRA's permissions comes
from collapsing them:

1. **Roles across the installation.** A role is a set of **permissions**
   (`aira_common.permissions`), held **through a Keycloak group**. Three roles are built in; an
   installation may define more.
2. **Grants on a use case** — `admin` or `user`, held per use case. They say what somebody may do
   _inside_ one, and they are given to a person or to a Keycloak group.

A role never grants access inside a use case by being a role, and a grant never confers a
permission across the installation. The rule behind both:

> Read visibility must never imply the right to act inside a use case.

Administering every use case is a permission of its own, `usecase.manage_all`. The Global
Administrator holds it by construction; another role holds it only if somebody gives it that
permission. It acts in **Management** — the console and the control-plane API. In the gateway it
opens nothing by itself (§2).

**Keycloak decides who holds a role; AIRA decides what a role may do.** Every check in
the gateway, in Management and in the console asks one function, `allows()`, for one permission.
None of them asks for a role.

---

## 1. The built-in roles

Group membership decides who holds a role. Both services read the same **`groups`** claim from the
same token and resolve it through `AIRA_ROLE_GROUPS`.

> **A Keycloak realm role grants nothing.** Neither plane reads `realm_access.roles`, so assigning
> one directly has no effect anywhere.

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

A group confers at most one defined role: Management refuses a group that already confers a role,
built-in or defined. (`AIRA_ROLE_GROUPS` itself does not check this — the same path given for two
built-in roles confers both.) IT Security and IT Steuerung see the roles and their change log and
change nothing. Every creation, change and deletion is recorded with who made it, when, and the
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
| `usecase.manage_all`        | Administer every use case in Management as if it were its administrator, which includes seeing them; nothing in the gateway | ⚠ | ✓ |   |              |
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
- **`usecase.manage_all` is Management's.** Its holder administers every use case in the console
  and through the control-plane API. In the gateway it opens nothing by itself: calling a use case,
  seeing its requests and reading its content still take a grant, or the oversight permissions
  above. Its holder can give themselves that grant like any administrator, and it is then
  recorded like any other — issuing themselves a key makes them a member of the use case.
- **Every content read is recorded**: who, which request, when, on what ground and with which
  roles, before the content is returned. The log is under **Platform → Content reads**, for
  `content_read.read`. Reading also needs the use case to store content and
  retention not to have removed it.
- **The register of processing activities** — the console's **Register** screen — is shown to
  `report.read_all`. The endpoint behind it, `GET /v1beta/register`, answers anybody else for the
  use cases they are in.
- **Only a catalogued and approved model may be used**, and only `catalog.write` changes the
  catalogue.
- **A global anomaly rule** lands on use cases its author cannot otherwise touch, which is why
  writing one is a permission and reading one is everybody's.

---

## 3. Grants: what happens inside one use case

A grant binds a **principal** — a person or a Keycloak group — to a use case with a role
(`libs/src/aira_common/access.py`).

| Grant role | May                                                                                            |
| ---------- | ---------------------------------------------------------------------------------------------- |
| `user`     | call the gateway attributed to this use case; see it, its requests, its figures, its findings (**Warnings**) and its anomaly rules (**Rules**), as far as its `payload_visibility` allows; issue an API key for itself |
| `admin`    | additionally change what happens inside it: members and group grants, API keys, the models released to it, pipeline, budgets, rate limits, its own anomaly rules, storage, visibility and retention; run the question catalogue on it; search the directory for people and groups to add; and retire it |

Two rules the code enforces so that neither plane has to restate them:

- **Routes are a union.** Somebody who is granted access twice — personally and through a group — is
  granted access.
- **Where two grants differ, the stronger wins.** An access decision that depended on which row was
  read first is not a decision anybody can review.

**What a use case shows its own people** is one setting, `payload_visibility`:

| Value               | Requests listed                        | Stored content                          | Figures split by person                 |
| ------------------- | -------------------------------------- | --------------------------------------- | --------------------------------------- |
| `members` (default) | everybody's, to everybody in it        | everybody in it                         | everybody's, to everybody in it         |
| `own`               | users their own, administrators all    | users their own, administrators all     | users their own, administrators all     |
| `oversight`         | everybody's, to everybody in it        | **nobody in it**, administrators too    | each person only their own              |

Totals and every other breakdown stay visible in every mode. `oversight` is the one case where an
administrator of a use case cannot read its prompts. Outside the use case,
content is read by whoever holds `payload.read_any` — IT Security and the Global Administrator, and
any role given it — and figures by whoever holds `report.read_all`, which no value narrows.

**AIRA never writes to the directory.** Groups come from Keycloak; AIRA reads them and grants
against them. If the token carries no `groups` claim, no group grant can match — the mapper has to
be configured on every client that reaches AIRA, including service accounts.

**A group grant is checked for shape, not existence — by design.** It may name a group
Keycloak does not have yet, so onboarding a department does not have to happen in two systems in a
fixed order. In the console the group is picked from the directory search, so a typo cannot be
entered there; through the API, a mistyped path makes a grant that reaches nobody, and the grant
list shows `reaches: 0` — a count of the people Management has seen sign in with that group, not
the group's size. Only a **role**'s group is checked against Keycloak, and binding one is refused
while the directory cannot be asked.

---

## 4. How a request is attributed

A caller reaches the gateway with one of two credentials, and the difference matters:

| Credential                             | Carries a use case     | Selector                                                  |
| -------------------------------------- | ---------------------- | --------------------------------------------------------- |
| **API key** (`aira_<prefix>_<secret>`) | yes, bound at issuance | none needed; a mismatched selector is refused             |
| **OIDC bearer token**                  | no                     | `/uc/<slug>` in the path, or the `X-AIRA-Use-Case` header (the header wins) |

On the Gemini surface a model call with a bearer token always names its use case: naming none is a
`400`. The KIRA surface, whose contract has no field for one, attributes a caller who belongs to
exactly one use case to it and refuses one who belongs to several with a `403` naming them
(`gateway/src/aira_gateway/api/kira/attribution.py`).

For a bearer token, membership comes from the Keycloak group `/use-cases/<slug>` and from grants
distributed to the gateway. A caller who names a use case they are not in is refused — **an empty
membership list means nothing is permitted, not that anything is**. No role bypasses this: seeing
every use case is not calling one. The use case must also be one the gateway holds and has not
retired: one whose configuration has not arrived from Management yet, a group `/use-cases/<slug>`
naming one Management does not have, and a retired one are all refused with a `403`.

With several Keycloak issuers (`AIRA_OIDC_ISSUERS`, read by the gateway only), a group path from any
of them confers the same role and the same use cases: the realms are trusted as one population.
Management, and so the console, reads one issuer.

An **unbound** API key is break-glass and deliberately unrestricted. It is the one exception, and it
exists so an installation can be recovered when the control plane is unavailable.

---

## 5. Setting the roles up

**The built-in roles** are held through the groups `AIRA_ROLE_GROUPS` names. **Its default is
empty** in both planes' settings, and then nobody holds a built-in role.
`deploy/compose/docker-compose.apps.yml`, `deploy/compose/.env.example` and the showcase and
standalone examples under `config/` set the three groups that the dev realm under
`deploy/compose/keycloak/realms/` creates and puts the demo users in:

```
AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung
```

A process started from source reads its own environment and a `.env` in its working directory, not
Compose's, so it needs the value too — or the demo accounts sign in holding no role.

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
- The gateway learns it over Kafka, on `aira.roles` (`aira.<stage>.roles` when `AIRA_KAFKA_STAGE`
  is set), and three steps stand between: the outbox relay publishes the change on its next run —
  every 10 seconds in the Compose stack (`AIRA_RELAY_INTERVAL`); elsewhere on whatever schedule the
  relay is given, since it publishes once and exits — the gateway's consumer applies it, and the
  gateway caches its role table for five seconds. A withdrawn permission therefore holds within a
  relay interval and a few seconds, without a new login.
- A change of **group membership** in Keycloak takes effect with the caller's next token, as every
  group change does.
- If the gateway cannot read its role table, stored roles confer nothing and the two fixed roles are
  unaffected.
