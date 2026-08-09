# AIRA — Local Infrastructure (Docker Compose)

This stack provides the open-source infrastructure AIRA depends on, for local development and the
demo mode. It is driven from the repo root via the `Makefile` (preferred), or directly with
`docker compose` from this directory.

## Services & ports

| Service | Image | Host port(s) | Purpose |
|---------|-------|--------------|---------|
| postgres | `postgres:17-alpine` | 5432 | System of record. Creates `aira_gateway`, `aira_mgmt`, `keycloak` DBs |
| keycloak | `quay.io/keycloak/keycloak:26.1` | 8080, 9000 | SSO / OIDC (admin console on 8080, health on 9000) |
| kafka | `apache/kafka:3.9.0` (KRaft) | 29092 | Event bus. In-network: `kafka:9092`; from host: `localhost:29092` |
| schema-registry | `confluentinc/cp-schema-registry:7.8.0` | 8081 | Event schema registry |
| vault | `hashicorp/vault:1.18` (dev) | 8200 | Secrets (dev root token `root`) — **not read by any code yet** |
| otel-collector | `otel/opentelemetry-collector-contrib` | 4317, 4318 | OTLP ingest (profile `observability`) |
| otel-lgtm | `grafana/otel-lgtm` | 3000 | Grafana + Tempo + Prometheus + Loki (profile `observability`) |

> Image tags are pinned per `ADR-0003`. Observability is Grafana `otel-lgtm` per **ADR-0004**,
> which supersedes the SigNoz choice in ADR-0002. `make up` includes the `observability` profile;
> `make up-core` starts infrastructure only.

## Two layers

`docker-compose.yml` is **infrastructure only**. The applications live in the overlay
`docker-compose.apps.yml` — gateway, config consumer, management API, outbox relay and the SPA
behind nginx, built from `gateway/Dockerfile`, `management/backend/Dockerfile` and
`management/frontend/Dockerfile`.

```bash
make up        # infrastructure only — for running the apps from source
make up-full   # infrastructure + all five application containers
```

See [`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md) for the full picture, including which of
these services are actually used by the code:

- **Vault** and the **Schema Registry** run here but no code reads from them today. Secrets come
  from environment variables; Kafka events are plain JSON with an `event_type` header.

## Usage

```bash
# from the repo root
make up        # start the whole stack (creates .env from .env.example if missing)
make ps        # show service status/health
make logs      # tail logs
make down      # stop the stack (keeps volumes)
make destroy   # stop and remove volumes (fresh state)
```

Or directly from this directory:

```bash
cp .env.example .env
docker compose up -d
docker compose ps
```

## Notes
- `.env` is git-ignored and holds **local-only** dev credentials. Real secrets belong in a secret
  store — note that the Vault integration is not implemented yet (see above).
- Keycloak imports the `aira` realm from `keycloak/realms/` on first startup — see below.
- Kafka runs in single-node KRaft mode (no ZooKeeper). Topic auto-creation is **off**, so
  `make kafka-topics` is required; it creates all five compacted config topics.


## Test clients in the dev realm

The realm carries two confidential clients with service accounts, `aira-integration-tests`
(role `it-steuerung`) and `aira-integration-tests-member` (role `use-case-admin`). They exist so
`tests/integration/` can obtain **real, realm-signed tokens** carrying real roles — the one thing
no hermetic test can produce and no browser test can hand to a non-browser caller.

Two of them on purpose: a visibility test with a single caller can only show that somebody sees
something, never that anybody is excluded.

They use the client-credentials grant, deliberately *not* the password grant, which ADR-0007
disabled and which is not worth re-enabling for a convenience a machine-to-machine grant already
provides. Their secrets are in the realm file: **dev only**, never a template for a real realm.

A stack whose realm predates these clients will not have them — the import runs only when the
realm does not exist. The tests say so when their token request fails; recreate the realm as
below.

## Changing the Keycloak realm

`--import-realm` uses the `IGNORE_EXISTING` strategy: the realm under
`keycloak/realms/aira-realm.json` is imported **only when it does not exist yet**. Editing the
file has no effect on a stack that has already run — recreate the realm first:

```bash
make destroy && make up          # drops all volumes, cleanest
# or: delete the realm in the admin console (or via the admin API) and restart the container
```

> **A recreated realm issues new subject ids, and Management binds users to them.**
> Every Keycloak user gets a fresh `sub`, and `OidcIdentity` binds a Django user to the old one
> (`ADR-0007`). The next sign-in therefore **provisions a second user** — `admin` becomes
> `admin-ec05a3db` — which owns nothing: no API keys, no memberships, no object permissions. The
> console shows the suffixed name and the original account looks abandoned.
>
> Do **not** delete the duplicates and let the originals re-provision: `ApiKey.owner` cascades, so
> that destroys every key those users issued. **Rebind instead** — point each original
> `api_oidcidentity.subject` at the user's new Keycloak id (including the service accounts, whose
> ids change too), then remove the duplicates. Keys, grants and guardian permissions survive.
>
> The same applies to a real change of identity provider, which is why it is written here rather
> than left as a local-stack curiosity.

Two things to watch when editing it:

- `CLIENT.DESCRIPTION` is `varchar(255)`. A longer description makes the import fail and
  **Keycloak will not start at all**.
- The realm carries the five AIRA roles and one demo user per role, with usernames matching the
  Django seed (`admin`, `itsec`, `itgov`, `ucadmin`, `ucuser`, all with `demo-password`).
  Keycloak is the source of truth for roles (FRD-201), so a role missing here means the user has
  it nowhere.
- **Recreating the realm orphans the Management users.** Management binds a Django user to the
  token's `sub`, not to its username (ADR-0007) — deliberately, so a renamed Keycloak account
  cannot take over an existing one. A new realm mints new `sub`s, the old rows keep the plain
  usernames, and the returning users are provisioned as `ucadmin-dedf235d` and the like. The
  symptom is an e2e login failing on a username it has never seen, or a demo user suddenly
  seeing none of their use cases. The fix is to drop the stale rows, never to loosen the
  binding — with the Management DB kept, delete the users whose `OidcIdentity.subject` is no
  longer in the realm and rename the suffixed survivors back; or simply `make destroy && make up`
  and reseed, which is what the demo data is for.
