# AIRA — Docker Compose

The Compose files that run AIRA: the infrastructure it depends on, the applications themselves,
the demo around them, and the overlays that run a published release or run it against your own
infrastructure. They are driven from the repo root via the `Makefile` (preferred), or directly with
`docker compose` from this directory.

## Services & ports

The infrastructure file, `docker-compose.yml`. Every port is published on `AIRA_BIND_HOST`
(`127.0.0.1`) and `AIRA_BIND_HOST6` (`::1`), and moves with its `AIRA_PUBLISH_*_PORT` variable.

| Service         | Image                                          | Host port(s)     | Purpose                                                               |
| --------------- | ---------------------------------------------- | ---------------- | --------------------------------------------------------------------- |
| postgres        | `postgres:17-alpine`                           | 5432             | System of record. Creates `aira_gateway`, `aira_mgmt`, `keycloak` DBs |
| keycloak        | `quay.io/keycloak/keycloak:26.1`               | 8080, 9000       | SSO / OIDC, importing the **development realm** on first start (admin console on 8080, health on 9000) |
| kafka           | `apache/kafka:3.9.0` (KRaft)                   | 29092            | Event bus. In-network: `kafka:9092`; from host: `localhost:29092`     |
| schema-registry | `confluentinc/cp-schema-registry:7.8.0`        | 8081             | Runs; **no code uses it** — events are plain JSON with an `event_type` header |
| vault           | `hashicorp/vault:1.18` (`server -dev`)         | 8200             | Secrets, in memory; root token `root`. The applications read it when `VAULT_ADDR` and a role id or token are set |
| redis           | `redis:7.4-alpine`                             | 6379             | Shared counters for rate limits and budgets; deliberately not persisted |
| otel-collector  | `otel/opentelemetry-collector-contrib:0.157.0` | 4317, 4318, 8889 | OTLP ingest, and its own counters (profile `observability`)          |
| otel-lgtm       | `grafana/otel-lgtm:0.30.0`                     | 3000             | Grafana + Tempo + Prometheus + Loki, with a provisioned dashboard (profile `observability`) |
| otlp-inspector  | `python:3.14-slim`                             | 4319             | What the **forwarding** leg sends, on a page (profile `debug`, `make otlp-inspector`) |
| ollama, ollama-pull | `ollama/ollama:latest`                     | 11434            | A real local model, and the one-shot that downloads it (profiles `demo` and `verify`) |

> Every image tag but Ollama's (`latest`) is pinned, so the stack is reproducible. Observability is
> Grafana `otel-lgtm`, behind the stack's own `otel-collector`. `make up` starts the infrastructure
> and the `observability` profile; `make up-core` starts the infrastructure without it.

**The infrastructure file is a development one.** Its Keycloak imports the development realm —
published passwords, `admin` among the global administrators, client secrets in this repository —
and its Vault runs in `-dev` mode. Nothing in it is meant for a deployment that anybody else can
reach.

## The files

| file | what it holds | run it with |
| --- | --- | --- |
| `docker-compose.yml` | infrastructure: Postgres, Keycloak with the development realm, Kafka, Vault in `-dev` mode, Redis, observability | `make up` |
| `docker-compose.apps.yml` | **the product**: both migrations, the topic one-shot, the gateway, its config consumer and retention loop, Management, its relay loop, the console behind nginx | `make up-apps` (with the file above) |
| `docker-compose.showcase.yml` | the demo around it: the realm repair (`keycloak-init`), the `-dev` Vault's secrets (`vault-init`), the seed (`management-seed`), and the Windows domain (`ad`, profile `windows-sso`) | `make up-full` / `make showcase` |
| `docker-compose.release.yml` | overlay: the three application images from the registry, their `build:` removed | `RELEASE=vX.Y.Z` on any target that starts the applications |
| `docker-compose.external.yml` | overlay: the applications **without** the infrastructure file, every address a required variable; the topic one-shot moves behind the profile `create-topics` | `make up-integrated`, `make kafka-topics-external` |
| `docker-compose.loadtest.yml` | overlay: the upstream double `modelsim`, the gateway pointed at it, its cloud settings emptied | `make loadtest-up` |

