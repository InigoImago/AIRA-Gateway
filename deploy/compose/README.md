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
| vault | `hashicorp/vault:1.18` (dev) | 8200 | Secrets (dev root token `root`) |

> Image tags are pinned per `ADR-0003`. Observability (OTel Collector + SigNoz) is added in `FRD-001`.

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
- `.env` is git-ignored and holds **local-only** dev credentials. Real secrets belong in Vault.
- Keycloak imports realms from `keycloak/realms/` on startup (empty until Phase 2 / `FRD-201`).
- Kafka runs in single-node KRaft mode (no ZooKeeper).


## Changing the Keycloak realm

`--import-realm` uses the `IGNORE_EXISTING` strategy: the realm under
`keycloak/realms/aira-realm.json` is imported **only when it does not exist yet**. Editing the
file has no effect on a stack that has already run — recreate the realm first:

```bash
make destroy && make up          # drops all volumes, cleanest
# or: delete the realm in the admin console (or via the admin API) and restart the container
```

Two things to watch when editing it:

- `CLIENT.DESCRIPTION` is `varchar(255)`. A longer description makes the import fail and
  **Keycloak will not start at all**.
- The realm carries the five AIRA roles and one demo user per role, with usernames matching the
  Django seed (`admin`, `itsec`, `itgov`, `ucadmin`, `ucuser`, all with `demo-password`).
  Keycloak is the source of truth for roles (FRD-201), so a role missing here means the user has
  it nowhere.
