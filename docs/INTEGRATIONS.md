# Integrating with your infrastructure

What each connected system must provide, which credentials AIRA needs, and which settings on **your**
side matter. One section per system; each ends with a short checklist.

Configuration values are named here and defined in [`CONFIGURATION.md`](CONFIGURATION.md). How to
run the processes is [`deployment/`](deployment/README.md) — against your own infrastructure,
[`deployment/integrated.md`](deployment/integrated.md).

```mermaid
graph LR
    subgraph required["Required"]
        pg[("PostgreSQL 14+")]
        kafka[["Kafka"]]
        kc["Keycloak / OIDC"]
        model["≥1 model platform"]
    end
    subgraph recommended["Strongly recommended"]
        redis[("Redis")]
        otel["OTLP collector"]
    end
    subgraph optional["Optional"]
        vault["Vault"]
    end
    aira["AIRA Gateway"] --> required
    aira --> recommended
    aira --> optional
```

---

## 0. When it does not work: watching the wire

Read this before section 1, because it is what turns the rest of this document into a loop you can
close. Every system below is reached over a network, with credentials, and every one of them fails
silently in at least one of its modes. `AIRA_DEBUG_INTEGRATIONS` makes each call say what it did:

```bash
AIRA_DEBUG_INTEGRATIONS=all      # while you are wiring something up
AIRA_DEBUG_INTEGRATIONS=kafka    # or just the one you are working on
AIRA_DEBUG_INTEGRATIONS=         # off — and off is what a working installation runs
```

One line per call, on stdout with the rest of the service log — what went where, how long it took,
and the far end's own words when it failed:

```json
{"system": "kafka", "operation": "producer.start", "outcome": "failed", "duration_ms": 64.5,
 "target": "broker-a:9093", "protocol": "SASL_SSL", "error_type": "KafkaConnectionError",
 "error": "Unable to bootstrap from [('broker-a', 9093, AddressFamily.AF_INET)]",
 "event": "integration_call", "level": "warning"}
```

`outcome` separates **`timeout`** from **`failed`** deliberately: a refusal is a wrong address, port
or credential and is fixed in configuration; a timeout is a firewall, a hung process or a route that
goes nowhere and is fixed by looking at the network. No line carries a payload, a token or a secret
value, and addresses are redacted in both places a credential hides in one.

