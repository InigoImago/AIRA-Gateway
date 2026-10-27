# FRD-150 — Each plane its own database user and Kafka identity, on one Vault path

> Phase: 7 (deployment) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: a feature request from an installation whose database and broker separate privilege per
> plane — a database user per database, a Kafka consumer that only reads and a relay that writes —
> and whose Vault offers one path for the whole application. Related: `FRD-116` (secrets from
> Vault), `FRD-000` (two databases), `ADR-0007` (fail closed).

## 1. Summary

Both planes read the same four names: `AIRA_POSTGRES_USER`, `AIRA_POSTGRES_PASSWORD`,
`AIRA_KAFKA_SASL_USERNAME`, `AIRA_KAFKA_SASL_PASSWORD`. Vault ranks above the environment, so a path
both planes read can hold one value of each, and the shipped Compose files pass one value of each to
both. An installation that gives the gateway and Management separate identities — what the
integrated deployment guide already asked of its platform team, *"a broker identity per plane"* —
could not configure them. Each plane now also reads its own name for each, `AIRA_GATEWAY_*` and
`AIRA_MANAGEMENT_*`, which replaces the shared one where it is set.

## 2. Goals & Non-Goals

- **Goals**: separate database and broker credentials per plane from **one** Vault path; nothing
  changes for an installation that writes none of the new names; no new start-up refusal.
- **Non-Goals**: per-plane separation of *what Vault lets each plane read* — that is two paths and
  two AppRoles, which already works (§5.2). Per-plane Redis credentials: only the gateway uses Redis.
  Separate credentials per *process* within a plane (the gateway's API, consumer and retention run
  share the gateway's).

## 3. User Stories

- As an **operator** whose Vault grants the application one path, I want the gateway to connect as
  `aira_gatewayusr` and Management as `aira_mgmtusr`, each with its own password, so that the
  database's separation of privilege is the one that runs.
- As a **platform team**, I want the gateway's consumer to authenticate as an identity that may only
  read the configuration topics and Management's relay as one that may write them.

## 4. Functional Requirements

- **FR-1** The gateway reads `AIRA_GATEWAY_POSTGRES_USER`, `AIRA_GATEWAY_POSTGRES_PASSWORD`,
  `AIRA_GATEWAY_KAFKA_SASL_USERNAME` and `AIRA_GATEWAY_KAFKA_SASL_PASSWORD`; Management reads the
  same four under `AIRA_MANAGEMENT_`. Each, when set and not empty, replaces the shared setting of
  the same name in that plane.
- **FR-2** The plane's own name wins **wherever each value was read from**: a shared value in Vault
  does not override a plane's own given in the environment. Vault's rank above the environment is a
  rule about one setting; this is a rule between two.
- **FR-3** A plane never reads the other's names.
- **FR-4** Empty is unset, as Compose passes an optional variable.
- **FR-5** Every reader of the shared settings reads the value in effect: the database connection,
  the broker's credentials, and the refusal of the published development password outside `local`.
- **FR-6** The two new passwords are credentials: Vault holds them, `vault_setup.py --from-env`
  copies them, the renderer refuses them in a configuration file, the showcase provisions them.

## 5. Design & Architecture

### 5.1 One rule in the base class

`aira_common.config.PLANE_CREDENTIALS` names the four settings. A settings class names its plane
(`credential_plane`) and declares a `<plane>_<setting>` field for each; an after-validator on
`BaseAiraSettings` writes each non-empty own value into the shared field. Every source — Vault,
environment, `.env` — fills the fields as before, so the spelling rules of `FRD-116` FR-3 apply
to the new names unchanged: `management_postgres_password` in Vault is
`AIRA_MANAGEMENT_POSTGRES_PASSWORD`.
Written into the shared field rather than read beside it, so no reader has to know the new names and
none can forget them.

### 5.2 Two paths remain the stronger separation

`VAULT_PATH` and `VAULT_ROLE_ID` are read by each process, so each plane can already have a path and
an AppRole of its own; then neither can read the other's password, and each path holds only the
shared names. On one path both AppRoles read everything on it, so the planes act on different
credentials while each could read the other's. The guides say which is which; the feature exists
because a second path is often not the operator's to create.

### 5.3 Compose

Each plane's environment block passes its own four names, empty by default; the shared ones keep
their defaults. `config/integrated.example.yaml` shows the user names under `gateway:` and
`management:`, and lists the passwords under `secrets.in_vault`.

## 6. Data Model

None.

## 7. API / Interface Contract

Configuration only: eight `AIRA_*` settings, in `docs/CONFIGURATION.md` §1.

## 8. Testing & Acceptance

- `tools/tests/test_each_plane_reads_its_own_credentials.py`, through the real settings classes and
  the real settings source with only Vault's read replaced: one path gives each plane its own
  database user (read off the gateway's database URL and Management's `DATABASES`) and its own Kafka
  identity (`kafka_security()`); without own names both read the shared ones; neither reads the
  other's; the own name in the environment beats the shared one in Vault; empty is shared; the
  published-password refusal reads the value in effect. Mutations `PLC-1`–`3`.
- **Integration** (`tests/integration/test_each_plane_its_own_credentials.py`), on the stack's own
  Vault and Postgres: two roles and databases created per run, one Vault path with a wrong shared
  password and each plane's own. The gateway's real migrations run as its user and every table is
  its; Management connects as its own; with the shared names alone, as the other plane's user, and
  into the other plane's database Postgres refuses. With the rule broken the three positive cases
  fail. Kafka SASL is not in it — the stack's broker is `PLAINTEXT`.
- **Browser**: none. The feature has no screen; what a browser could show is that the stack runs,
  which every browser test already shows.
- **Acceptance** (live, throwaway containers): a Postgres with `aira_gatewayusr` and `aira_mgmtusr`
  owning one database each, connect revoked from everybody else, and a Vault dev server whose one
  path held a wrong shared password and both planes' own. The images built from this tree ran
  their migrations against it: the gateway's created 17 tables owned by `aira_gatewayusr`,
  Management's 29 owned by `aira_mgmtusr`; the gateway with the shared names alone, the gateway
  as Management's user and Management pointed at the gateway's database were each refused by
  Postgres. Kafka SASL was not run live; its credentials take the same path through
  `kafka_security()`, which the tests read.

## 9. Consequences

- An installation can now run the separation its database and broker were set up for, from the path
  it has. One that writes none of the new names is unaffected.
- The gap stated rather than closed: on one path each plane's AppRole can read the other's
  passwords.