Each layer is added with another `-f`; the Makefile targets above already do that.
`tools/compose_files.py` is the one place that decides which file belongs to which combination. The
split matters when you are **integrating**: `docker-compose.apps.yml` is the file to read, and
nothing in it exists for the demo — no demo service, and no service depending on one; the showcase
file adds both when it is layered on. Its defaults are development values all the same
(`AIRA_ENVIRONMENT=local` among them); a deployment overrides them from a configuration file
(`config/README.md`).

```bash
make up              # infrastructure + observability — for running the apps from source
make up-apps         # the above + the application containers, no demo provisioning
make up-full         # the above + the showcase layer
make up-integrated   # the application containers alone, against infrastructure you provide
```

See [`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md) for operating it, and
[`docs/deployment/`](../../docs/deployment/README.md) for each way of running it.

## Usage

```bash
# from the repo root
make up        # start the infrastructure (creates .env from .env.example if missing)
make ps        # show service status/health
make logs      # follow every service's logs
make restart   # restart what is running
make down      # stop everything this repository can start (keeps volumes)
make destroy   # stop and remove volumes — the audit trail, all configuration, the realm
```

Or directly from this directory:

```bash
cp .env.example .env
docker compose up -d
docker compose ps
```

## Notes

- `.env` is git-ignored and holds **local-only** dev credentials. Real secrets belong in Vault: set
  `VAULT_ADDR` and a role id, and the applications read their credentials from it at start-up,
  ranked above the environment. Left empty, as `.env.example` leaves it, they read
  the environment. `make vault-init` creates the path, a read-only policy and an AppRole on the
  development Vault.
- Keycloak imports the `aira` realm from `keycloak/realms/` on first startup — see below.
- Kafka runs in single-node KRaft mode (no ZooKeeper). Topic auto-creation is **off**: the
  `kafka-topics` one-shot creates the nine compacted config topics when `make up-apps`,
  `make up-full` or `make showcase` starts the applications, and `make kafka-topics` does it in this
  stack's broker for a from-source setup — both with the `aira.<stage>.` prefix when
  `AIRA_KAFKA_STAGE` is set.
- Redis runs with `--save "" --appendonly no`: its counters are not persisted, because Postgres
  is the system of record for budget usage.

## Test clients in the dev realm

The realm carries four confidential clients with service accounts for `tests/integration/`, each
holding what its group confers:

| Client | Its service account's group | So |
| --- | --- | --- |
| `aira-integration-tests` | `/aira/it-steuerung` | IT Steuerung |
| `aira-integration-tests-security` | `/aira/it-security` | IT Security |
| `aira-integration-tests-admin` | `/aira/global-admins` | Global Administrator |
| `aira-integration-tests-member` | `/abteilungen/kundendienst` | no role — a member through a department group |

They exist so the suite can obtain **real, realm-signed tokens** carrying real groups — the one
thing no hermetic test can produce and no browser test can hand to a non-browser caller. The
roleless one is there on purpose: a visibility test with a single caller can only show that
somebody sees something, never that anybody is excluded.

A fifth, `aira-directory`, is the read-only service account Management searches the realm with
(`view-users`, `query-groups`, nothing else).

They use the client-credentials grant, deliberately _not_ the password grant, which every client in
the realm file has switched off and which is not worth re-enabling for a convenience a
machine-to-machine grant already provides. Their secrets are in the realm file: **dev only**, never
a template for a real realm.

A stack whose realm predates these clients will not have them — the import runs only when the
realm does not exist. The `keycloak-init` service notices and re-imports the realm; see below.

## A Windows domain beside the realm (`windows-sso`)

`deploy/compose/ad` is Samba as an Active Directory domain controller, in the showcase file behind
the profile `windows-sso`. It stands in for an installation's AD so the Windows sign-on
can be proven against a real KDC and directory: people, role groups and departments under
`OU=AIRA`, an LDAP bind account, and the mail domain as an alternative UPN suffix. The domain is
made on the container's first start; a recreated container is a new domain with new keys.

`tools/windows_sso.py apply` makes both administrators' changes — Keycloak's service principal and
its keytab in the domain, the federation with Kerberos in the realm — and `remove` takes the realm's
half away. The keytab is handed to Keycloak as a file, the way a domain administrator hands it
over, readable by Keycloak alone; a recreated Keycloak has lost it, and `apply` hands it over
again.

```bash
make windows-sso-up     # start the domain, federate the realm (five-minute cache)
make test-windows-sso   # prove it: console and gateway, getting in and not; the realm is restored after
make windows-sso-down   # take the realm's half away, stop the domain
```

**Not a template for a real domain**: its LDAP accepts a simple bind in plain text inside the
stack's network, and its bind password is random, per domain, and never leaves the container. What
an installation sets is in [`docs/INTEGRATIONS.md`](../../docs/INTEGRATIONS.md) §2, *Windows sign-on*.

## Changing the Keycloak realm

`--import-realm` uses the `IGNORE_EXISTING` strategy: the realm under
`keycloak/realms/aira-realm.json` is imported **only when it does not exist yet**. Editing the file
therefore has no effect on a stack that has already run — which is why a colleague could pull a
change, run `make showcase`, and be told _invalid username or password_ by a realm from before it.

The `keycloak-init` service (`tools/keycloak_demo_realm.py`, in the showcase file) closes most of
that. On every start it compares the running realm against the file and, when a user, group,
membership or client the file names is missing, **deletes and re-imports it**. Idle otherwise,
gated to `local`/`demo` environments, and never aimed at a directory AIRA does not own — the
product does not write to one; this is the demo stack provisioning its own. A change it
does not compare — a client's settings, the redirect URIs after the console's port moved — needs
the realm deleted in Keycloak's admin console and the stack started again, after which it imports
the file. Do not delete a volume for it: the realm's database shares `pgdata` with both planes'.

> **The ids are pinned in the file, and that is what makes re-importing safe.**
> Keycloak's `sub` _is_ the user id, and Management binds a Django user to that `sub` rather than
> to a username — deliberately, so a renamed Keycloak account cannot take over an
> existing one. A realm re-imported **without** pinned ids mints new subjects, so every returning
> user is provisioned again: `admin` becomes `admin-8ebe2b11`, owning nothing — no keys, no
> memberships, no object permissions — while the original account looks abandoned. That is why
> every user and group in the realm file carries a stable `id`.
>
> A **real** change of identity provider still moves the subjects, and there the answer is to
> rebind rather than to loosen the binding: point each `api_oidcidentity.subject` at the user's new
> id and remove the duplicates. Do not delete the originals and let them re-provision —
> `ApiKey.owner` cascades, and that destroys every key those users issued.

Three things to watch when editing it:

- `CLIENT.DESCRIPTION` is `varchar(255)`. A longer description makes the import fail and
  **Keycloak will not start at all**.
- The realm carries three realm roles, which grant nothing, and six people, all with
  `demo-password`: `admin`, `itsec` and `itgov`, each in the group that confers one built-in role
  under the `AIRA_ROLE_GROUPS` the Compose files default to; `ucadmin` and `ucuser`, who hold no
  role and reach use cases through grants; and `demo-user`. Usernames match the Django seed. Keycloak is the source of truth
  for roles through **group membership**, so a group missing here means the role is held
  nowhere.
- **Every user and group needs an `id`.** Without one, recreating the realm orphans the
  Management users — see the note above. A new entry added without an id is a trap that only
  springs on the next person to recreate their realm, so the test suite refuses it.