Names: `otel`, `kafka`, `auth`, `vault`, `redis`, `postgres`, or `all`. A name this build does not
know refuses the process at start-up rather than watching nothing. Full reference:
[`CONFIGURATION.md` §1a](CONFIGURATION.md#1a-watching-a-call-to-another-system).

For the leg **after** the collector — what your OTLP collector managed to forward onward — the
counters are in `make otel-status` (§6).

---

## 1. PostgreSQL

**Two databases**, one per plane. They are not interchangeable.

| Database | Owner | Holds |
|---|---|---|
| `aira_mgmt` | Management | configuration: use cases, memberships and group grants, hashed API keys, pipelines, budgets, limits, rules, roles, model catalog, outbox |
| `aira_gateway` | Gateway | read-model of the above, plus `request_logs`, `payload_access` (every read of stored content), `anomaly_events`, `access_suspensions`, `budget_usage`, `retention_runs` |

**What you provide**

- PostgreSQL **14 or newer** — Django's floor, which Management checks when it connects. The
  Compose stack runs 17, the only version CI tests.
- **One role per database, owning it** — or holding `CREATE` and `USAGE` on its schema `public`.
  The migrations create their tables in `public`, and from PostgreSQL 15 on, `CREATE` on the
  *database* allows new schemas only: `public` belongs to the database's owner and is not writable
  by others. Run every migration as the same role, because a later one alters tables an earlier one
  created; the services connect as that role as well.
- No extensions beyond the defaults.
- **Direct connections, or session pooling** — not transaction pooling (PgBouncer's transaction
  mode). The relay holds a session-level advisory lock for its run, so that two runs never publish
  out of order; through a transaction pooler the lock can stay behind on one connection or let two
  runs overlap. The gateway lets psycopg prepare a statement on its fifth run, which a transaction
  pooler hands to connections that never prepared it.

**Sizing.** `request_logs` is the one table that grows with traffic: one row per request, plus one
per pipeline model call. Payloads (`request_payload`, `response_payload`) dominate its size and are
deleted on the retention clock — per use case, `AIRA_DEFAULT_RETENTION_DAYS` (7) by default; the
metadata is kept, because that is what the spend history is made of. If you set
`AIRA_LOG_RETENTION_DAYS`, whole rows go and you are also truncating the reporting horizon — the
default of `0` (never) is deliberate.

**Migrations** are Alembic (gateway) and Django (Management), run as one-shot jobs **before** the
new code starts. Nothing else creates tables, and the gateway refuses `create_all` on anything but
SQLite: the migrations own the schema, and an old container's `create_all` can bring back a table a
migration dropped, which every event then fails against.

> done two databases, each owned by the role that migrates it · migrations as a pre-deploy job ·
> no transaction pooling · a plan for `request_logs` growth

---

## 2. Keycloak (or another OIDC provider)

AIRA needs **one realm** — the gateway can trust several, the console reads one (below) — three
role groups, whatever groups your organisation already uses, a public client for the console, one
confidential client per machine caller, and optionally one for directory lookups.

How the pieces fit — signing in, API keys, what the gateway checks, the directory lookups — is drawn
in [`ARCHITECTURE.md`](ARCHITECTURE.md) §8.

### Roles come from groups

**Group membership is the only source of a role.** Three groups carry the organisation-wide roles,
and you tell AIRA which is which:

```
AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung
```

| Role | Holds by default | Changeable |
|---|---|---|
| `global-admin` | every permission, and alone the one that defines roles (`role.manage`) | no |
| `it-security` | sees every use case, figure, request and anomaly; authors global anomaly rules; stops traffic and investigates; reads any stored content; runs the question catalogue | no |
| `it-steuerung` | sees every use case, retired ones included, every figure, request and anomaly, and the record of content reads; **changes nothing** | its permissions |

The rest is a matter of what a role holds, not of which role it is: creating a use case
or pricing a model can be granted to IT Steuerung, or to a role an installation defines; only
`role.manage` stays with the Global Administrator. A role an installation defines is bound to
**one** group in the console, not through `AIRA_ROLE_GROUPS`, and that group must exist in
Keycloak. Who may do what, permission by permission, is [`ROLES.md`](ROLES.md).

**Administering every use case is not calling one.** `usecase.manage_all`, a Global
Administrator's by default, lets its holder administer any use case in Management, and grants
nothing in the gateway: calling a use case, or seeing its requests there, takes a grant on it — or,
for the requests, `trace.read_all`, a permission of its own.

The paths are yours — `/it/security-team` is as good as `/aira/it-security`, and several groups may
confer one role (`it-security=/a/one,/b/two`). The match is **exact**: a sub-group does not inherit,
and `/aira/global-admins-readonly` confers nothing.

> **A Keycloak realm role grants nothing.** AIRA does not read `realm_access.roles`. Assigning
> `global-admin` directly to a person has no effect — which is the point: there is exactly one place
> a role is granted, so there is exactly one place to audit and one place to revoke.

Outside `local`, Management **refuses to start** when no group is named for `global-admin`: an
installation nobody can administer cannot be repaired through its own console.

**There is no `use-case-admin` or `use-case-user` role.** Administering or belonging to a use case
is a grant on *that* use case — whoever holds `usecase.create` creates it and becomes its first
administrator; its administrators grant it to groups and to people, as `user` or `admin`. See the
next section.

### Groups — who reaches which use case

**Your group names are yours.** A use case's access list names whatever paths your realm actually
uses — `/abteilungen/vertrieb/nord`, `/ai/kundenservice` — and AIRA grants them a role
(`user` or `admin`) in the console. Somebody joining or leaving the group takes effect on their
next token, without anybody editing a list here.

A naming convention also still works, and needs no grant at all:

```
/use-cases/<slug>
```

A user in `/use-cases/kundenservice` reaches that use case as a `user` by the name alone — the last
segment of the path is the slug, so `/use-cases/kundenservice/team-a` names `team-a`. It is one
route of **three**, and they are a **union** — where the roles differ, the stronger wins:

1. this convention, resolvable from the token by itself;
2. a grant to **any group path your realm already uses** — `/abteilungen/vertrieb/nord`, whatever
   it is. AIRA imposes no naming convention on somebody else's directory;
3. a grant naming **one person**, for the case where no group fits.

Every route reaches only a use case that exists: one the gateway holds no configuration for is
refused with `403` *not known to this gateway*, the convention included, until Management has
created it and its event has arrived. You do not need a group per use case. Route 1
exists because it needs no grant and is a perfectly good way to run a small installation.

**A group grant is checked for its shape, not for its existence** — by design: a
department can be granted before Keycloak has its group, so onboarding it is not a two-system
dance. The path must be a full one, starting with `/`. In the console, with the directory client
below, a group is picked from the directory, so a typo cannot be entered there; through the API, a
mistyped path is a grant that reaches nobody. The grant list says so: `reaches` counts the accounts
whose last token to Management carried that exact path, and `0` is either a typo or a group nobody
in it has yet signed in to the console with. Only binding a **role** to a group asks Keycloak, and
it is refused while Keycloak cannot be asked.

**AIRA never writes to your directory.** It does not create groups, add people to them or delete
them. Group membership stays your system's answer.

#### The one setting this depends on

> **The access token must carry a `groups` claim, with full paths.**

Without it a group grant reaches nobody — silently, because a token with no groups is
indistinguishable from a token whose owner is in none. Add a **group-membership** protocol mapper
to every client whose tokens AIRA sees, including service accounts:

| Field | Value |
|---|---|
| Mapper type | Group Membership |
| Token Claim Name | `groups` |
| Full group path | **on** |
| Add to access token | on |

The dev realm has it on the SPA client and on the test service accounts. A realm that has it on
only some of them produces a feature that works for some callers and not others, which is the
hardest shape of all to diagnose.

A path is matched **exactly**, as a string. A value without the leading `/` is not refused — the
token still authenticates — and matches nothing.

#### And an audience claim, which Keycloak does not add by itself

> **The access token's `aud` must carry whatever you set as `AIRA_OIDC_AUDIENCE`.**

Outside `AIRA_ENVIRONMENT=local` the gateway and Management both **refuse to start** without an
audience named: without one, any token the realm ever minted is accepted, including one issued to a
different client. Management checks whenever an issuer is set; the gateway when
`AIRA_OIDC_ENABLED=true` — which it needs to accept a token at all, and whose default is off — and
not under `AIRA_DEMO_MODE`. But a Keycloak access token for a public client carries
`aud: ["account"]` and **not** the client id, so the value has to be put there deliberately:

| Field | Value |
|---|---|
| Mapper type | Audience |
| Included Client Audience | the value you will configure as `AIRA_OIDC_AUDIENCE` |
| Add to access token | on |

A client scope does the same job and is the better place for it when several clients need it.

**The check at start-up is on the setting, not on the realm.** With `AIRA_OIDC_AUDIENCE` set and
no mapper, both services start, and every token is rejected with `401` against a realm that looks
entirely correct. The dev realm has the mapper (`aira-audience`, on the console's client and the
test service accounts), but the local stack leaves `AIRA_OIDC_AUDIENCE` empty, so `aud` is not
compared there: a realm without the mapper looks fine from inside this stack and fails on somebody
else's Keycloak.

#### The issuer is compared, not called — the keys come from the JWKS URI

> **`AIRA_OIDC_ISSUER` is the address browsers use, and it must equal the tokens' `iss` exactly.**

The console sends the browser to the issuer, and the services compare it with each token's `iss` as
a string — a trailing slash included. No service calls it. Both planes fetch the signing keys from
`AIRA_OIDC_JWKS_URI`, which defaults to Keycloak's `<issuer>/protocol/openid-connect/certs`, and
Management asks the directory at `AIRA_DIRECTORY_URL`, which defaults to the issuer without its
`/realms/<name>`. Where the services reach Keycloak by another name than browsers do — inside a
cluster, usually — set those two and leave the issuer as the browser sees it.

**HTTPS, outside `local`.** Both planes refuse to start while the issuer or the JWKS URI is
`http://` to anything but a loopback address, and so does `VAULT_ADDR` ([§4a](#4a-hashicorp-vault)):
over plain HTTP, anything on the path can substitute the signing keys and mint tokens that verify.
The gateway waives this under `AIRA_DEMO_MODE`; Management does not.

**What a token must be**, from Keycloak or any other provider:

- signed with **RS256**, by a key the JWKS lists;
- carrying `exp`, `iat`, `sub` and `iss`, and `aud` where an audience is configured;
- issued at most `AIRA_OIDC_CLOCK_SKEW_SECONDS` (60) in the future, and used until `exp` plus
  `AIRA_OIDC_EXPIRY_LEEWAY_SECONDS` (0);
- at most 16 KiB — a longer credential is refused with `401` before it is parsed.

A provider other than Keycloak signs people in on those terms, with `AIRA_OIDC_JWKS_URI` set and a
`groups` claim of full paths. What it does not get is the directory: those lookups use Keycloak's
admin API, with the realm read from `/realms/<name>` in the issuer — so no directory search, no
role bound to a group, and nobody granted by name before they have signed in once.

#### Several realms — the gateway only

`AIRA_OIDC_ISSUERS` lets the gateway accept tokens from more than one realm — a migration, a second
instance, a merger:

```
AIRA_OIDC_ISSUERS=https://sso.example.com/realms/aira|aira-gateway;https://sso.example.com/realms/partner|aira-gateway
```

One `issuer|audience|jwks_uri` per entry, `;`-separated, the JWKS URI optional. Set, it
**replaces** `AIRA_OIDC_ISSUER`, `_AUDIENCE` and `_JWKS_URI` for the gateway, so the console's realm
must be one of the entries. A token is routed by its own `iss` — one that names no entry is refused,
and no key set is fetched for it — and then verified against that realm; outside `local`, every
entry needs an audience. The realms are **trusted equally**: one `AIRA_ROLE_GROUPS`, one set of
grants, and the same group path or username from either grants the same, which is sound only while
they describe one population. The audit row records the issuer a token came from. Management and
the console read one issuer, `AIRA_OIDC_ISSUER`: people from the other realms call the gateway and
do not sign in to the console.

#### And one that decides whose allowance is whose

> **`preferred_username` should be in the access token** — Keycloak puts it there by default.

A per-head budget or rate limit is counted against the **person**, so that an API key and a browser
session by the same human share one allowance rather than getting two. The name in the token is what
joins them: an API key's identity already *is* its owner's username, and a token's subject is a
directory id that resembles nothing.

Without the claim nothing breaks and nothing is silently shared: that caller's allowance keys on
their subject, which is stable and unique — they simply get a second one for their key. A Keycloak
service account carries the claim as well, `service-account-<client id>`: that is the name its
allowance counts against, its account in Management is called, and a direct grant to it names.

#### The username has to be one Keycloak does not let people change

> **Leave *Edit username* off in the realm** (Realm settings → Login). It is off by default.

This is the one assumption AIRA makes about your directory that it cannot check for you, so it is
written down rather than left implied. The name in the token is not only a label:

| What it decides | Decided by |
| --- | --- |
| which use cases a caller reaches, where a grant names a **person** rather than a group | the gateway, on every request |
| whose per-head budget and rate limit the request counts against | the gateway, on every request |
| whether the payload view treats somebody as an administrator of their use case | the gateway, when stored content is read |
| whether a **kill switch** aimed at a person stops both of their credentials | the gateway, on every request |
| which invited Django account a new `sub` claims on first sign-in | Management, at that sign-in |

With *Edit username* on, a user can rename themselves to a colleague's name and inherit each of
those. Everything else AIRA reads from a token — the subject, the groups — is Keycloak's to set and
nobody else's, and the username is the exception because a realm can be configured to hand it to
the user.

AIRA does not check the setting: reading it needs the optional admin client below, so a check
would pass silently on every installation that has not configured one — a control that is present
on paper and absent in practice, which this project refuses to ship. If your realm must allow
username editing, grant access by **group** only and expect one person's allowances to follow the
name rather than the human.

#### Leaving a group does not end an API key

> **When somebody leaves, revoke their API keys — or let them expire.**

Removing a person from a group in your directory ends their **token's** access when the access
token they hold expires — five minutes, by Keycloak's default. The services verify a token
themselves and ask Keycloak nothing, so a token already issued keeps its groups until then; with an
Active Directory federation, Keycloak's cache decides when a *new* token stops carrying the group
([below](#an-active-directory-federation)).

It ends nothing for an **API key** they own through that group: a key is revoked when the access it
rests on ends *in AIRA* — a member removed, a group grant revoked — and Management
knows a person's groups only from that person's own tokens, which somebody who has left no longer
presents. Their key serves until it expires: `AIRA_API_KEY_DEFAULT_DAYS` (30) unless a longer
lifetime up to `AIRA_API_KEY_MAX_DAYS` (180) was asked for. A shorter default narrows the window for
whoever forgets.

#### A username handed to somebody new

A direct membership travels to the gateway as a **username**, matched there against
`preferred_username`. If somebody signs in under a name an account here already holds, their
account is created as `<name>-<first eight characters of their subject>`, and the console refuses
a direct grant by either name — the gateway would read it as the other person — and says to grant
through a group instead. Remove a departed person's direct memberships before their username is
given to somebody else.

#### To stop a person, name them by their username

> **A suspension targeting a `subject` must carry the name, not the directory id.**

An API key never carries a `sub` — Management issues it against a *username*, so that is the only
identity on the wire — while a browser session carries both. A suspension typed as a directory id
therefore stops that person's **tokens** and serves their key, which is a kill switch that appears
to work. Typed as the username it stops both.

The trace list shows the name in its own column beside the subject, which is the value to copy.
This is not something the gateway can close for you: joining a directory id to a username needs a
question put to the directory, and the gateway never asks the directory while it serves a request —
it decides from its own copy of the configuration, which Management sends it over Kafka.

#### Asking your directory (needed for custom roles)

To let the console **search** your groups and users when granting access, and to **check that a
group exists before a role is bound to it**, give AIRA a read-only service account and
set `AIRA_DIRECTORY_CLIENT_ID` / `AIRA_DIRECTORY_CLIENT_SECRET`. It needs `view-users` and
`query-groups` on the realm — nothing else, and it is used for nothing but these lookups, which are
Keycloak's admin API. Where Management reaches Keycloak by another address than the one in
`AIRA_OIDC_ISSUER` (inside a container, usually), set `AIRA_DIRECTORY_URL` to it.

**Binding a role to a group is refused without it.** A group nobody could check is not trusted: the
console says the group could not be verified, and the three built-in roles keep working. The
development realm ships such a client, `aira-directory`, and the compose stack uses it by default.

Without it the console still works, with one real limit. It offers the people who have signed in
and the groups already granted somewhere, and **says that is what it is showing**; it cannot invent
a group nobody has used, so on a fresh installation the first grant of a new group has to be typed.

> **And granting a *person* who has never signed in needs this client.** AIRA creates their account
> — with an invitation their first token claims — only for a username the directory
> confirms, because an account created for a name nobody has is an accountability chain ending in
> a string. Without the client the console says so and names both ways out: configure it, or have
> the colleague sign in once, which creates the account. Granting a **group** needs none of this
> and is unaffected.

### Clients

| Client | Type | Used by | Needs |
|---|---|---|---|
| `aira-gateway` | public, **PKCE S256** | the SPA and the gateway's JWKS validation | exact redirect URIs and web origins — never `*` |
| *(service accounts)* | confidential | machine-to-machine callers | `client_credentials`; membership of the groups that grant what the caller may do, and the groups and audience mappers. A realm role grants nothing |
| *(directory, optional)* | confidential | Management's directory lookups | `view-users` and `query-groups` from `realm-management`, nothing else — see above |

**Settings on your side that matter**

- **Direct access grants (password grant): off.** AIRA does not use it, and enabling it weakens the
  realm for a convenience a machine-to-machine grant already provides.
- **Redirect URIs and web origins: pinned** to your SPA origin. A wildcard lets an attacker redirect
  the authorization code to a site they control.
- **Do not request `offline_access`.** The code flow already returns a refresh token;
  `offline_access` asks for one that outlives the SSO session, which a governance console must not
  hold — and if the realm forbids offline tokens, the code-to-token exchange fails with an error
  Keycloak returns *without* CORS headers, so the browser reports something that names neither the
  scope nor the setting.
- **Client description length**: Keycloak stores it in `varchar(255)`. A longer one breaks the
  realm import, and the failure looks like Keycloak not starting.

### An Active Directory federation

Where Keycloak federates Active Directory over LDAP — whether or not anybody ever signs on with
Windows — two settings on that federation decide when a change in AD reaches AIRA:

| Setting | Value | Why |
|---|---|---|
| *Cache policy* | **Max lifespan**, e.g. 5 minutes | the setting that decides when somebody is out |
| mapper *MSAD account controls* | **must exist** | what keeps a disabled account out |

**When does somebody disabled or moved in AD lose access?** That is Keycloak's *cache policy*, and
its default has no bound: Keycloak keeps its copy of a person until something evicts it. A person
disabled in AD can keep renewing their session, and a person taken out of a group keeps it — in
every new token, even after a fresh sign-on. With **Max lifespan**, a change in AD reaches AIRA at
most *lifespan + one access-token lifetime* later: 5 + 5 minutes with the lifespan above and
Keycloak's default token lifetime. *No cache* makes it one token lifetime, at the price of a
directory lookup per sign-in and renewal. A password sign-on and a Kerberos one behave the same:
this is a property of the federation.

**Keep the mapper *MSAD account controls*.** It is the only thing that reads AD's *account disabled*
flag: without it, a person disabled in AD still renews the session they have — and, with Windows
sign-on, still signs on with a ticket issued before. Keycloak creates it only when the federation's
vendor is *Active Directory*.
`tools/windows_sso.py` also sets its `always.read.enabled.value.from.ldap` to `true`; whether the
mapper Keycloak creates has that on, and whether it matters, this repository has not established.

### Windows sign-on (Kerberos) — optional

People at a PC of your Windows domain can reach the console **without typing a password**: the
browser presents the Kerberos ticket Windows gave them at logon, Keycloak accepts it (SPNEGO) and
issues the tokens a password would have produced. Somebody without a ticket — a PC outside the
domain, a browser not set up for it — sees Keycloak's form, and their domain password works there
as before.

**AIRA changes nothing for it.** The console, the gateway and the tokens are the same;
AIRA cannot even tell which way somebody signed in. The whole change is configuration in three
places, and this repository proves it end to end against a Samba domain — console and gateway, who
gets in and who does not: `make test-windows-sso`. `tools/windows_sso.py` makes the
changes of the first two lists below against that stand-in, which makes it their most precise
version. It also builds what an installation already has — the federation itself, read-only with
imported users, and two group mappers — and does nothing on a PC: the test browser is given the
allowlist on its command line.

#### 1. In the domain (your AD administrators)

- **An account of its own for Keycloak's service principal** — not the account the LDAP federation
  binds with. Exporting a keytab resets the account's password (`ktpass … -pass`), and a reset bind
  password stops Keycloak reading the directory at all. Set its password never to expire: every
  change makes the keytab stale.
- **The SPN, for the host in the browser's address bar**: `setspn -S HTTP/sso.firma.example
  FIRMA\svc-keycloak-http`. That host is the host of `AIRA_OIDC_ISSUER` — the console sends the
  browser there — and a browser asks for a ticket to *exactly* that name: an SPN for any other name
  is one no browser ever requests. Several names in use means several SPNs.
- **AES**: tick *This account supports Kerberos AES 128 bit / 256 bit encryption* on the account
  (`msDS-SupportedEncryptionTypes` = `0x18`). Without it the KDC issues RC4 tickets, which Keycloak's
  Java refuses — and everybody silently gets the form.
- **The keytab**, for Keycloak and nobody else:

  ```
  ktpass -out keycloak.keytab -princ HTTP/sso.firma.example@FIRMA.LOCAL ^
         -mapUser FIRMA\svc-keycloak-http -crypto AES256-SHA1 -ptype KRB5_NT_PRINCIPAL -pass *
  ```

  Copy it to the Keycloak host (e.g. `/opt/keycloak/conf/keycloak.keytab`, where the tool puts it),
  readable by Keycloak only. **Every password change of that account makes the keytab stale** —
  export it again, or every ticket is refused and everybody gets the form.

#### 2. In Keycloak — on the LDAP federation you already have

| Setting | Value | Why |
|---|---|---|
| *Allow Kerberos authentication* | on | also turns the browser flow's *Kerberos* step to **Alternative** |
| *Kerberos realm* | `FIRMA.LOCAL` | the AD domain, in capitals |
| *Server principal* | `HTTP/sso.firma.example@FIRMA.LOCAL` | the SPN above |
| *Key tab* | the keytab's path | Keycloak only *accepts* tickets; it needs the key and never asks the KDC |
| *Use Kerberos for password authentication* | off | a typed password stays an LDAP bind, as before |
| *Kerberos principal attribute* | **empty** | see below |
| *Username LDAP attribute* | `sAMAccountName` | what a ticket names |
| *Cache policy* | **Max lifespan**, e.g. 5 minutes | as on every AD federation — [above](#an-active-directory-federation) |
| mapper *MSAD account controls* | **must exist** | as on every AD federation — [above](#an-active-directory-federation) |

- **Leave the *Kerberos principal attribute* empty.** A ticket names its person
  `<sAMAccountName>@FIRMA.LOCAL`. Set to `userPrincipalName`, Keycloak looks for a UPN of exactly
  that — and finds nobody wherever the UPN ends in the mail domain (`anna.schmidt@firma.de`), which
  is most installations. Empty, it matches the account name and checks the realm. **Keycloak fills
  `userPrincipalName` in when an Active Directory federation is created**, so clear it afterwards.
- **The *Kerberos* step of the browser flow stays *Alternative*.** *Required* locks out everybody
  without a ticket, including every PC outside the domain.
- **The users DN (and any LDAP filter) decide who may sign on at all.** An account outside it has a
  valid ticket and no way in — which is how to keep service and admin accounts out of AIRA.
- **Edit the federation; never delete and recreate it.** Deleting removes the people it imported,
  and with them the `sub` AIRA binds every person to: everybody would arrive in the
  console as a new account, with nothing they were granted by name. The gateway matches a grant by
  name on the username, so there those grants keep working — two answers to one question, the case
  [*A username handed to somebody new*](#a-username-handed-to-somebody-new) describes.

#### 3. On the PCs (your client IT)

- The PC is in the domain, and the person logged on with their domain account.
- **Edge and Chrome**: the policy `AuthServerAllowlist` names the Keycloak host
  (`sso.firma.example`), by GPO or Intune. Without it, on Windows they offer the ticket only to a
  host in the *Local intranet* zone — which a dotted name is not, unless your zone policy puts it
  there — and the sign-on fails safe, into the form. This repository proves the allowlist, not the
  zone.
- **Firefox**: the policy `Authentication` → `SPNEGO` (or `network.negotiate-auth.trusted-uris`)
  names `https://sso.firma.example`.
- Users open Keycloak under the host the SPN names. If that name is a DNS **alias**, Edge and Chrome
  ask for a ticket to the canonical name instead (policy `DisableAuthNegotiateCnameLookup`), so the
  SPN must name that one.

**Checking it on a PC:** open the console, then `klist` in a command prompt — a ticket for
`HTTP/sso.firma.example` means the browser asked for one; none means the browser never tried.

#### What AIRA sees

The same token as from the form: `sub`, `preferred_username` (the `sAMAccountName`) and `groups` —
the paths your group mappers produce. A role or a use case is granted to those paths exactly as to
any other group; mapping AD groups onto the paths `AIRA_ROLE_GROUPS` already names means nothing in
AIRA changes at all.

#### When the form appears on a domain PC

Keycloak logs the cause as a warning. **Read the line before** *Received kerberos token, but there
is no user storage provider that handles kerberos credentials* — that one follows every failure and
names none.

| In Keycloak's log | Cause |
|---|---|
| nothing at all | the browser offered no ticket — the allowlist policy, a host the SPN does not name, no domain logon — or the person is disabled in AD, which is the intended answer |
| `Checksum failed` | the keytab is stale (the account's password changed) or for another account |
| `Not found LDAP user with kerberos principal [x@FIRMA.LOCAL]` | the *Kerberos principal attribute* is set, or the person is outside the users DN |
| only the line above, with no cause | Kerberos is not allowed on any federation of the realm |

A person disabled in AD whose session is still open is refused at its next renewal:
`REFRESH_TOKEN_ERROR … reason="User disabled"` — within the cache lifespan, not before.

> done three role groups named in `AIRA_ROLE_GROUPS` · a **`groups` claim with full paths on
> every client, including service accounts** — without it nobody holds any role at all · an
> **audience mapper** carrying `AIRA_OIDC_AUDIENCE` — without the setting the services do not
> start, without the mapper every token is refused · a public PKCE client with pinned URIs ·
> password grant off · `AIRA_OIDC_ISSUER` **as the browser sees it**, and `AIRA_OIDC_JWKS_URI`
> reachable from both services · `https://` for both · with an **AD federation**: a **bounded cache
> policy** and the *MSAD account controls* mapper · *(optional)* a read-only service account for
> directory search, binding a role to a group and granting a person who has not signed in ·
> *(optional)* Windows sign-on: an SPN and an AES keytab in AD, Kerberos on the federation with the
> principal attribute **empty**, and the browsers' allowlist policy

---

## 3. Apache Kafka

Configuration flows Management → Kafka → gateway. **Nine compacted topics**, one per entity kind:

| Topic | Carries |
|---|---|
| `aira.usecases` | use cases — created or changed, retired, purged |
| `aira.memberships` | grants on a use case: to one person, and to a group |
| `aira.api-keys` | key hashes and revocations |
| `aira.pipelines` | pipeline configuration |
| `aira.budgets` | budgets, the installation's included |
| `aira.rate-limits` | rate limits |
| `aira.models` | the model catalog, prices and capabilities |
| `aira.anomaly-rules` | anomaly rules |
| `aira.roles` | what each stored role may do, and the group that confers it |

Each record is a JSON value under a key — the entity's slug, prefix, id or name; `slug|username` or
`slug|group` for a grant — with an `event_type` header, and a `traceparent` header where the change
that caused it carried one. There is no version field and no schema registry to provide: the gateway
applies the types it knows and logs and skips any other (`config_event_not_applied`). A deletion is
an event with a body, never a tombstone, so compaction keeps the last event of every key. The events
themselves, with their keys and fields, are listed in [`API.md` §4](API.md#4-configuration-events).

**On a cluster several stages share**, give each installation a stage with `AIRA_KAFKA_STAGE`
(both planes, the same value). The topics become `aira.<stage>.<entity>` — `aira.t.usecases`
— and the gateway's consumer group `aira-gateway.<stage>`. The stage follows
`aira.` on purpose: one prefixed ACL, `aira.t.`, then covers exactly one stage's topics,
including a topic added later; the consumer group is a resource of its own and needs its own ACL.
A stage must not be able to write another's topics, because the gateway builds its authorization
from them. Without a stage the names are the ones above.

**What you provide**

- `cleanup.policy=compact` on each. Compaction is what makes the read-model rebuildable: the latest
  value per key is the state.
- **Create them explicitly, before either plane starts.** With auto-creation off — as it should be —
  a missing topic stops **all** configuration, not only its own. The relay waits for the topic
  (40 seconds), stops at that event and exits with *publishing stopped at event … on `<topic>`*;
  the outbox is one queue across the nine topics, so that event and every later change on every
  topic stay pending, and each run tries again. The consumer does not start without all of its
  topics and restarts in a loop, applying nothing. Neither `/readyz` notices, and the console shows
  every change as saved. A missing `Write` ACL stops the relay the same way, at once.
  `make kafka-topics` creates the topics in this repository's own broker and
  `make kafka-topics-external` in yours (`PLAINTEXT` only), both idempotently, and both create
  exactly the topics the code publishes to.
- **Room for a 1,000,000-byte event.** Management refuses a change whose event would be larger, and
  the relay sends a request of up to 1 MiB. Kafka's default `max.message.bytes` (1,048,588) fits;
  set below about 1,001,400 bytes, on the topic or the broker, it makes the broker refuse the
  largest events, and the relay parks them — named on every run, never sent.
- Retention: compaction handles it; no time-based retention is needed.
- One partition per topic is sufficient. Ordering per key is what matters, and volume is
  configuration changes rather than traffic.
- Replication is yours to choose, with one limit: the relay publishes with `acks=1`, so an event
  counts as sent once the partition leader has it, and a leader lost before replicating it takes
  that change with it.

**Single-writer processes.** Run exactly **one** relay and **one** consumer per installation — not
one per gateway instance: the read-model lives in the shared `aira_gateway` database. They are not
horizontally scalable and do not need to be; relay runs never overlap, because each takes the lock
([§1](#1-postgresql)).

### The bus is a trust boundary

> **Anything that can publish to these topics can grant itself access.** The gateway applies what
> arrives straight into the read-model its authorization is derived from — that is what keeps the
> consumer simple, an upsert or a delete per event, keyed so that an event delivered twice grants
> nothing more — and it is only safe if the broker is authenticated. A publisher who is not AIRA can send
> `api_key.created` with a hash of their choosing, or `use_case_group.granted` naming a group they
> belong to, and hold administrator access to any use case. No credential is presented and
> **no audit row is written**, because from the gateway's side
> nothing unusual happened: configuration arrived, exactly as configuration does.

`Write` on **any one** of the nine topics is enough for that: the consumer applies an event by its
`event_type` header, whichever topic it arrived on.

So both planes take a broker identity, and both **refuse to start on `PLAINTEXT` outside `local`**:

```
AIRA_KAFKA_SECURITY_PROTOCOL=SASL_SSL
AIRA_KAFKA_SASL_MECHANISM=SCRAM-SHA-512    # default when unset; PLAIN sends the password in clear
AIRA_KAFKA_SASL_USERNAME=aira-gateway      # a separate identity per service
AIRA_KAFKA_SASL_PASSWORD=…                 # from Vault
AIRA_KAFKA_SSL_CAFILE=/etc/ssl/private-ca.pem   # only for a private CA
```

Each plane reads these two for itself. Where both planes share one environment or one Vault path,
the per-plane names carry the two identities instead and replace the shared ones:
`AIRA_GATEWAY_KAFKA_SASL_USERNAME` / `AIRA_GATEWAY_KAFKA_SASL_PASSWORD` for the consumer (the
gateway's), `AIRA_MANAGEMENT_KAFKA_SASL_USERNAME` / `AIRA_MANAGEMENT_KAFKA_SASL_PASSWORD` for the
relay (Management's). The database works the same way, with `AIRA_GATEWAY_POSTGRES_USER` /
`_PASSWORD` and `AIRA_MANAGEMENT_POSTGRES_USER` / `_PASSWORD`.

The identity is a username and a password: `PLAIN`, `SCRAM-SHA-256` or `SCRAM-SHA-512`. Kerberos
(`GSSAPI`) and `OAUTHBEARER` are not supported, and `SSL` on its own identifies nobody — there is no
client-certificate setting — so it is `SASL_SSL`. `SASL_PLAINTEXT` is not refused outside `local`,
and carries every event unencrypted.

**What you provide**: one broker principal per service (relay and consumer) — `Write` on the nine
topics for the relay; `Read` on them for the consumer, and `Read` on its consumer group,
`aira-gateway` (`aira-gateway.<stage>` with a stage), where it commits its offsets. Nothing wider:
the relay sends plain records and needs no idempotent or transactional rights. ACLs are what make
the identity worth having; an authenticated principal that may publish to any topic is the same
hole with a login.

> done nine compacted topics, created explicitly, with room for a 1,000,000-byte event · one relay ·
> one consumer · **a broker identity per service and ACLs scoped to these topics and the consumer
> group** · `SASL_SSL` · bootstrap servers reachable from both planes

---

## 4. Redis

Optional to start, **required to scale**. It holds rate-limit buckets and budget reservations —
values written on *every* request, which is what earns it a place.

Without it, rate limits become **per-instance**: N replicas behind a load balancer allow N × the
limit. Budgets fall back to the Postgres path, which enforces but is racy.
`/readyz` reports `degraded: true` and keeps serving.

**What you provide**: any Redis 6+ reachable at `AIRA_REDIS_URL` — one server, or Sentinel; Redis
Cluster is not supported. The default is `redis://localhost:6379/0`, and an empty value means none.
No persistence is required: a budget counter seeds from Postgres on a miss and expires after five
minutes, so drift cannot outlive a period, and a rate-limit bucket that is gone starts full.

- **The URL carries everything** — TLS (`rediss://`), the username and password, the database.
  `AIRA_REDIS_USERNAME`, `AIRA_REDIS_PASSWORD` and `AIRA_REDIS_DB` are read in Sentinel mode only,
  so with a password the URL is the secret, for Vault.
- **Redis Sentinel** is supported. Set `AIRA_REDIS_SENTINELS` (`host:port,…`) and
  `AIRA_REDIS_SENTINEL_SERVICE` (the name the sentinels monitor) instead of the URL. The
  passwords, `AIRA_REDIS_PASSWORD` and `AIRA_REDIS_SENTINEL_PASSWORD`, are secrets for Vault.
  On failover the gateway asks the sentinels for the new leader. While it moves, rate limits
  hold per instance and budgets use the Postgres path, as for any Redis outage. **Sentinel mode
  has no TLS**, to the sentinels or to the leader.
- **Memory**: a few megabytes of small counters, each with a TTL; 128 MB is plenty. Set a
  `maxmemory` **and** `maxmemory-policy noeviction` — the compose stack sets the policy and no cap.
  An evicting policy can drop a caller's rate-limit bucket silently, which gives them a fresh
  allowance. With `noeviction` and a cap, a full Redis refuses writes, and the gateway falls back
  visibly.
- **Nothing requires TLS or a password** for Redis outside `local`; both are yours to set.

> done one Redis, or accept per-instance limits and one gateway replica

---

## 4a. HashiCorp Vault

Optional, and where the credentials belong. Both planes read **one KV-v2 path, once,
at start-up**, logging in with an AppRole. Any `AIRA_*` setting can come from there, and a value
there wins over the environment.

**What you provide**

| | |
|---|---|
| A **KV-v2** secrets engine | read at `<VAULT_ADDR>/v1/<VAULT_MOUNT>/data/<VAULT_PATH>`; the defaults are `secret` and `aira` |
| The **AppRole** auth method at the path `approle` | the login is `POST /v1/auth/approle/login`; that path is fixed in the code, and no setting moves it |
| A **policy** with `read` on `<mount>/data/<path>` | nothing else: an AppRole that may write its secrets is one whose compromise rewrites the installation |
| An **AppRole** holding that policy | its role id in `VAULT_ROLE_ID`, its secret-id in a mounted file named by `VAULT_SECRET_ID_FILE` |
| **HTTPS** | outside `local`, a `VAULT_ADDR` that is `http://` to anything but loopback refuses to start, on both planes |
| The namespace, on Vault Enterprise | `VAULT_NAMESPACE` |

`tools/vault_setup.py` creates exactly this — read on one path, role tokens of one hour (four at
most), a secret-id valid for 720 hours and without a use count, written to a file with mode `0600`
— and `make vault-init` runs it against the development Vault. The steps against yours are
[`deployment/integrated.md` §4](deployment/integrated.md#4-vault-put-the-secrets-in-before-anything-reads-them);
the variables are [`CONFIGURATION.md` §7](CONFIGURATION.md#7-secrets-from-vault).

What decides how it behaves:

- **Read once.** A Vault that goes down later changes nothing in a running process, and a changed
  value — or a new secret-id — takes effect at the next start: **rotation is a restart**. The
  secret-id expires as well, and the first start after that fails with *Vault refused the AppRole
  login*; mint the new one before the old one runs out.
- **Fail closed.** A configured Vault that cannot be reached, refuses the login or answers anything
  but `200` stops the process from starting, rather than letting it run on whatever the environment
  holds. A path that does not exist is reported apart from an outage: it is a path or a permission.
- **Configured means `VAULT_ADDR` and a credential** — `VAULT_ROLE_ID` with its secret-id, or, for
  local development, `VAULT_TOKEN`. **`VAULT_ADDR` alone is not configured**: the process then reads
  every value from the environment, and nothing but the gateway's `/readyz` says so
  (`"secrets": {"source": "environment"}`).
- **Both planes read the same path by default** — the Compose files give each `VAULT_PATH=aira` —
  so each can read the other's credentials. `VAULT_PATH` and `VAULT_ROLE_ID` are set per process if
  you want them apart; the setup tool makes one policy and one role.
- **A key is a setting's name**, with or without the `AIRA_` prefix and in either case. Two
  spellings of one setting with different values refuse to start.

> done a KV-v2 path · AppRole at `approle` · a read-only policy on that path · the secret-id in a
> mounted file, and a date to replace it by · `https://`

---

## 5. Model platforms

At least one. Each is registered **only when configured**, and each is audited under its own
provider, publisher and region.

### Google Agent Platform (formerly Vertex AI) — Gemini, Anthropic and managed open models

> **The product was renamed.** Vertex AI is now
> [Gemini Enterprise Agent Platform](https://cloud.google.com/products/gemini-enterprise-agent-platform);
> Agent Engine became Agent Runtime, Memory Bank became Agent Platform Memory Bank
> ([name changes](https://docs.cloud.google.com/gemini-enterprise-agent-platform/vertex-ai-name-changes)).
> The REST hosts, paths and credentials this adapter uses are unchanged, so the settings keep the
> `AIRA_VERTEX_*` names — renaming a configuration key breaks every deployment that has one, and
> buys a word.

| You provide | Why |
|---|---|
| A GCP **project** with the Agent Platform (Vertex AI) API enabled | |
| A service account with `roles/aiplatform.user` — reached by its key, **without a key** through workload identity federation (D), or as the machine's own on Google Cloud — **or** an API key | One adapter for all of them — but **Claude needs the service account**: Vertex refuses an API key for Anthropic models — see below |
| The credential, from the environment, **Vault** or a file | `AIRA_VERTEX_CREDENTIALS` (JSON), `AIRA_VERTEX_CREDENTIALS_FILE` (a path to the same JSON) or `AIRA_VERTEX_API_KEY` |
| The **regions** you are permitted to use | Listed in `AIRA_ALLOWED_REGIONS`; a model outside them refuses to start, and the console refuses one **as it is typed**, naming what is allowed. `global` is not in the shipped default: it names no region and guarantees none, so an installation that wants it says so out loud |
| **Model Garden access** for the publishers you want | Anthropic models need to be enabled in your project; an open model the project cannot use answers the model check with `404` |
| For an **open model** (GLM, DeepSeek): a permission by name | They are served only at `global`, which names no region. `AIRA_ALLOWED_MODEL_REGIONS=global=glm-4.7-maas` permits it for that model and no other |

One transport, three dialects. Anthropic on Vertex uses `:rawPredict` with the Messages API:
`max_tokens` is always sent, cache tokens count as input, and there are no embeddings.

- **Thinking** takes the shape the catalogue declares for the model. A token budget (`limited`,
  1024 tokens at least) is the `enabled` thinking Claude 4.5 and earlier take. A level word (`low`,
  `medium`, `high`, `xhigh`, `max`) or `auto` is the `adaptive` thinking that Claude Opus 4.7 and
  every later model require, with the level as its effort. The reference list declares each Claude
  model this way.
- **Its reasoning** is returned, buffered or streamed, and stored with the answer under the same
  `store_payloads` gate when the use case's `include_reasoning` is on, which is the default.
- **Sampling.** The same models answer `400` to `top_k`, to a `temperature` other than 1 and to a
  `top_p` below 0.99. Their catalogue entry says *fixed sampling*, and the gateway sends them no
  request that sets one: it moves on to the next model in the chain, or refuses naming the control.
- **Structured output** takes the formats Anthropic lists (`date-time`, `date`, `time`,
  `duration`, `email`, `hostname`, `uri`, `ipv4`, `ipv6`, `uuid`). A number's width (`int32`,
  `double`) is left out of the schema it is sent. A constraint it cannot take — a length, a
  minimum, `minItems` above 1 — skips the model by name.
- **A cache write for an hour** is billed at twice the input price, a five-minute one at the
  model's cache-write price.

The managed open models speak the **OpenAI**
dialect at one endpoint, `endpoints/openapi/chat/completions`, with the model named in the body as
`publisher/model` — catalogue one under provider `vertex`, its publisher (`zai-org`,
`deepseek-ai`) and region `global`. Thinking is a switch there (on or off, no budget), their
reasoning is returned only to a use case that asked for it, and there are no embeddings.

**Speech models** are Gemini models — on Vertex, or AI Studio; no other dialect speaks.
- Measured on Vertex, and Google's to change: `gemini-2.5-flash-tts`, `gemini-2.5-pro-tts` and
  `gemini-2.5-flash-lite-preview-tts` answer in `europe-west1` and `europe-west4`;
  `gemini-2.5-flash-preview-tts` and `gemini-2.5-pro-preview-tts` only in `us-central1`.
- Catalogue a speech model with the capability `speech` and without `generate`. It then answers
  only a request for audio, and a text request to it is refused by name.
- The audit trail keeps a description and hash of the audio, not the audio.

#### Which credential — and why it is not a free choice

|  | Service account key | API key | No key (D, **recommended**) |
|---|---|---|---|
| Setting | `AIRA_VERTEX_CREDENTIALS`, or `AIRA_VERTEX_CREDENTIALS_FILE` | `AIRA_VERTEX_API_KEY` | `AIRA_VERTEX_CREDENTIALS(_FILE)` with Google's federation configuration, and `AIRA_VERTEX_FEDERATION_*` — or `AIRA_VERTEX_METADATA_SERVER` on Google Cloud |
| How it authenticates | signed JWT exchanged for a short-lived token | `x-goog-api-key`, sent as-is | your Keycloak's token, exchanged at Google's STS, acting as the service account for an hour |
| Publishers it reaches | all | Google and the open models — **not Anthropic**: Vertex answers `401`, and the caller gets `502` | all |
| Rotation | a new JSON key: create it in IAM, store it, restart, then delete the old one — or, from a file, write it over the file: the next token is signed with it, no restart | a new key: store it, restart, then revoke the old one | your Keycloak client's secret; nothing at Google |
| Scope | one IAM role, auditable per principal in Cloud Logging | whatever the key is restricted to | the same role, through the binding that lets the client act as the account |
| Region | locational host, real residency | **the same** — see the note below | the same |

Set both and the **service account wins**: a deployment that has one has made the more deliberate
choice, and silently preferring a key left in the environment would be a downgrade nobody asked for.

> **The residency footnote that matters.** Google's own *express mode* documents the **global**
> endpoint `aiplatform.googleapis.com`, and its
> [data-residency page](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/data-residency)
> is explicit that global endpoints "route and process data anywhere globally… you can't control or
> know which region your ML processing requests are sent to". **AIRA does not use that endpoint.**
> The same API key was measured to answer on the *locational* host
> (`europe-west1-aiplatform.googleapis.com`, both path forms), so this adapter keeps its regional
> hosts and its per-model region check, and an API key gets the same residency a service account
> does. If you point anything else at the global endpoint, that guarantee is gone. The one
> exception is a model whose region is `global`, where `global` is permitted: it is sent there,
> and its audit row says `global`. Permit it by name, as the open models need — Google serves them
> nowhere else (`AIRA_ALLOWED_MODEL_REGIONS`); `global` in `AIRA_ALLOWED_REGIONS` permits it for
> every model.

The credentials are read once, at start-up, like every secret here ([§4a](#4a-hashicorp-vault)):
the service account refreshes its short-lived token by itself, but a new JSON key or API key takes
effect only at the next start. Delete the old service-account key first, and calls fail with `503`
once the token it issued expires.

**A key in a file is the exception.** `AIRA_VERTEX_CREDENTIALS_FILE` names a file holding the same
JSON — a Docker or Kubernetes secret, a file a Vault agent renders — and the gateway looks at it
again each time it fetches a token, about every 48 minutes. Rotation is then: create the new key,
write it over the file, wait an hour, delete the old key. A file changed into something unusable
keeps the last key that worked, and the failed refresh is in the log; a file missing at start-up,
or holding no usable key, stops the start. The value and the file are exclusive: both set stops the
start.

#### B — the service account, step by step

Console, or the equivalent `gcloud`. Replace `PROJECT` with your project id.

1. **Enable the API.** Console → *APIs & Services → Library* → “Vertex AI API” (listed under Agent
   Platform after the rename) → **Enable**.
   `gcloud services enable aiplatform.googleapis.com --project PROJECT`
2. **Create the service account.** *IAM & Admin → Service Accounts → Create*. A name like
   `aira-gateway`. No project role at creation.
   `gcloud iam service-accounts create aira-gateway --project PROJECT`
3. **Grant exactly one role.** `roles/aiplatform.user` — the narrowest that can call
   `:generateContent` and `:rawPredict`. Not `roles/editor`.
   ```
   gcloud projects add-iam-policy-binding PROJECT \
     --member "serviceAccount:aira-gateway@PROJECT.iam.gserviceaccount.com" \
     --role roles/aiplatform.user
   ```
4. **Create a JSON key.** *Keys → Add key → Create new key → JSON*. It downloads once.
   `gcloud iam service-accounts keys create key.json --iam-account aira-gateway@PROJECT.iam.gserviceaccount.com`
5. **Store it.** Either leave it a file and name it — the simplest, and the one that rotates
   without a restart:
   `AIRA_VERTEX_CREDENTIALS_FILE=/run/secrets/vertex-key.json`
   (in Compose, a `secrets:` entry or a read-only volume; the gateway's user must be able to read
   it). Or the whole file, as one line, in Vault (below) or in the environment:
   `AIRA_VERTEX_CREDENTIALS='{"client_email":"…","private_key":"-----BEGIN PRIVATE KEY-----\n…"}'`
6. **Name the project and the models:**
   ```
   AIRA_VERTEX_PROJECT=PROJECT
   AIRA_VERTEX_MODELS=europe-west1/google/gemini-2.5-flash,europe-west1/google/text-embedding-005
   AIRA_ALLOWED_REGIONS=europe-west1,europe-west4,eu
   ```
   The form is `region/publisher/model`: **region per model, not per process**.
7. **Start.** A malformed credential, or a model in a region the list does not permit, is a
   **startup** failure with the reason named — deliberately, because a gateway that starts and then
   fails every request looks like an outage at Google.

Reference: [Authenticate to Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/machine-learning/authentication)
· [Deployments and endpoints](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/locations)

#### C — the API key, step by step

1. **Get the key.** Console → *APIs & Services → Credentials* → the key created for you, or
   *Create credentials → API key*
   ([docs](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/start/api-keys)).
2. **Check its API restrictions.** This is the step that bites: a key restricted to the Agent
   Platform API answers `403 API_KEY_SERVICE_BLOCKED` on any other Google API, and a key restricted
   to something else answers the same here. *Credentials → your key → API restrictions.*
3. **Store it** as `AIRA_VERTEX_API_KEY`, from the environment or Vault.
4. **The rest is identical to B** — `AIRA_VERTEX_PROJECT`, `AIRA_VERTEX_MODELS`,
   `AIRA_ALLOWED_REGIONS`. Same hosts, same paths, same residency check; only the header differs.
5. **Except for Claude.** Vertex refuses an API key for Anthropic models (`401`, which the caller
   receives as a `502`), so a deployment on a key cannot serve them. The gateway sends the key to
   every publisher alike and does not refuse such a model at start-up; to serve Claude, configure
   the service account (B).

#### D — no key at all: workload identity federation through your Keycloak

A service-account key is a private key that is valid until somebody deletes it, and an organisation
created on Google Cloud since May 2024 may not create one: the policy
`iam.managed.disableServiceAccountKeyCreation` is on by default. Federation needs none. The gateway
asks your Keycloak for a token as a client of it, Google's Security Token Service exchanges that
token for a Google one, and the Google token acts as the service account for an hour. Google holds
no secret of yours and you hold none of Google's; what is left to protect and rotate is one client
secret in your own Keycloak.

**Keycloak and Google stay two separate identity worlds.** Nothing connects Keycloak to Google
Workspace or Cloud Identity: no directory sync, no single sign-on, no Workspace account or licence.
The trust lives in the GCP project alone — a *workload identity pool* that believes exactly one
Keycloak client, a machine and not a person — and lets that client act as one service account.
Nobody's Keycloak account reaches Google, and no Google user is created.

**Who has to do what.** The Keycloak steps need a realm administrator. The Google steps need, in
the GCP project, `roles/iam.workloadIdentityPoolAdmin` (step 3) and the right to change the service
account's IAM policy, `roles/iam.serviceAccountAdmin` or the project's owner (step 4) — usually your
Google Cloud administrators rather than whoever runs AIRA. An organisation may restrict which
identity providers a pool may trust (`constraints/iam.workloadIdentityPoolProviders`); then the
realm's issuer URL has to be added to that policy's allowed values first, and a pool created without
it is refused with `Org Policy violated` (`ORG_POLICY_VIOLATED` in the model check).

**In Keycloak** (the realm the console already uses, or another):

1. *Clients → Create client*, id `aira-gateway-gcp`. **Client authentication** on, **Service
   accounts roles** on, every other flow off. *Credentials* → copy the client secret.
2. *Client scopes → aira-gateway-gcp-dedicated → Add mapper → By configuration → Audience*:
   **Included Custom Audience** `aira-gcp`, **Add to access token** on. Google checks the token's
   `aud` against the provider's allowed audiences, and Keycloak's default `aud` is not one of them.

**At Google** (replace `PROJECT` and `PROJECT_NUMBER`):

3. A pool and a provider that trusts your realm:
   ```
   gcloud iam workload-identity-pools create aira --location global --project PROJECT
   gcloud iam workload-identity-pools providers create-oidc keycloak \
     --location global --workload-identity-pool aira --project PROJECT \
     --issuer-uri "https://sso.example.com/realms/aira" \
     --allowed-audiences aira-gcp \
     --attribute-mapping "google.subject=assertion.azp" \
     --attribute-condition "assertion.azp == 'aira-gateway-gcp'"
   ```
   The issuer is the realm's URL exactly as it stands in the token's `iss`. **If Google cannot reach
   your Keycloak** — it lives on the internal network — upload the realm's keys instead, without
   the certificate fields Google refuses, by adding `--jwk-json-path realm-jwks.json` after:
   ```
   curl -s https://sso.example.com/realms/aira/protocol/openid-connect/certs \
     | jq '{keys: [.keys[] | select(.use == "sig") | del(.x5c, .x5t, ."x5t#S256")]}' \
     > realm-jwks.json
   ```
   Keycloak rotates its realm keys; upload them again when it does
   (`gcloud iam workload-identity-pools providers update-oidc … --jwk-json-path`), or every
   exchange fails with `SIGNATURE_NOT_VERIFIED`.
4. Let the client act as the service account (B, steps 2–3, without step 4's key):
   ```
   gcloud iam service-accounts add-iam-policy-binding \
     aira-gateway@PROJECT.iam.gserviceaccount.com \
     --role roles/iam.workloadIdentityUser \
     --member "principal://iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/aira/subject/aira-gateway-gcp"
   ```

**In AIRA:**

5. The configuration — Google's `external_account` format, which names no secret:
   ```json
   {
     "type": "external_account",
     "audience": "//iam.googleapis.com/projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/aira/providers/keycloak",
     "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
     "token_url": "https://sts.googleapis.com/v1/token",
     "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/aira-gateway@PROJECT.iam.gserviceaccount.com:generateAccessToken"
   }
   ```
   as `AIRA_VERTEX_CREDENTIALS`, or as a file named by `AIRA_VERTEX_CREDENTIALS_FILE`. `gcloud iam
   workload-identity-pools create-cred-config` writes the same file; its `credential_source` is not
   used when the next three settings are set.
6. ```
   AIRA_VERTEX_FEDERATION_TOKEN_URL=https://sso.example.com/realms/aira/protocol/openid-connect/token
   AIRA_VERTEX_FEDERATION_CLIENT_ID=aira-gateway-gcp
   AIRA_VERTEX_FEDERATION_CLIENT_SECRET=…        # from Vault
   ```
   and `AIRA_VERTEX_PROJECT`, `AIRA_VERTEX_MODELS`, `AIRA_ALLOWED_REGIONS` as in B.
7. **Check reachability** on a model in the console. A refusal names its cause:
   `AUDIENCE_MISMATCH` (step 2), `ATTRIBUTE_CONDITION_REJECTED` (step 3's condition),
   `ISSUER_UNREACHABLE` (upload the keys), `IAM_PERMISSION_DENIED` naming
   `iam.serviceAccounts.getAccessToken` (step 4), `CLIENT_REJECTED` (step 1's secret).

Rotation is Keycloak's: regenerate the client secret, store it, restart the gateway. Disabling the
client, or removing the binding of step 4, ends the access within the hour a token lives.

**Other sources of the identity.** Instead of the three `FEDERATION` settings, the configuration's
own `credential_source` may name a **file** that a platform keeps a token in (a Kubernetes projected
service-account token, plain text or a JSON field) or a **local URL** answered on a GET, with
headers — Google's format for both. AWS and executable sources are refused at start: an executable
would be a command a credential file runs. Leaving `service_account_impersonation_url` out uses the
federated token directly, for an installation that grants `roles/aiplatform.user` to the federated
principal itself.

**On Google Cloud** — a VM, GKE with workload identity, Cloud Run — `AIRA_VERTEX_METADATA_SERVER=true`
uses the machine's own service account. Nothing else.

The configuration is checked at start: an audience that is not a provider's full name, a
`token_url` or impersonation address anywhere but `https://….googleapis.com` (the token sent there
is a credential), and a lifetime outside 600–43200 seconds each stop the start.

#### Both credentials from Vault

Any `AIRA_*` setting can come from Vault — the settings source is generic — and **Vault wins over
the environment** where both hold a value ([§4a](#4a-hashicorp-vault)). Add them to the configured
path with `patch`, which merges into what it holds: on KV-v2, `vault kv put` writes a new version
with only the keys it names, and every other credential at that path is gone from it. `put` is
right only for a path that holds nothing yet.

```bash
vault kv patch secret/aira \
  AIRA_VERTEX_API_KEY='AQ.…' \
  AIRA_VERTEX_CREDENTIALS='{"client_email":"…","private_key":"-----BEGIN PRIVATE KEY-----\n…"}'
```

Verified against a running Vault, not assumed: both arrive, and the multi-line PEM survives the
round trip intact. The startup log names **which keys** were loaded and never their values
(`vault_secrets_loaded … keys=[AIRA_VERTEX_API_KEY, AIRA_VERTEX_CREDENTIALS]`).

#### E — prices from your billing account

What a model costs **you** is on your billing account, not on Google's price list: contract
discounts, committed use and the surcharge for Claude on a regional endpoint all make the two
differ. When a model is catalogued, the console can fill its prices from the account — each one
naming the SKU, the line of the bill, it was read from.

1. **Find the billing account id**: Google Cloud console → *Billing* → the account that pays for the
   project → *Account ID*, `012345-567890-ABCDEF`.
2. **Let the gateway's Google identity read its prices**: on the **billing account** (its own IAM,
   not the project's) grant the service account *Billing Account Viewer*,
   `roles/billing.viewer` — it shows prices and costs, and changes nothing. Somebody with *Billing
   Account Administrator* does this. The identity is the one from B or D; an API key cannot read a
   billing account.
   ```
   gcloud billing accounts add-iam-policy-binding 012345-567890-ABCDEF \
     --member "serviceAccount:aira-gateway@PROJECT.iam.gserviceaccount.com" \
     --role roles/billing.viewer
   ```
3. **Enable the Cloud Billing API** in the project: `gcloud services enable
   cloudbilling.googleapis.com --project PROJECT`.
4. `AIRA_VERTEX_BILLING_ACCOUNT=012345-567890-ABCDEF`, restart the gateway.

In the model editor's *What it costs* tab, **Prices from the billing account** asks for the model
and the form's first region; filling a model from the model reference asks by itself. Prices come
in `AIRA_CURRENCY` — Google converts — and a contract price is used where your contract has one.
A field the account has no SKU for keeps what was there, and the editor says which.

**Where the account has no price**, the model reference's US-dollar list price is the fallback. It
is filled only at a rate you set, `AIRA_MODEL_REFERENCE_EXCHANGE_RATE` (euros per dollar, e.g.
`0.92`, on Management), and says so under the field; without a rate it is shown and not filled.

The match from a SKU's name to a model is by name, word for word (*Gemini 2.5 Flash Input Text* is
`gemini-2.5-flash`'s input, *Gemini 2.5 Flash Lite …* is not). Check the SKU named under each price
before saving: a name Google spells differently from the model id is not matched, and is reported
as unpriced rather than guessed.

#### When the model check says no

The console's **Check reachability** asks each region a question that costs nothing, and when the
answer is no it says why: the cause Google stated, as one word from Google's own list, and what to
change. Requests that fail the same way carry the word in their error message
(`Google Vertex AI returned 403. Reason: SERVICE_DISABLED.`), so a developer can tell the fault is
the installation's and say which; the explanation, with project numbers and permissions, is for
whoever runs the installation — the check, `/readyz` and the gateway's log. Google's own message is
never repeated: it can quote the address it was called at.

| Word | What it means | What to do |
|---|---|---|
| `SERVICE_DISABLED` | the Vertex AI API is not enabled in the project | enable `aiplatform.googleapis.com` (B, step 1), wait a minute |
| `IAM_PERMISSION_DENIED` | the account lacks a permission — or the model does not exist in that region, which Google reports the same way | grant `roles/aiplatform.user` (B, step 3); check the model's region |
| `NOT_FOUND` | no such model at that address | the model id, its region (not every model is offered in every region), and for Claude: enabled in Model Garden |
| `PERMISSION_DENIED`, `FAILED_PRECONDITION` | the call is refused for the project | for a partner model, accept its terms in Model Garden |
| `CONSUMER_INVALID`, `USER_PROJECT_DENIED` | the project does not exist, or the account may not use it | `AIRA_VERTEX_PROJECT` is the project **id**; grant Service Usage Consumer |
| `BILLING_DISABLED` | no billing on the project | link a billing account |
| `API_KEY_INVALID`, `API_KEY_SERVICE_BLOCKED` | the API key is wrong, or restricted to other APIs | C, step 2 |
| `RATE_LIMIT_EXCEEDED`, `RESOURCE_EXHAUSTED` | a quota or Google's capacity is spent | raise the quota, or add a region |
| `KEY_NOT_ACCEPTED` | Google does not accept the service account's key: deleted, disabled, or another account's | a new key (B, step 4) |
| `ACCOUNT_NOT_FOUND` | the service account is deleted or disabled | — |
| `CLOCK_SKEW` | the token request's time window was refused | synchronise this machine's clock |
| `AUDIENCE_MISMATCH`, `ATTRIBUTE_CONDITION_REJECTED`, `ISSUER_UNREACHABLE`, `SIGNATURE_NOT_VERIFIED`, `CLIENT_REJECTED`, `SERVICE_ACCOUNTS_DISABLED` | federation (D) refused at one of its steps | D, step 7 names the step for each |
| `CERTIFICATE_NOT_TRUSTED` | a proxy that inspects TLS presented its own certificate | `SSL_CERT_FILE` ([a private certificate authority](#a-private-certificate-authority)) |
| `NAME_NOT_RESOLVED`, `CONNECT_TIMEOUT`, `PROXY_REFUSED` | no way out to Google from here | DNS, the firewall, `HTTPS_PROXY` |

### Microsoft Foundry / Azure OpenAI

| You provide | Why |
|---|---|
| An **Azure OpenAI resource** endpoint | `AIRA_FOUNDRY_ENDPOINT` |
| An **API key** | `AIRA_FOUNDRY_API_KEY`. Entra ID is not configurable yet: the adapter can send an Entra bearer token, and no setting supplies one |
| One **deployment per model**, with its region | `AIRA_FOUNDRY_DEPLOYMENTS` as `model=deployment\|region[\|embed]`, `;`-separated — e.g. `AIRA_FOUNDRY_DEPLOYMENTS=gpt-4o=prod-gpt4o\|westeurope`. The region is required — an entry without one refuses to start — and checked against `AIRA_ALLOWED_REGIONS` at start-up; the per-model list does not apply here |

An Azure **deployment name is not a model name**. Attributing a response to the deployment would not
fail — the spend figure would quietly stop being complete, because a deployment has no price. AIRA
keeps both and prices the underlying model.

### Self-hosted, OpenAI-compatible (Ollama, vLLM, …)

| You provide | Why |
|---|---|
| One or more **named servers**, each with at least one model | `AIRA_OPENAI_SERVERS` as `name=url\|models\|embeddings\|region`; a fleet is several machines and each is audited by name |
| A **region label**, if the deployment claims one | Checked at startup against `AIRA_ALLOWED_REGIONS`; a server without one is served unchecked |

**No credential is sent** to these servers, and there is no setting for one. A server that requires
a key — vLLM's `--api-key`, for one — can be used only behind a proxy that adds it; otherwise the
network is all that protects it.

Two traps this dialect hides — here, and on Foundry and Vertex's open models alike — both handled:
usage arrives in a chunk with an **empty `choices` array**, and the vendor reports **no stream
usage** unless `stream_options.include_usage` is sent — a stream reporting none would be released
rather than settled, and every streamed request would be silently free.

### Google AI Studio (the Gemini public API)

An API key (`AIRA_GOOGLE_API_KEY`). Simplest to start with, and the one to avoid where residency
matters: `generativelanguage.googleapis.com` names no region and guarantees none, so AIRA records it
as `global` — a value the EU default does **not** contain. A deployment that wants it says `global`
in `AIRA_ALLOWED_REGIONS` out loud, which turns "may we send data there" from something somebody
remembers into a line of configuration and a region on every audit row. With the key set and
`global` not in the list, the gateway does not start.

That line is not narrow. The per-model list, `AIRA_ALLOWED_MODEL_REGIONS`, does not reach AI Studio,
so `global` in `AIRA_ALLOWED_REGIONS` permits `global` for **every** model on every platform — a
Vertex model at `global`, a server or a deployment labelled so. And setting `AIRA_ALLOWED_REGIONS`
replaces the default list rather than adding to it, so it names every region you use, then
`global`.

`AIRA_GEMINI_MODELS` may be left empty: cataloguing a model under this provider,
`generative-language`, is enough to serve it.

### Which platforms can be asked what they offer

The console can ask a provider for its model list and start a catalog entry from one of them. That
is a property of the platform, not a setting:

| Platform | Can be asked | Cataloguing alone reaches it |
|---|---|---|
| Google AI Studio | yes | yes |
| Self-hosted, OpenAI-compatible | yes | yes, on a server configured with at least one model |
| Microsoft Foundry / Azure | no | no — each model needs a **deployment** first |
| Google Vertex AI | no — it offers no list the credential may read | yes — under provider `vertex`, with its publisher and a region; `AIRA_VERTEX_MODELS` is optional |

A platform that cannot be asked is not misconfigured. Its models are named in the gateway's
configuration or typed into the catalog, and the editor's reachability check is what says whether
anything serves them. Nothing a vendor lists is ever catalogued automatically: a listing is not an
approval — only a model catalogued and approved here is served — a vendor's capability flag is a
claim rather than evidence, and no listing publishes a price.

### What the firewall must let out

Only the gateway calls a model platform; Management calls none. The gateway reaches, over HTTPS
unless a self-hosted server's URL says otherwise:

| Platform | Hosts |
|---|---|
| Vertex | `<region>-aiplatform.googleapis.com` per region in use; `aiplatform.eu.rep.googleapis.com` for the region `eu`; `aiplatform.googleapis.com` only for a model at `global` |
| Vertex, with a service account | `oauth2.googleapis.com` — the key's `token_uri` — for the short-lived token |
| AI Studio | `generativelanguage.googleapis.com`, or `AIRA_GEMINI_BASE_URL` |
| Foundry | the host of `AIRA_FOUNDRY_ENDPOINT` |
| OpenAI-compatible | each server's URL, as configured |

> done credentials in Vault ([§4a](#4a-hashicorp-vault)) or the environment — an `AIRA_*` value
> mounted as a file is read by nothing · regions on the allow-list · Claude only with a service
> account · the egress above · every model that will be served declared in the catalog with a
> **price** and its **capabilities**

---

## 6. Observability

OTLP over HTTP to any collector that accepts it without a credential: the applications' leg has one
AIRA setting, `AIRA_OTEL_ENDPOINT`, and nothing for a header or a CA. (The OpenTelemetry SDK reads
its own `OTEL_EXPORTER_OTLP_HEADERS` and `OTEL_EXPORTER_OTLP_CERTIFICATE`; nothing here sets or tests
them, and the shipped Compose passes neither.) Nothing is exported until `AIRA_OTEL_ENABLED=true`,
which is off by default. Traces carry `aira.*` attributes — subject, use case, model, outcome,
tokens, cost, provider/publisher/region — so a trace can be filtered by any of them.

`?key=` is redacted from spans; credentials never appear in a log line. `x-trace-id` is on every
gateway response while tracing is on, failures included, which are the ones most worth
correlating; Management sends none. The health probes are deliberately untraced (they were 20 of 86
spans in a measured minute), so `/healthz` and `/readyz` carry no id — an id that correlates with
nothing is worse than none, because somebody searches for it.

### Two channels, each configured on its own

This collector feeds **two independent channels**. Inside the collector they share a receiver and
nothing else — not a pipeline, not a batch window, not a destination — so each is configured without
touching the other.

**Both are downstream of one thing, and it is worth knowing which.** The applications export to
`AIRA_OTEL_ENDPOINT`; everything below is what the collector does with what arrives. If that hop is
wrong, *neither* channel receives anything, however well the second one is configured — the
delivery endpoint can be correct and the page still show nothing. `make otel-status`
answers that hop in one line, and `AIRA_DEBUG_INTEGRATIONS=otel` makes each export say where it
went and what answered.

| | **observability** | **delivery** |
|---|---|---|
| what it carries | every span, metric and log | one record per gateway API access, and per call to a model — each of them at `AIRA_OTEL_SAMPLE_RATIO=1.0`, whatever sampled flag a caller sends — and every log record |
| the question it answers | *was the gateway slow, or the model* | *who called what, and how did it end* |
| volume, measured | 347 spans on a demo run | **20** of those |
| filtered | no | traces, to requests and the calls made inside them; metrics and logs whole |
| on by default | yes, in the collector | no |
| prefix | `AIRA_OTEL_BACKEND_` | `AIRA_OTEL_FORWARD_` |
| shared by both | `AIRA_OTEL_ENABLED` and `AIRA_OTEL_ENDPOINT` — the hop *into* the collector | |

**Sampling is the installation's, not the caller's.** `AIRA_OTEL_SAMPLE_RATIO` is applied in the
applications, to each request's trace id. A caller's `traceparent` continues its trace id, and its
sampled flag decides nothing: `…-00` does not take a request out of the records, `…-01` does not
force one in. At `1.0`, the default, every request is kept. Below it, a share of the delivery
channel's records — refused attempts included — never reaches a SIEM; the share is picked by trace
id, which a caller can choose, so a caller who sends a high one is always among the dropped; and a
dropped request still answers with an `x-trace-id`, stored on its audit row, that names no trace.

**Only the trace context is read.** Both applications take a caller's `traceparent` and
`tracestate`, and nothing else a caller sends for tracing: a `tracestate` longer than 8192
characters is dropped unread — the trace id beside it still continues — and `baggage` is not read
at all. Nothing here uses baggage, reading it costs work before the request is authenticated, and
the HTTP client would otherwise carry a caller's baggage on to every upstream it calls. An
installation that wants other propagators names them in `OTEL_PROPAGATORS`, and that choice stands.

**The records are the gateway's.** Management marks no span with `aira.*`, so the control plane's
own accesses are not among them; its log records travel, as all logs do.

**It is a feed, not a store.** The collector queues in memory and gives a batch up after five
minutes of retrying (`undelivered` in `make otel-status`); a restart, or the recreate that every
change to its settings takes, loses what was queued. The record that is kept is the audit trail in
the database — and a request refused before it is attributed to a use case has no row there, so
for that one the span is all there is.

**Beyond the rows of that table, the two are configured alike, one prefix along.** The transport,
the encoding, the compression, the credential header, the CA, the client certificate, the batching,
the queue and the retry — a closed core: the same knobs on both channels, under the same names, and
no others. A destination that needs more is a **recipe**, a collector fragment in
`deploy/compose/otel/recipes/` selected through `…_PROTOCOL_CONFIG` or `…_AUTH_CONFIG`, with its own
values in `deploy/compose/otel/custom/collector.env`
([the seven things that vary](#attaching-an-otlp-consumer--the-seven-things-that-vary)).

| | |
|---|---|
| `…_CONFIG` | **whether**, and where — the channel's on/off switch |
| `…_PROTOCOL_CONFIG` | **how it is reached** — OTLP/HTTP, OTLP/gRPC, or a recipe |
| `…_AUTH_CONFIG` | **who we say we are** — nothing, a header, or a recipe |

Two rules make the two families readable together:

**`…_ENDPOINT` is the channel's default transport; the other transport has its own name.** It
reads backwards between them: observability defaults to gRPC, so its `_ENDPOINT` is `host:port` and
`_HTTP_ENDPOINT` is the extra; delivery defaults to HTTP, so its `_ENDPOINT` is a URL and
`_GRPC_ENDPOINT` is the extra. `_PLAINTEXT` follows its `_ENDPOINT`. Those two names are the only
difference between the families.

**The encoding is reachable over HTTP only.** OTLP/gRPC is protobuf by definition — the
specification defines no JSON over gRPC — so `…_ENCODING` does not reach a gRPC leg, and the
observability channel, which speaks gRPC by default, has an encoding to choose only once it is
switched to HTTP.

**Setting one up does not disturb the other.** The delivery fragment adds `traces/siem`,
`metrics/siem` and `logs/siem` and names no observability pipeline, so the first channel is
byte-for-byte the same whether forwarding is merged or not. And each has its own batch processor,
so `AIRA_OTEL_FORWARD_BATCH_SECONDS` retimes the delivery channel and leaves the trace backend's
timing alone.

**Channel 1, to your own trace backend** — the bundled Grafana is only the default:

```bash
AIRA_OTEL_BACKEND_ENDPOINT=tempo.internal:4317
AIRA_OTEL_BACKEND_PLAINTEXT=false        # it is not on this machine's network
AIRA_OTEL_BACKEND_CA_FILE=/etc/otelcol-contrib/ca/your-ca.crt   # if it is behind a private CA
```

…over OTLP/HTTP instead, which is what makes the encoding a choice — `json` sends OTLP/JSON, one
nested document per batch, to a backend or datalake that parses documents rather than OTLP:

```bash
AIRA_OTEL_BACKEND_PROTOCOL_CONFIG=/etc/otelcol-contrib/backend-http.yaml
AIRA_OTEL_BACKEND_HTTP_ENDPOINT=https://tempo.internal:4318
AIRA_OTEL_BACKEND_ENCODING=json          # or `proto`, which several managed endpoints require
AIRA_OTEL_BACKEND_COMPRESSION=none       # gzip | none | zstd | snappy
```

…with a credential in a header. Grafana Cloud, for one, takes HTTP basic — an instance id and an
access policy token — which is the header fragment with `Basic` and their base64 as the value:

```bash
AIRA_OTEL_BACKEND_AUTH_CONFIG=/etc/otelcol-contrib/backend-auth-header.yaml
AIRA_OTEL_BACKEND_AUTHORIZATION=Basic <base64 of 123456:token>
```

**Channel 2, to whoever consumes the access records** — the switch, then wherever it goes:

```bash
AIRA_OTEL_FORWARD_CONFIG=/etc/otelcol-contrib/forward.yaml
AIRA_OTEL_FORWARD_ENDPOINT=https://receiver.internal:4318
```

Recreate the collector after either. The seven axes below are written with the delivery channel's
prefix; every one of them applies to both.

**Both channels can be authenticated at once.** The base configuration owns the collector's
`service::extensions` list and starts both channels' header credentials there; a header fragment
attaches `auth:` to its own channel's two exporters and nothing else. A merged list **replaces**
rather than adds, so a fragment that named the list would leave only its own extensions running —
silently, with `otelcol validate` clean — and a recipe that brings an extension of its own, such as
`recipes/forward-oauth2.yaml`, restates the whole list, the base's entries included.

**A credential fragment selected without its values does not stop the collector: it sends the name
of the variable you forgot** — `AIRA_OTEL_BACKEND_AUTHORIZATION-is-not-set`, which
`make otlp-inspector` shows and the far end quotes back in its rejection.

### The two legs, and which one you can change

```
                                        ┌── your choice ──▶ your trace backend   (channel 1)
  gateway ─── application/x-protobuf ──▶ collector
  management        (fixed)             └── your choice ──▶ whoever consumes the records (channel 2)
                                             (protobuf or JSON, per channel)
```

**Leg 1, the applications to the collector, is protobuf and is not switchable.** Measured on the
running stack: `Content-Type: application/x-protobuf`. This is not a setting anybody withheld —
the OTLP spec defines an `http/json` protocol and `opentelemetry-exporter-otlp-proto-http`, the
package both planes use, does not implement it. `AIRA_OTEL_ENDPOINT` chooses *where*; nothing
chooses the encoding. If your destination cannot read protobuf, put a collector in front of it;
that is what the second leg is for.

**Leg 2 is yours — and there are two of it, one per channel**, each a variable rather than a file
you edit:

```bash
AIRA_OTEL_BACKEND_ENCODING=json     # channel 1, over HTTP: OTLP/JSON to your trace backend
AIRA_OTEL_FORWARD_ENCODING=proto    # channel 2, over HTTP: what Azure Monitor requires — see
                                    # "A worked routed destination" below
```

They are set independently and take effect independently: one channel can send `json` while the
other sends `proto`, into two receivers at once.

**Neither reaches a gRPC leg.** OTLP/gRPC is protobuf by definition — the specification defines no
JSON over gRPC — so on either channel the encoding is a property of the HTTP transport. Channel 1
speaks gRPC by default, which is why `AIRA_OTEL_BACKEND_PROTOCOL_CONFIG=…/backend-http.yaml` is the
first half of asking for JSON there.

Which one a receiver takes is not a detail: a receiver may refuse one or the other, and receivers
that matter here do — a conformant one may refuse JSON, Azure Monitor takes protobuf only, and a
backend that parses documents rather than OTLP wants JSON.
**Answer this before you wire anything up**; it is the single most likely reason a correctly
configured leg delivers nothing.

`otel/collector-forward.yaml` and `otel/collector-backend-http.yaml` are the worked examples,
merged on top of the reference configuration by `AIRA_OTEL_FORWARD_CONFIG` and
`AIRA_OTEL_BACKEND_PROTOCOL_CONFIG` — see
[*Attaching an OTLP consumer*](#attaching-an-otlp-consumer--the-seven-things-that-vary) below
([§0](#0-when-it-does-not-work-watching-the-wire) for the debug channel, `make otel-status` for
whether it arrived).

### What "OTLP/JSON" actually is, before you plan around it

Measured through the overlay above into a throwaway listener:

```json
{"resourceSpans": [{"resource": {"attributes": [{"key": "service.name",
 "value": {"stringValue": "aira-gateway"}}]}, "scopeSpans": [{"spans": [ … ]}]}]}
```

`Content-Type: application/json`, and the body is the **protobuf-JSON mapping**: one nested
document per batch, `resourceSpans[].scopeSpans[].spans[]`, with every attribute as a
`{"key": …, "value": {"stringValue": …}}` pair. It is **not** one flat event per line, and a SIEM
that ingests newline-delimited JSON will not read it as events without a transform. Answer that
question before wiring it up, and know what a SIEM receives: a span per request and per model call,
sampled, with the metrics and log lines whole — a feed, not the audit trail, which is the rows in
the gateway's database ([§1](#1-postgresql)).

**Under strict data mode spans and log lines name nobody.** `aira.subject`, and every person field
of a log line, carries `p-` and a keyed hash of the name instead: the same person is always the same
pseudonym, so a SIEM can still follow one caller, and nobody reads the name off it. The key is
`AIRA_PSEUDONYM_SECRET`, from Vault and shared by both planes; without it each process draws its own
and says so once, and a person's pseudonym then differs between processes. `aira.credential` (a key
prefix) and `aira.source_ip` stay, because an incident needs them — so who may query the tracing
backend and the SIEM is still that system's access control: grant it to IT Security. To learn who a
pseudonym is, follow the span's trace id to the request's audit row, which the incident roles read.
The shipped Grafana dashboard selects no caller, and an anonymous visitor there is a Viewer, who
cannot query spans ad hoc.

If a flat shape is what you need, the collector is where to produce it: this build ships the
`file`, `kafka`, `elasticsearch`, `opensearch`, `splunk_hec` and `syslog` exporters alongside
`otlphttp`, and those speak their destinations' own formats rather than OTLP.

### Did it arrive? Three hops, three answers

A service can only report what its own exporter saw, and that is less than it looks: OTLP defines
a **partial success** — the collector answers `200` with a body saying it dropped part of the batch
— and the Python exporter reads the status and throws the body away. So a green line can be about
telemetry that was discarded.

```
  application ──▶ collector ──▶ Grafana / your SIEM
     │               │                  │
     │               │                  └── make otel-status   forwarded / undelivered
     │               └── make otel-status   accepted / refused
     └── AIRA_DEBUG_INTEGRATIONS=otel      left, took Nms, answered 200, N rejected
```

```console
$ make otel-status
signal           accepted   refused  forwarded  undelivered
log_records             …         0          …            0
spans                   …         0          …            0
```

`forwarded` is summed across exporters, so a batch counts once for every exporter that sent it.
Each base pipeline of the reference stack has three — `otlp/backend`, `debug` and `file/arrived`,
which counts even while it writes to `/dev/null` — so there `forwarded` reads about three times
`accepted`, and more with the delivery channel on. `undelivered` counts a *give-up*, not
an attempt, so an exporter still retrying a dead endpoint reads `0` for as long as its backoff
lasts; `make otel-status` prints the collector's own words about why.

### Two destinations that want different things

The `otel` leg and a SIEM are not the same question, and treating them as one is what makes a
second destination a volume problem.

**Grafana wants everything** — SQL statements, pool connections, ASGI internals — because that is
how *"was the gateway slow or was the model slow"* gets answered. **A SIEM wants one record per
request**, and the calls that carried data outside this installation. Measured on the shipped
stack: three requests produced **184 spans**, of which **6** are those two things.

**They are two independent streams that share a receiver**, not a branch and its parent. The
forwarding fragment adds three pipelines of its own and names none of the base three:

```
                      ┌─ traces  · metrics  · logs       → Grafana + the inspection exporters
  applications ─▶ collector
                      └─ traces/siem · metrics/siem · logs/siem → your endpoint
                           └ filter      (whole)   (whole)
```

Each has its own batching (`batch/siem`), so `AIRA_OTEL_FORWARD_BATCH_*` reaches the stream it
names and no other. And because the fragment never names a base pipeline, **the observability
stream is byte-for-byte the same whether forwarding is on or off**: a fragment that named one would
have to restate its exporter list, because a merged list replaces, and an exporter left out would
silently unhook Grafana.

What the traces pipeline keeps: a span with `aira.use_case` (the request), a span with
`aira.outcome` (a request, including one refused before it could be attributed), or any span with a
URL and a parent — in practice an HTTP call made *inside* a request (the model the prompt actually
went to), and a server span whose caller sent a `traceparent`. A refused attempt carries what is
known: `aira.outcome` — `unauthenticated` (401, or 429 after too many failed attempts from one
address), `forbidden` (403, a use case the credential may not act on) or `unattributed` (a known
caller whose request no use case could be attributed to: 400, or KIRA's 403) — `aira.status`,
`aira.source_ip`, and for a known caller its `aira.subject`, `aira.auth_method` and
`aira.credential`, and the use case it asked for as `aira.use_case.requested` — never
`aira.use_case`, because it was not attributed to it. What it drops: SQL, pool connections, ASGI
send/receive halves, and the reachability prober — which asks every configured model every 60
seconds whether it is there and, kept, would be most of what is selected: **32 of 35 spans** in a
measured window, without one of them being a request.

Metrics and logs go over **whole** — they are small, and an `oidc_jwks_unavailable` is exactly what
a second destination is for. That is a decision made on their own pipelines, not a consequence of
sharing the base ones.

### It is off until you say so, and off means absent

Verified on the running stack rather than asserted:

| | |
|---|---|
| No `AIRA_OTEL_FORWARD_*` set | collector loads `config.yaml` + `empty.yaml` (an empty `{}`) |
| Exporters running | `otlp/backend`, `debug`, `file/arrived` — **no `otlphttp/forward`** |
| Containers | one collector, as always |
| `traces/siem` pipeline | does not exist |

`AIRA_OTEL_FORWARD_ENDPOINT` **alone does nothing** — measured. `AIRA_OTEL_FORWARD_CONFIG` is what
makes the fragment part of the configuration at all; the rest are read only once it is.

**And forgetting the endpoint does not break the stack.** One container carries every exporter, so
an exporter without an endpoint would fail validation and restart the collector for ever, taking
Grafana with it — with the reason only in the logs of a container nobody watches. The endpoint
therefore falls back to `http://forward-endpoint-not-set.invalid:4318` (RFC 2606 — never
resolves): the collector starts, that one exporter fails by name, and everything else keeps working.

The fallback is spelled twice, in `docker-compose.yml` and in the fragment, and under Compose the
first is the one that acts: Compose would pass an **empty string** for an unset variable, and an
empty string overrides a `${env:…:-default}` inside the collector. The fragment's own default
matters only where the variable is absent altogether, under another orchestrator.

### Attaching an OTLP consumer — the seven things that vary

**Written with the delivery channel's prefix, and true of both.** The observability channel has
the same names, so read `AIRA_OTEL_BACKEND_` for `AIRA_OTEL_FORWARD_` throughout if the destination
you are attaching is your trace backend. Two of the axes — a path per signal, and a credential other
than a header — are recipes rather than variables.

**The protocol is standard, which is not the same as "one variable reaches everything".** A
destination varies on seven axes, and each is a variable or a fragment:

| | variable | |
|---|---|---|
| **transport** | `AIRA_OTEL_FORWARD_PROTOCOL_CONFIG` | HTTP (default) or `…/forward-grpc.yaml` for gRPC — and a recipe for a destination that is not OTLP, such as Splunk ([below](#splunk-over-its-http-event-collector)) |
| **encoding** | `AIRA_OTEL_FORWARD_ENCODING` | `json` · `proto`. The spec makes protobuf **required** and JSON optional, so a conformant receiver may refuse JSON |
| **path** | a recipe | the collector appends `/v1/<signal>` to the endpoint; a receiver with a route in front of OTLP sets per-signal URLs in a recipe, as `recipes/forward-azure-monitor.yaml` does |
| **credential name** | `AIRA_OTEL_FORWARD_AUTH_HEADER` | `Authorization` is what a *minority* ask for |
| **credential kind** | `AIRA_OTEL_FORWARD_AUTH_CONFIG` | none · a header (HTTP basic is `Basic …` in it) · a recipe: OAuth2, a platform identity |
| **who we are** | `_CLIENT_CERT_FILE` / `_CLIENT_KEY_FILE` | mutual TLS |
| **compression** | `AIRA_OTEL_FORWARD_COMPRESSION` | `gzip` · `none` · `zstd` · `snappy` |

Full reference: [`CONFIGURATION.md` §5a](CONFIGURATION.md). Three worked shapes:

**A plain OTLP receiver** — most of them. One variable:

```bash
AIRA_OTEL_FORWARD_CONFIG=/etc/otelcol-contrib/forward.yaml
AIRA_OTEL_FORWARD_ENDPOINT=https://otel.internal:4318
```

**One that wants an API key and gRPC:**

```bash
AIRA_OTEL_FORWARD_CONFIG=/etc/otelcol-contrib/forward.yaml
AIRA_OTEL_FORWARD_PROTOCOL_CONFIG=/etc/otelcol-contrib/forward-grpc.yaml
AIRA_OTEL_FORWARD_GRPC_ENDPOINT=otel.vendor.example:4317
AIRA_OTEL_FORWARD_AUTH_CONFIG=/etc/otelcol-contrib/forward-auth-header.yaml
AIRA_OTEL_FORWARD_AUTH_HEADER=x-api-key
AIRA_OTEL_FORWARD_AUTHORIZATION=…
```

**One behind your identity provider** — Keycloak, Okta, Auth0, Ping, Entra, or anything that
implements RFC 6749 §4.4. The collector fetches the token and refreshes it, so what is configured is
a long-lived secret rather than a token that stops working during the afternoon. That is a recipe:

```bash
AIRA_OTEL_FORWARD_AUTH_CONFIG=/etc/otelcol-contrib/recipes/forward-oauth2.yaml
# in deploy/compose/otel/custom/collector.env:
OAUTH_TOKEN_URL=https://kc.internal/realms/ops/protocol/openid-connect/token
OAUTH_CLIENT_ID=aira-collector   # a confidential client of its own, not the console's
OAUTH_CLIENT_SECRET=…            # → Vault
OAUTH_SCOPES=["telemetry.write"]
```

**With no auth fragment selected, no credential header is sent at all** — not an empty
`authorization: ''`, which any receiver that parses it answers with `400`.

**A mistyped value stops the collector rather than degrading it**, unlike a missing endpoint, and
the difference is deliberate for the one measured: `…_ENCODING`. That is one of two words somebody
has just typed on purpose, in the same minute as the endpoint it belongs to; an *endpoint* is what
gets forgotten while configuring something else, so that one keeps its harmless fallback. The other
typed values — a compression name, a size, a duration, a switch — are read at start-up as well;
treat a typo in any of them the same way. `make otel-status` is what tells you the collector is not
running.

**A credential fragment selected with nothing in its variables is not one of them.** The collector
starts and sends the **name of the variable you forgot**:
`AIRA_OTEL_FORWARD_AUTHORIZATION-is-not-set`, which `make otlp-inspector` shows and the far end
quotes back in its rejection.

#### Splunk, over its HTTP Event Collector

Splunk Enterprise and Splunk Cloud Platform take events over HEC, which is not OTLP: one JSON event
per span, log record or metric data point, rather than one nested document per batch with the
attributes three levels down. `recipes/forward-splunk-hec.yaml` does that: it is selected in the
transport slot the way gRPC is and sits behind the same filter and batching, so Splunk receives what
the OTLP leg would have sent, one record per request and per model call:

```bash
AIRA_OTEL_FORWARD_CONFIG=/etc/otelcol-contrib/forward.yaml
AIRA_OTEL_FORWARD_PROTOCOL_CONFIG=/etc/otelcol-contrib/recipes/forward-splunk-hec.yaml
# in deploy/compose/otel/custom/collector.env:
SPLUNK_HEC_ENDPOINT=https://splunk.internal:8088/services/collector
SPLUNK_HEC_TOKEN=…                      # → Vault
SPLUNK_HEC_INDEX=aira                   # the request records and logs
SPLUNK_HEC_METRICS_INDEX=aira_metrics   # a metrics index
```

What arrives is one event per span, log record or metric data point. A model call, as
collector-contrib 0.157.0 sent it, trimmed:

```json
{"event": {"trace_id": "ea97…", "span_id": "e250…", "parent_span_id": "b717…", "name": "POST",
           "kind": "SPAN_KIND_CLIENT", "start_time": 1789293896708285333, "end_time": …,
           "attributes": {"aira.use_case": "kundenservice", "aira.model": "gemini-2.5-flash",
                          "aira.model_call.purpose": "serve", "aira.subject": "…",
                          "http.status_code": 200}},
 "fields": {"service.name": "aira-gateway", "deployment.environment": "local", …},
 "source": "aira", "sourcetype": "aira:otel", "time": 1789293896.708}
```

The attributes are a flat object in the event; the resource attributes are in `fields`, which HEC
indexes as fields of their own.

What Splunk has to provide:

- **A HEC token** whose allowed indexes include both of those. It is the credential on this leg,
  sent as `Authorization: Splunk …`, so no `…_AUTH_CONFIG` fragment applies.
- **A metrics index for the metrics.** `mstats` reads nothing else, and one index cannot be both
  kinds — which is why the recipe has two exporters that differ in the index and in nothing else.
- **`KV_MODE = json` for the sourcetype** (`aira:otel` unless `SPLUNK_HEC_SOURCETYPE` says otherwise).
  The span attributes are then search-time fields — `attributes.aira.use_case`; without it,
  `spath` extracts them per search.

`_ENCODING` and `_COMPRESSION` do not reach this leg: HEC is JSON, and the exporter gzips, which HEC
accepts. `make otlp-inspector` accepts HEC too, at `http://otlp-inspector:4318/services/collector`,
so the leg can be checked before a Splunk exists. For an installation whose trace backend is
Splunk, copy the recipe to `custom/`, rename its exporters to `…/backend` and name the base
pipelines, keeping `debug` and `file/arrived` in each (`recipes/README.md`).

#### And the other direction: sending **to** this installation

Nothing needed. The collector accepts OTLP on 4317 (gRPC) and 4318 (HTTP), in protobuf **or** JSON,
gzipped or not — verified with a hand-written OTLP/JSON document posted from outside the stack
(`200`, and the span through the pipeline; a malformed one answers `400`). Any conformant OTel
producer — another team's service, a sidecar, a third-party agent — can point at it and its spans
join the same pipelines, filters and destinations as AIRA's own.

**Which is the one thing to restrict.** The receiver takes OTLP from anybody who reaches it, with no
credential, and the delivery filter reads attributes, not senders: a span posted with
`aira.use_case` or `aira.outcome` is forwarded as an access record like any other. Keep 4317 and
4318 on a network only your producers reach. The Compose stack publishes them on loopback, and
`AIRA_BIND_HOST=0.0.0.0` publishes them along with everything else.

#### A worked routed destination: Azure Monitor, and therefore Microsoft Sentinel

An example of what a recipe is for rather than a supported product. Sentinel reads a Log Analytics
workspace; telemetry gets in through Azure Monitor's OTLP ingestion — an endpoint on a Data
Collection Endpoint, routed by a Data Collection Rule, with one URL per signal, metrics on another
host, and protobuf only. `recipes/forward-azure-monitor.yaml` sets all of that and authenticates
with the collector's managed identity, which needs no secret:

```bash
AIRA_OTEL_FORWARD_CONFIG=/etc/otelcol-contrib/forward.yaml
AIRA_OTEL_FORWARD_PROTOCOL_CONFIG=/etc/otelcol-contrib/recipes/forward-azure-monitor.yaml
# in deploy/compose/otel/custom/collector.env:
AZURE_MONITOR_TRACES_ENDPOINT=https://<dce>.<region>-1.ingest.monitor.azure.com/dataCollectionRules/<dcr>/streams/Microsoft-OTLP-Traces/otlp/v1/traces
AZURE_MONITOR_LOGS_ENDPOINT=https://<dce>.<region>-1.ingest.monitor.azure.com/dataCollectionRules/<dcr>/streams/Microsoft-OTLP-Logs/otlp/v1/logs
AZURE_MONITOR_METRICS_ENDPOINT=https://<dce>.<region>-1.metrics.ingest.monitor.azure.com/dataCollectionRules/<dcr>/streams/Custom-Metrics-Otel/otlp/v1/metrics
```

A collector outside Azure has no managed identity: select `recipes/forward-oauth2.yaml` in
`AIRA_OTEL_FORWARD_AUTH_CONFIG` as well, against Entra
(`OAUTH_TOKEN_URL=https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token`,
`OAUTH_SCOPES=["https://monitor.azure.com/.default"]`); it is merged later and replaces the
identity. Azure-side work this repository cannot do: the DCE, the DCR pointing at the workspace, and
**Monitoring Metrics Publisher** granted on that rule to the collector's identity.

**Metrics will want one more step.** Application Insights expects delta temporality and exponential
histograms; `opentelemetry-python` produces cumulative with explicit buckets. The
`cumulativetodelta` processor is in the collector image and is not wired up — it would change what
the Grafana leg receives too, so it belongs to whoever has the workspace open.

### When the receiver answers `429`

Measured on this stack, so the numbers are a starting point rather than a rule of thumb:

| | |
|---|---|
| Spans one gateway request produces | **17.5** (server span, httpx client spans, SQLAlchemy spans) |
| Applications → collector | spans every **5 seconds**, up to **512** in one POST; logs every second; metrics every minute — the SDK's defaults, and a signal with nothing queued sends nothing |
| At idle | ~6 POSTs/min per service; the metrics reader adds one per minute |

**The request count is governed by a timer, not by your traffic** — until a batch fills. That is the
part worth taking in before you tune anything, because it decides which lever helps:

- A receiver limiting **requests per second**: lower the request count. Sampling will not do it —
  measured, `AIRA_OTEL_SAMPLE_RATIO=0.25` took spans from 17.5 to 5.9 per request and left the POST
  count unchanged at 6. Batch harder instead (below).
- A receiver limiting **volume or ingested events**: `AIRA_OTEL_SAMPLE_RATIO` is the lever, and it
  scales roughly linearly. It applies to traces only; logs and metrics are unaffected. **It thins
  the delivery channel as well**, whose records are spans: below 1, a share of the access records
  — refused attempts included — never reaches a SIEM. Where that channel is the record of every
  access, leave the ratio at 1.0 and batch instead.

The collector's own defaults are the wrong shape for a rate limit — a batch every **200 ms**, from
**ten concurrent senders** — and a `429` is retryable, so refusals come back and tighten the loop.
Four variables change that on the forwarding leg:

```bash
# in deploy/compose/.env — hold telemetry this long, put this much in one request,
# send one at a time, and do not ask again before the window has moved:
AIRA_OTEL_FORWARD_BATCH_SECONDS=10s
AIRA_OTEL_FORWARD_BATCH_SIZE=8192
AIRA_OTEL_FORWARD_CONSUMERS=1
AIRA_OTEL_FORWARD_RETRY_INITIAL=5s
```

Those are the delivery channel's defaults as the stack ships it. Measured against a counting
listener, 40 gateway requests: **8 HTTP requests carrying 696 spans** (87 per request), against
**27 carrying 1399** (52 per request) with the collector's own defaults — about 40 % fewer requests
for the same telemetry, and sequential rather than in bursts of ten.

`make otel-status` shows whether anything is being dropped while you tune.

### Seeing what *leaves*, before a SIEM exists

`make otel-arrivals` and `AIRA_OTEL_ARRIVED_FILE` below are what **arrived** at the collector,
before the SIEM filter and before anything is forwarded. That is a different question from what
goes out on the forwarding leg — where the filter, the encoding and the credential all take effect
— and the inspector below gives that leg something to point at while you are still deciding.

```bash
make otlp-inspector      # a receiver that stands in for your SIEM, with a page in front of it
```

Then two variables and a recreate:

```bash
AIRA_OTEL_FORWARD_CONFIG=/etc/otelcol-contrib/forward.yaml
AIRA_OTEL_FORWARD_ENDPOINT=http://otlp-inspector:4318
```

The page lists the last few hundred batches: each span on one row with its `aira.*` attributes, the
content type and encoding actually used, the sizes on the wire and after gunzip, and **whether a
credential was on the request** — its scheme and length, never its value. Protobuf batches are
counted and labelled rather than decoded, which is enough to answer *is it going out and is it
authenticated*; with `AIRA_OTEL_FORWARD_ENCODING=json`, the default, the contents are readable.

**Two addresses, and confusing them is the commonest way this ends in an empty page.** Inside the
stack's network the receiver is `otlp-inspector:4318` — a Docker name and the *container's* port,
both of which exist only on the machine running the stack. From anywhere else it is that host on
the **published** port, and the same port serves the page and OTLP:

| the collector is… | `AIRA_OTEL_FORWARD_ENDPOINT` |
|---|---|
| on the same machine as the inspector | `http://otlp-inspector:4318` |
| anywhere else | `http://<the-inspector's-host>:4319` |

So a browser on another machine and a collector on another machine want the *same* URL — the page
and `/v1/{traces,logs,metrics}` share it. Reaching it from elsewhere at all needs
`AIRA_BIND_HOST=0.0.0.0` **and** `AIRA_BIND_HOST6=::`, for the reason
[`.env.example`](../deploy/compose/.env.example) gives under *reaching this stack from another
machine*: Docker opens one socket per published entry, so setting only the first leaves every IPv6
caller reaching a machine with nothing listening.

`make otlp-inspector` prints both addresses and then reads the collector's own arguments to say
whether anything is currently pointed at it — an empty page looks identical whether the collector
was configured, configured wrongly, or not configured at all.

It is a debugging tool: in memory, capped, lost on restart, unauthenticated, and it holds
attribution — a developer's machine, and nowhere else. `make otlp-inspector-down` stops it and
forgets what it held.

### Seeing what *arrives* at the collector

**The comfortable way is already running.** `make up` starts Grafana with Tempo, Loki and
Prometheus behind it, and the collector forwards everything it receives to them — browsable at
<http://localhost:3000> → *Explore* → *Tempo*, with all its attributes. What it receives depends on
one switch: the applications export only with `AIRA_OTEL_ENABLED=true`, which is off by default —
set it in `deploy/compose/.env` for the containers; `make run-gateway` and `make run-backend` set it
for a process run from source.

The fastest route to one request: every gateway response carries an `x-trace-id` header while
tracing is on, failures included, and pasting it into Tempo's *TraceID* search resolves at once (a
tag search takes about a minute to index). What you get is the request span with its `aira.*`
attributes — subject, credential, use case, model, outcome, status, tokens, cost — and everything
underneath it: the model call, the SQL statements, the pool connections.

```
POST /v1beta/models/{resource}
  aira.subject = ucadmin        aira.outcome      = served
  aira.use_case = kundenservice aira.status       = 200
  aira.model = qwen3:0.6b       aira.total_tokens = 53
  aira.credential = dd533902    aira.cost_nanos   = 15800
  aira.source_ip = 172.19.0.1   aira.api.surface  = gemini
```

**No prompt and no response** — those never reach a span; they are stored on the audit
row and read through the console, where every read is recorded. The `trace_id` is the same in both,
so a span in Tempo and its stored payload are one lookup apart.

**The model call underneath it says the same things about itself.** A served request
produces two records the delivery channel forwards — the API access above and the model access
below — and the second one is a record in its own right rather than a detail of the first:

```
POST                                       (scope: instrumentation.httpx)
  aira.subject = admin            aira.model              = qwen3:0.6b
  aira.use_case = personalwesen   aira.model_call.purpose = serve
  aira.credential = 888a5742      http.status_code        = 200
  aira.auth_method = api_key      http.url = http://ollama:11434/v1/chat/completions
```

That matters to a **consumer that does not join spans**. A tracing backend would show the API
access one click above and needs none of this; a SIEM filters flat events by field, and without
these attributes the model access would say only *somebody POSTed to a host and got a 200* — not
even which model, since an OpenAI-compatible upstream carries that in the request body.

`aira.model_call.purpose` is one of five: `serve` (answering a caller), `pipeline` (a classifier or
redactor the pipeline ran), `embed`, `rerank` (a ranking request), `probe` (an operator's thinking
check, `:checkThinking`). A
record is one HTTP call: a fallback chain marks **each attempt**, so a chain that tried three models
leaves three records naming three models, and a regional failover inside one attempt leaves one per
region it tried.

Three calls are not what that suggests:

- With a Vertex service account, the token exchange with `oauth2.googleapis.com` — about once an
  hour — can happen inside a model call, and is then stamped as that model access.
- An operator's reachability check (`:check`) and the count after a stream the caller left
  (`:countTokens`, `AIRA_COUNT_CANCELLED_STREAMS`, on by default) carry no `aira.*` attributes, and
  still pass the filter as calls made inside a request. The second sends the prompt and the answer
  to the vendor a second time.

Outgoing calls the gateway makes for its own reasons carry **none** of these attributes,
deliberately — they are not model accesses and a consumer must not count them as such: the
reachability prober's calls are roots, which the filter drops, and a JWKS refresh or a Vault read
leaves no span at all.

The two ways below are for when you need the bytes rather than a view of them — a receiver that
will not parse something, or a question about the wire itself.

The collector already prints every batch it receives — `make otel-arrivals`, which is
`docker logs -f <stack>-otel-collector`. `AIRA_OTEL_DEBUG_VERBOSITY` decides how much:

| | |
|---|---|
| `basic` | counts only — "3 spans", nothing about them |
| `normal` | one line per span: name, trace id, span id, attributes (**the default**) |
| `detailed` | every attribute, event, link and resource field, indented, as below |

```
Span #0
    Trace ID       : 95fb847b00318cfb108ff3eee0d616b6
    ID             : 95797a75c0c57fe9
    Name           : POST /v1beta/models/{resource}
    Kind           : Server
Attributes:
     -> http.scheme: Str(http)
```

And the same arrivals as **OTLP/JSON**, to parse rather than read — one document per line, all
three signals:

```bash
# in deploy/compose/.env, then recreate the collector
AIRA_OTEL_ARRIVED_FILE=/payload/arrived.json
# then: deploy/compose/payload/arrived.json
```

Measured on the shipped stack: 22 lines, none broken, carrying `resourceSpans`, `resourceLogs`
**and `resourceMetrics`** — metrics included, which matters because they are the hardest absence to
notice: a metric that never arrives looks exactly like one whose value did not change.

`/dev/stdout` works for a quick look and mangles anything large — Docker's json-file driver cuts at
a 16 KiB boundary. A file under `deploy/compose/payload/` comes out whole.

**The health probes are not in there.** Docker asks every 15 seconds per container and each ask
was three spans plus its database reads — measured at 23 % of everything in a window that also held
three real requests. They are excluded from tracing (`OTEL_PYTHON_EXCLUDED_URLS`, set before
anything is instrumented), so a trace backend, an OTLP quota and a debug payload all stop being
mostly probes. Only the exact paths `/healthz` and `/readyz` are excluded: the instrumentations
match the whole URL, whose host is the caller's own `Host` header, so a host or a use case whose
name merely contains the word stays traced. An installation that sets that variable itself keeps
its own list — and should anchor it the same way.

**A file the collector cannot write is reported as delivered.** It runs as uid 10001 and your
checkout does not, so `deploy/compose/payload/` has to be writable by it — `make env` sets that,
and every `up*` target depends on `env`. If you point `AIRA_OTEL_ARRIVED_FILE` somewhere else,
check that the collector can create it: nothing will say otherwise, and `otel-status` will count
the batches as sent. And do not `rm` the file while the collector runs — it keeps writing to an
inode nobody can see any more.

**This is what arrived, not what was forwarded.** The two are different questions and a green log
cannot tell them apart; `make otel-status` answers the second.

### Seeing the payload itself

Two places, and they show different things.

**What this gateway produces** — `AIRA_DEBUG_OTEL_PAYLOAD=<n>` prints the first *n* items of every
batch as OTLP/JSON on the service's own stdout, one payload per line:

```bash
docker logs aira-gateway \
  | jq -Rr 'fromjson? | select(.event=="otel_payload" and .signal=="traces") | .payload' \
  | tail -1 | jq .
```

Two things that bite:

- **`jq -R … fromjson?`, not plain `jq`.** `docker logs` carries the web server's own access lines,
  which are not JSON; plain `jq` stops at the first of them and prints nothing at all.
- **The first *n* is literally the first *n*.** A batch holds whatever ended in its window — SQL
  statements and pool connections as well as the request — so a small *n* can show you none of the
  span you came for. `=40` is a usable number on this stack; raise it until that span is in there.

Rendered to match a collector's OTLP/JSON exactly — hex identifiers, integer enums, measured
against one. It is *not* the bytes on that leg, which are protobuf.

**What your endpoint receives** — not the arrivals file above, which is what *arrived*: unfiltered,
batched for the first channel, and always JSON. The forwarding leg is where the SIEM filter, the
encoding and the credential take effect, and what it sends is what `make otlp-inspector` shows
([*Seeing what leaves*](#seeing-what-leaves-before-a-siem-exists)): leave
`AIRA_OTEL_FORWARD_ENCODING` at `json`, its default, and each batch is readable there, with
`/batch/<n>/raw` the body as sent, after gunzip — `AIRA_OTEL_FORWARD_COMPRESSION=none` makes those
the bytes on the wire.

`AIRA_OTEL_ARRIVED_FILE=/dev/stdout` works for a quick look at the arrivals (`make otel-arrivals`),
but **stdout mangles a large payload**: measured, Docker's json-file driver cuts at a 16 KiB
boundary and a 49 692-character document came back truncated at 48 KiB and would not parse, while
everything under ~8 KiB was intact. A file in `deploy/compose/payload/` comes out whole, however
big. The file is batched with the first channel, so for one span per line while you are reading,
set `AIRA_OTEL_BACKEND_BATCH_SIZE=1` — which re-batches the trace backend's leg as well;
`AIRA_OTEL_FORWARD_BATCH_SIZE` reaches only the delivery channel.

What a span carries, from a real request on the shipped stack:

```
POST /v1beta/models/{resource}   traceId=4a57ae30937c9637d39df1d79b5b4b56
  aira.api.surface        = gemini            aira.outcome  = served
  aira.auth_method        = api_key           aira.source_ip = 172.19.0.1
  aira.credential         = dd533902          aira.status   = 200
  aira.model              = qwen3:0.6b        aira.subject  = ucadmin
  aira.operation          = generateContent   aira.use_case = kundenservice
  aira.upstream.provider  = local             aira.upstream.region = 
```

**No prompt and no response** — those never reach a span. `aira.subject`,
`aira.credential` and `aira.source_ip` do, which is worth knowing before you forward this
anywhere.

### Not the same switch

`AIRA_LOG_JSON` is unrelated: it is whether **the service's own log lines on stdout** are JSON or
human-readable text. It does not touch OTLP. `AIRA_LOG_JSON=false` is for reading a terminal during
development; leave it `true` anywhere something collects container output.

> done an OTLP endpoint that takes the applications' export without a credential ·
> `AIRA_OTEL_ENABLED=true` · a sample ratio you can afford — `1.0` where the delivery channel is the
> access record · the receiver's ports reachable by your producers only · the encoding decided on
> the collector, not in AIRA

---

## 7. The SPA is configured at deployment time

**One image, any realm.** The console image writes `runtime-config.js` at every container start,
from the environment, and the bundle loads it before the app — so pointing the console at your
Keycloak is two variables and never a rebuild:

```js
// written at start from AIRA_OIDC_ISSUER and AIRA_OIDC_CLIENT_ID
window.__AIRA_CONFIG__ = {
  issuer: 'https://sso.example.com/realms/aira',
  clientId: 'aira-gateway',
};
```

The file is overwritten at each start, so mounting one is not the way to configure it: a writable
mount is replaced, and a read-only one — a `ConfigMap` volume — cannot be written at all. An unset
`AIRA_OIDC_ISSUER` is not refused either: the image writes `http://localhost:8080/realms/aira`, and
the console, which cannot reach that from a browser elsewhere, says so and names it. A misdirected
console does not fail at all — it sends people to a real login page at the wrong realm.

At run time the image reads:

| Setting | Is |
|---|---|
| `AIRA_OIDC_ISSUER` | your realm's issuer, as browsers reach it — the same value Management checks tokens against |
| `AIRA_OIDC_CLIENT_ID` | the console's public client; `aira-gateway` when unset |
| `AIRA_MANAGEMENT_UPSTREAM` | where `/api` goes; `http://management:8002` when unset |
| `AIRA_GATEWAY_UPSTREAM` | where `/gw` goes; `http://gateway:8001` when unset |
| `AIRA_CSP_CONNECT_SRC` | where the console may connect; when unset, `'self'` and the issuer's origin |
| `AIRA_DNS_RESOLVER` | the resolver nginx asks for the two upstreams; `127.0.0.11`, Docker's, when unset |

**Leave `AIRA_CSP_CONNECT_SRC` unset unless you need a further origin.** Derived from the issuer, it
names the one host the console calls cross-origin — the token request goes to Keycloak — and an
issuer the policy does not name produces a login that fails in the browser and nowhere else. A value
you set replaces the derived one, so it names `'self'` and the issuer's origin itself.

**The resolver must be one the container can ask.** nginx re-resolves the upstream names (holding an
answer at most ten seconds), so a gateway restarting behind a new IP is found again instead of
answering 502 until nginx restarts. `127.0.0.11` exists only on a Docker network: in Kubernetes set
the cluster's DNS, e.g. `kube-dns.kube-system.svc.cluster.local`, and name the upstreams fully,
since nginx's resolver applies no search domains.

nginx sets the console's security headers — a content policy, `nosniff`, `DENY` framing,
`Referrer-Policy: strict-origin-when-cross-origin`, a `Permissions-Policy` — from
`management/frontend/deploy/aira-headers.inc.template`, on the page and its routes. None of them is
HSTS: that belongs to the TLS proxy in front ([§8](#8-reverse-proxy-and-tls)). The bundle carries
only a looser content policy of its own, in `index.html`, which leaves connections open so the same
build works under `ng serve`; the browser enforces both, so the header decides where the console
may connect. `requireHttps: 'remoteOnly'` in the OIDC client keeps `http://localhost` working and
demands HTTPS everywhere else.

> done `AIRA_OIDC_ISSUER` (and the client id, if not `aira-gateway`) on the console's container ·
> both upstreams · a resolver the container can reach · `AIRA_CSP_CONNECT_SRC` only for an extra
> origin · TLS in front

---

## 8. Reverse proxy and TLS

Terminate TLS in front of both APIs. Four things AIRA needs from the proxy:

- **`X-Forwarded-For`**, appended, and a hop count on both planes that matches it — see below.
  Without them every audit row names your proxy, and every caller shares one failed-attempt limit.
- **HSTS, sent by the proxy.** No AIRA component sends it as shipped: the gateway leaves it to the
  proxy, and Management sends it only on a request it sees as HTTPS — which, behind the console's
  nginx, it never does, because that nginx sets `X-Forwarded-Proto` to its own scheme, `http`.
- **No buffering on streaming responses.** `:streamGenerateContent` (SSE with `?alt=sse`, a streamed
  JSON array without) and `/kira/api/external/streaming-chat` (SSE) stream their answers; a proxy
  that buffers them turns a stream into a single late response. In nginx that is
  `proxy_buffering off` — the shipped SPA proxy sets it on `/gw`, and it is the default that bites,
  not an exotic setting.
- **Keep the query string out of your access log**, or turn the log off for the gateway. Every
  Gemini client may authenticate with `?key=<api key>`: that is the wire protocol, not a choice
  AIRA makes. The gateway redacts it from its own logs and from exported spans, and sends
  `Referrer-Policy: no-referrer` so it does not leak onward — but a proxy in front logs the request
  line by default, and that line contains the credential.

Timeouts should exceed your slowest model. A self-deployed model cold-starting can take minutes,
which is why its own timeout defaults to 300 seconds (`AIRA_OLLAMA_TIMEOUT_SECONDS`, for every
OpenAI-compatible server); Vertex and Foundry wait 120 (`AIRA_VERTEX_TIMEOUT_SECONDS`,
`AIRA_FOUNDRY_TIMEOUT_SECONDS`). The shipped console proxy allows 310 seconds on `/gw`.

**A stream needs no longer read timeout than its keepalive.** A model reading a large context or
thinking says nothing for minutes; a streamed answer therefore sends its status within
`AIRA_STREAM_KEEPALIVE_SECONDS` (15) and something every 15 seconds while the model is quiet, so an
idle timeout above that — nginx's default 60 seconds, a load balancer's 30 — never closes it.
Keep that setting below the shortest idle timeout between your callers and the gateway. A
**non-streamed** answer has nothing to send before its status: there the read timeout of every
proxy on the way has to exceed the longest answer, or the caller's request is cut off while the
model still works — and a client that works with large contexts should stream.

### The caller's address, through your proxies

**The gateway.** `AIRA_TRUST_FORWARDED_FOR=true` makes it read the caller's address from
`X-Forwarded-For`, and `AIRA_TRUSTED_PROXY_HOPS` says how many proxies append to it (**1** for a
single nginx, which is what this repository ships). Leave the first off without a proxy you trust:
with it on, any caller could write any address into your audit trail.

The address is read that many entries **from the right**. The left end is whatever the *caller*
sent: a proxy appends, so `X-Forwarded-For: 10.9.9.9` from a client arrives as
`10.9.9.9, <real address>`. Getting this wrong is not cosmetic — the value lands on every audit row,
it is what the console's request list filters by when somebody investigates an incident, and it is
the key the failed-authentication bound counts against, so a caller who could choose it could also
rotate it and never be bounded. A chain **shorter** than the hop count is treated as not having come
through those proxies at all, and the socket peer is used instead.

**Management** has no switch: it always reads `X-Forwarded-For` by its own `AIRA_TRUSTED_PROXY_HOPS`
(default 1; `0` takes the socket peer), and the Compose files set that for the gateway only. It
keys its failed-authentication limit on the result — and an address over that limit is refused for
every request, valid tokens included, so a count too low puts every user behind one address and
lets one prober lock them all out. A chain shorter than the count gives its leftmost entry, which
may be one the caller wrote. A count above 0 also makes Management believe `X-Forwarded-Proto`.

**Count every proxy that appends on the way in**, on both planes: too low, and the address is a
proxy's; too high, and it is one the caller wrote. The console's nginx is one: behind a TLS proxy
of yours, a request through `/api` or `/gw` has passed two — the count Management needs — while an
API caller that reaches the gateway through its own ingress passes only yours.

### A private certificate authority

Each outgoing connection trusts what its library trusts, and they do not share one store:

| Connection | Trusts by default | To trust your CA |
|---|---|---|
| Vault, the directory, every model platform (`httpx`) | the `certifi` bundle installed with the application, **not** the system store | `SSL_CERT_FILE` (a bundle) or `SSL_CERT_DIR` |
| The JWKS fetch (`PyJWT`, through Python's `urllib`) | the system store | the system store, or `SSL_CERT_FILE` / `SSL_CERT_DIR` |
| Kafka | the system store | `AIRA_KAFKA_SSL_CAFILE` — and then that file only |
| The applications' OTLP export to the collector (`requests`) | the `certifi` bundle | the OpenTelemetry SDK's own `OTEL_EXPORTER_OTLP_CERTIFICATE`, or `REQUESTS_CA_BUNDLE`; `SSL_CERT_FILE` does not reach it, and AIRA sets neither |
| The collector to your destinations | the collector image's store | `AIRA_OTEL_BACKEND_CA_FILE`, `AIRA_OTEL_FORWARD_CA_FILE` ([§6](#6-observability)) |

So a CA added only to the operating system's store makes the JWKS fetch work and leaves Vault, the
directory and the model platforms refusing the certificate. `SSL_CERT_FILE` **replaces** the
bundle rather than adding to it: point it at a file that holds your CA *and* the public roots, or
the public model platforms stop verifying instead.

> done TLS · HSTS at the proxy · `X-Forwarded-For` appended, and the hop count set on **both**
> planes · SSE unbuffered · generous timeouts · a private CA in `SSL_CERT_FILE` on both planes, not
> only in the system store

---

## 9. What a first integrated deployment looks like

```mermaid
graph TB
    subgraph day1["Day 1 — it runs"]
        a1["Postgres: 2 databases"]
        a2["Keycloak: realm, role groups, clients"]
        a3["Kafka: 9 compacted topics"]
        a4["1 model platform"]
        a5["Deploy: gateway, consumer, management, relay, frontend"]
    end
    subgraph day2["Day 2 — it is governed"]
        b1["Redis: real rate limits across replicas"]
        b2["Model catalog: prices + capabilities"]
        b3["Retention worker: scheduled"]
        b4["OTLP: traces"]
        b5["Vault: credentials out of the environment"]
    end
    subgraph day3["Day 3 — it is defensible"]
        c1["Budgets and rate limits per use case"]
        c2["Anomaly rules, in <b>alert</b> first"]
        c4["Promote rules to throttle / block"]
    end
    day1 --> day2 --> day3
```

The order is not arbitrary. **Prices before budgets** — where a cost limit binds, a model with no
price is passed over and a request left with nothing priced is refused
(`AIRA_ENFORCE_PRICED_MODELS`, on by default), so a budget set before the prices stops
traffic; elsewhere unpriced traffic is counted apart rather than as zero. **Alert before block** —
a rule is a hypothesis until somebody has watched it be right, and a detection system that blocks
wrongly once is switched off forever. **Every caller names a use case from the start** —
`AIRA_REQUIRE_USE_CASE` is on by default and refuses to be switched off anywhere but `local`, so
migrating callers is the first day's work, not the last.

### Groups for the question catalogue

The question catalogue, a list of test prompts, is run **against a use case's own pipeline**, and a
run is that use case's traffic. Two things have to be true of the person running it, and the first
is the one people look for a setting for:

1. **The gateway would accept them for the use case** — decided exactly as for an ordinary API
   request: from the groups their token carries, or a grant naming them.
2. **They administer that use case**, or they hold `smoketest.run_any` — Global Administrator and
   IT Security by default, and any role it is granted to; administering every use case in
   Management (`usecase.manage_all`) does not count here. A plain use-case *user* may call the use
   case all day and may not put a hundred questions to it: that spends the use case's budget and
   reads a catalogue stating what this installation tests for.

Three consequences worth knowing before you look for a missing setting:

- **Seeing a use case is not calling one.** An oversight role sees every use case and
  is offered a Run button for none of them, by design — create a group whose path is
  `/use-cases/<slug>`, or grant an existing group to that use case in the console, which works the
  same way.
- **Calling one is not administering one.** Administration comes from a group grant or a membership
  with the `admin` role, both set in the console; the token carries no notion of it. Somebody who
  should be able to test their own pipeline and cannot is almost always a `user` where they should
  be an `admin`.
- **A use case with no model released to it cannot be run**, because there is nothing for the
  questions to be put to. The console says exactly that rather than disabling the button silently.
  A run is entered at a model the person starting it picks, from that release list.

The seed creates one use case named `smoke-test` for IT Security's **model** evaluation — a
released model and a pipeline that starts there. It is an ordinary use case in every respect and
the application does not branch on it; to evaluate another model, make another use case the same
way. Its group is `/use-cases/smoke-test`.
