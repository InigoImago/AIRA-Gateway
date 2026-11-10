# Deployment 4 of 4: Integrated

**For:** running AIRA on your organisation's infrastructure, with your Keycloak, your database and
your model platform.
**You need:** the accesses listed in §1, and somebody who can grant them. On the machine that starts
it: Docker with Compose v2.24.4 or newer (the overlays use its `!reset` and `!override`), `make`,
uv, and a checkout of this repository at the tag you deploy — the Compose files and the tools of §4
and §5 are the repository's, not part of the images.
**Shape:** you supply the infrastructure; AIRA supplies three published images — two application
planes and a console — which §5a pulls from a registry and starts. Nothing here is built.

This page is the order to do things in. What each connected system must provide in detail is in
[`../INTEGRATIONS.md`](../INTEGRATIONS.md), and every configuration value is in
[`../CONFIGURATION.md`](../CONFIGURATION.md). This page tells you *when* to read them;
[`../DEPLOYMENT.md`](../DEPLOYMENT.md) §0 is the same procedure with the command that checks each
step.

Work through it in order. Each step can be verified before the next one, which matters: an
integration that is wrong in step 2 and discovered in step 9 costs a day.

---

## 1. What you must be given, and by whom

Ask for these before you start. The middle column is what to ask for; the right column is why, so
you can answer "what do you need that for?".

| From | Ask for | Why |
|---|---|---|
| **Database team** | A PostgreSQL 14+ server (CI tests 17), **two** databases (`aira_gateway`, `aira_mgmt`), each owned by the role that migrates it — or that role holding `CREATE` and `USAGE` on its schema `public`; one role for both, or one per plane (§4) | The planes never share a table. The migrations create their tables in `public`, which from PostgreSQL 15 on is writable by the database's owner alone: `CREATE` on the database allows new schemas, not tables. The Compose files connect both planes as one role unless each plane's own user is set (§4) |
| **Identity team** | A Keycloak realm, and in it: a **group** for each of the three built-in roles (or permission to create them); a **public client** for the console with PKCE S256, its redirect URIs and web origins pinned to the console's hostname, a **`groups` mapper** (full paths) and an **audience mapper**; and a **confidential, read-only directory client** (`view-users`, `query-groups`) for Management — and for the gateway, which reads a mail's addresses with it | Roles and use-case access come from group paths — a realm role grants nothing. Without the audience mapper both planes refuse the token; without the directory client no role can be bound to a group, nobody who has not signed in yet can be granted access, and no mail finds an address |
| **Identity team** | The issuer URL as browsers reach it, the JWKS URL as the containers reach it, and the audience your tokens carry | The services validate every token themselves and require `exp`, `iat`, `sub` and, outside `local`, an audience |
| **Identity team** | The same two mappers on every machine client that will call the gateway with a token of its own | A token without `groups` matches no group grant, and one without the audience is refused |
| **Platform team** | Kafka with the **9 compacted topics** created (or permission to create them), and a broker identity per plane over SASL and TLS — Management writes, the gateway reads | Configuration travels as events; auto-creation is off; `PLAINTEXT` is refused outside `local`, and a missing topic stops every configuration change |
| **Platform team** | A Redis instance | Shared counters for rate limits and budget reservations across gateway processes |
| **Platform team** | An OpenTelemetry collector, if you want telemetry — or a backend per signal that takes OTLP over HTTP | Nothing in this composition starts one |
| **Security team** | A Vault (or compatible) with a KV-v2 mount, permission to create a **read-only policy** and an **AppRole**, and a way to put the AppRole's secret-id into the containers as a file | Every credential lives there; the process fails to start rather than falling back |
| **Cloud team** | A service account or key for your model platform, and the **regions** you are permitted to use | Residency is enforced at startup, not intended |
| **Network** | Ingress and TLS for two hostnames (console, gateway), and egress to your model platform | The console and the API are separate origins |
| **Mail team** *(optional)* | A relay the gateway may send through, a sender address it accepts, and a route to it from the gateway's host | Mail to a person whose own budget crosses a threshold their use case set, and to a use case's administrators about its shared budget and its anomaly findings, at the address the directory client reads from Keycloak; without a relay nothing is mailed and nothing else changes |

You do **not** need cluster-admin anywhere. AIRA never writes to your directory, never creates
users, and never changes a Keycloak group.

---

## 2. Keycloak

Read [`../INTEGRATIONS.md` §2](../INTEGRATIONS.md#2-keycloak-or-another-oidc-provider) and do
exactly what it says. The four things people get wrong:

1. **A group must confer each role, and `AIRA_ROLE_GROUPS` must name it** — `global-admin`,
   `it-security`, `it-steuerung`, mapped to whatever group paths your realm uses. **A Keycloak
   *realm role* grants nothing**: neither plane reads `realm_access.roles`, and an installation that
   assigns realm roles has nobody holding a role, with no error anywhere. See
   [`../ROLES.md`](../ROLES.md).
2. **The `groups` mapper must be on every client that reaches AIRA**, including service accounts. A
   token with no `groups` claim grants nothing through a group, and nothing anywhere says so.
3. **The audience mapper must be on every client that reaches AIRA.** Keycloak does not add the
   audience by itself; without it both planes refuse every token outside `local`, and the console,
   after one attempt to sign in again, stops and says *"We could not sign you in"*.
4. **Redirect URIs and web origins must be pinned** to your console hostname — never `*`. AIRA does
   not read the client's settings, so nothing here refuses a wildcard; it is yours to avoid.

Verify before moving on: sign in through the console's own login once it runs, or have the identity
team issue a token for a test user, and decode it ([`../DEPLOYMENT.md`](../DEPLOYMENT.md) §0 Step 2
shows how). The claims must contain `groups` — with **full paths**, including the path
`AIRA_ROLE_GROUPS` names for their role — plus `exp`, `iat`, `sub` and an `aud` naming your
audience. `realm_access.roles` is not read and its presence proves nothing.

---

## 3. Databases, Kafka, Redis

Create the two databases and the role that owns them — or one role per plane, each owning its
database (§4).

Create the topics — the names are in
[`../INTEGRATIONS.md` §3](../INTEGRATIONS.md#3-apache-kafka). All are **compacted**: they are a
current-state log, not a history, and compaction is what lets a gateway's read-model be rebuilt by
replaying them from the beginning. Create every one: the relay publishes in order, and the first
event for a topic that does not exist stops it — with every later event behind it.

Point Redis at the gateway. Without it, rate limits fall back to **per-process** counters — not
open, but N processes then allow N times the limit — and budgets fall back to the older racy path.
Both are reported by `/readyz` as `degraded: true`, which is a deliberate state and not a failure.

---

## 4. Vault: put the secrets in before anything reads them

This is the step that is easiest to skip and worst to skip. Do it now.

```bash
# Against your Vault, with a token that may write policies:
export VAULT_ADDR=https://vault.example.internal
export VAULT_TOKEN=<a token that may create policies and AppRoles>

uv run python tools/vault_setup.py --mount secret --path aira
```

It creates the KV-v2 path, a **read-only policy on exactly that path**, and an AppRole; it prints
the role id and writes the secret-id to a file with mode `0600` — by default
`deploy/compose/secrets/vault-secret-id`, which `.gitignore` excludes. It refuses any
`--secret-id-file` git would commit (a tracked file, or an unignored one inside a checkout), and it
never prints a secret value, including the one it generates.

**The secret-id expires** after 30 days (`--secret-id-ttl`, a Vault duration; `0` means never). The
services read it once, at start, so a running deployment is unaffected — but a restart after the
expiry fails closed with *Vault refused the AppRole login*. Run the tool again before then and
restart the services; the output says when the current one runs out. Every run mints a new
secret-id and writes it over the file, the runs below included; one minted earlier stays valid until
it expires.

Put the credentials in. `--from-env` copies every **credential setting** of both planes that is
set in your shell — the names under `secrets.in_vault` in the `config/*.example.yaml` files — and
nothing else:

```bash
export AIRA_POSTGRES_PASSWORD=...
export AIRA_SECRET_KEY=...                # Django's; generate a long random value
export AIRA_KAFKA_SASL_PASSWORD=...
export AIRA_DIRECTORY_CLIENT_SECRET=...
export AIRA_PSEUDONYM_SECRET=...          # one pseudonym per person in telemetry, across processes
export AIRA_VERTEX_CREDENTIALS=...        # and/or AIRA_VERTEX_API_KEY, AIRA_GOOGLE_API_KEY, …
uv run python tools/vault_setup.py --from-env
```

A Redis URL with a password in it is a credential as well, and not one the tool copies — it takes
only settings named like a credential. Write it to the path as `AIRA_REDIS_URL` with Vault's own
`vault kv patch`, and leave `redis.url` empty in the file of §5: Vault's value wins over the empty
one.

**Where the database and the broker give each plane its own identity** — a database user per
database, a Kafka consumer that only reads and a relay that writes — the two planes cannot share
`AIRA_POSTGRES_PASSWORD` and `AIRA_KAFKA_SASL_PASSWORD`. Write each plane's own beside the shared
names on the same path; a plane's own name replaces the shared one, and a plane never reads the
other's:

```bash
export AIRA_GATEWAY_POSTGRES_PASSWORD=...          # the gateway's database user's
export AIRA_MANAGEMENT_POSTGRES_PASSWORD=...       # Management's
export AIRA_GATEWAY_KAFKA_SASL_PASSWORD=...        # the consumer's, which only reads
export AIRA_MANAGEMENT_KAFKA_SASL_PASSWORD=...     # the relay's, which writes
uv run python tools/vault_setup.py --from-env
```

The user names identify and are configuration: `gateway.postgres_user`,
`gateway.kafka_sasl_username`, `management.postgres_user` and `management.kafka_sasl_username` in
the file of §5 (`AIRA_GATEWAY_POSTGRES_USER` and its three siblings). On one path each plane's
AppRole can still *read* the other's password. Where that matters, give each plane a path and an
AppRole of its own instead: `VAULT_PATH` and `VAULT_ROLE_ID` are read by each service, so a Compose
file of your own can set them per service, and each path then holds the shared names alone. The
tool cannot make the second pair: its policy and its AppRole are both named `aira`, and a run with
another `--path` moves that one policy to the new path. Create the second with Vault's own
commands.

Then configure the applications with the **address and role id** (these identify; they do not
authorise) and the path of the secret-id **file** inside the containers:

```
VAULT_ADDR=https://vault.example.internal
VAULT_MOUNT=secret
VAULT_PATH=aira
VAULT_ROLE_ID=<printed above>
VAULT_SECRET_ID_FILE=/run/secrets/vault-secret-id
```

In the configuration file of §5 these are its `vault:` section — `addr`, `mount`, `path`, `role_id`
and `secret_id_file` — which the renderer writes under exactly these names.

**Mounting that file is yours to do — the Compose files do not.** Neither `docker-compose.apps.yml`
nor the two overlays of §5a mounts the secret-id — the one thing they mount is
`deploy/compose/secrets/vertex`, read-only into the gateway, for a Vertex key read as a file — and
`make up-integrated` takes no further file. A `VAULT_SECRET_ID_FILE` that names a path the container
cannot read stops every service at start-up (*`VAULT_SECRET_ID_FILE` points at '…', which cannot be
read*). Mount it read-only into the seven services that read Vault, with a Compose file of your own:

```yaml
# deploy/compose/secrets/vault-secret-id.yml — not shipped; beside the secret-id, out of git
x-secret-id: &secret-id
  volumes:
    - ./secrets/vault-secret-id:/run/secrets/vault-secret-id:ro
services:
  gateway-migrate: *secret-id
  gateway: *secret-id
  gateway-consumer: *secret-id
  gateway-retention: *secret-id
  management-migrate: *secret-id
  management: *secret-id
  management-relay: *secret-id
```

and start with the `docker compose` command of §5a with that file as a fourth `-f`. The path on the
left is resolved from `deploy/compose`. Both images run as uid 10001, so the file must be readable
by that user — owned by it, mode `0600`. The same file is where any other file a setting names goes
in, because nothing shipped mounts one: a Kafka CA (`kafka.ssl_cafile`) is a path inside the
containers, and a private CA bundle needs its mount and `SSL_CERT_FILE` in each service's
`environment:` as well — the shipped files pass no variable they do not name.

**Do not put the secret-id in an environment variable** if you can mount it. `VAULT_SECRET_ID` is
read first and works, but a value there is in `docker inspect`, in `/proc/<pid>/environ`, and in any
library that dumps its configuration when it panics.

**Then remove the plaintext `AIRA_*` secrets from your environment files.** Leaving both means the
environment wins nothing and proves nothing.

**Verify once the services run** (§9): `/readyz`, asked with an operator's credential, must say
`"source": "vault"` under `secrets` and list the key **names**. If it says `"source": "environment"`,
Vault is not configured — `VAULT_ADDR` alone is not enough, a role id is needed beside it — and every
credential is coming from your environment files, which is exactly the state this step exists to
leave behind.

---

## 5. Configure the two applications

Every variable is in [`../CONFIGURATION.md`](../CONFIGURATION.md). The ones that decide whether the
deployment starts, and whether it starts as a deployment:

| Variable | Set it to | If you get it wrong |
|---|---|---|
| `AIRA_ENVIRONMENT` | anything but `local` | `local` — the default of `docker-compose.apps.yml` — switches every deployment check off; only a value that cannot be read at all is still refused. Any other value makes both planes refuse to start on an unsafe default, naming every reason at once |
| `AIRA_OIDC_ISSUER`, `AIRA_OIDC_JWKS_URI`, `AIRA_OIDC_AUDIENCE` | your realm's values | An audience is **required** outside `local`, on both planes |
| `AIRA_ROLE_GROUPS` | your groups for the three built-in roles | Management refuses to start without a group for `global-admin` |
| `AIRA_PRIVACY_CONTROLLER` | your organisation's name, address and a contact | Management refuses to start without it: the privacy notice every console user is shown names the controller |
| `AIRA_KAFKA_SECURITY_PROTOCOL` and the SASL settings | `SASL_SSL`, and each plane's broker identity — `AIRA_GATEWAY_KAFKA_SASL_USERNAME` and `AIRA_MANAGEMENT_KAFKA_SASL_USERNAME`, their passwords in Vault (§4) | `PLAINTEXT` is refused on both planes; with only the shared `AIRA_KAFKA_SASL_USERNAME`, both planes authenticate as one identity |
| `AIRA_DIRECTORY_CLIENT_ID`, `AIRA_DIRECTORY_URL` | the directory client, and where Management and the gateway reach Keycloak | Without them no role can be bound to a group, and no mail finds an address |
| `AIRA_ALLOWED_REGIONS` | the regions you are permitted to use | A model configured outside them stops the gateway at startup; a catalogued one is refused per request. `AIRA_ALLOWED_MODEL_REGIONS` permits a region for named models alone |
| `AIRA_TRUST_FORWARDED_FOR`, `AIRA_TRUSTED_PROXY_HOPS` | `true`, and the number of proxies that append, **only** behind proxies you control | Otherwise a caller can dictate the address in your audit trail. The Compose files hand the count to the gateway only: Management keeps its default of 1 — right for the console's nginx alone, one short behind a proxy of yours ([`../INTEGRATIONS.md` §8](../INTEGRATIONS.md#8-reverse-proxy-and-tls)) |
| `AIRA_MAX_REQUEST_BYTES` | your body ceiling | The product's default is 8 MiB, the integrated example's 1 MiB. Inline documents need more, and so does a coding assistant's long session, which resends the whole conversation with every step |
| `AIRA_STORE_PAYLOADS` | your policy | A kill switch above the per-use-case setting |
| `AIRA_OTEL_ENABLED`, `AIRA_OTEL_ENDPOINT` | your collector — or, per signal, `AIRA_OTEL_TRACES_ENDPOINT`, `_METRICS_ENDPOINT`, `_LOGS_ENDPOINT` as whole URLs | The default endpoint names a collector this composition does not start; a signal with no URL at all is not exported |

Outside `local` the control plane also refuses an unset issuer, a development `SECRET_KEY`,
`DEBUG=true` and `ALLOWED_HOSTS=*`; the gateway refuses authentication or the use-case requirement
switched off, OIDC switched on with no issuer, and the published Postgres password; both refuse a
plain-HTTP issuer, JWKS or Vault address that is not loopback. The full list for both planes is
[`../CONFIGURATION.md` §8](../CONFIGURATION.md). It is intentional and not configurable. Several
realms at once (`AIRA_OIDC_ISSUERS`) are read by the gateway only; a delivery channel to a SIEM is
the collector's (`AIRA_OTEL_FORWARD_*`,
[`../INTEGRATIONS.md` §6](../INTEGRATIONS.md#6-observability)).

### One file instead of forty variables

The table above is what a deployment must get right. Setting them one at a time is how a deployment
comes to run on a value nobody chose, so there is a file:
[`config/integrated.example.yaml`](../../config/integrated.example.yaml) — every setting under a
section, with the comments that say what each one refuses. Copy it and edit the copy:

```bash
cp config/integrated.example.yaml config/my-installation.yaml
$EDITOR config/my-installation.yaml
make config-check CONFIG=config/my-installation.yaml        # would both planes start?
uv run python tools/config_render.py config/my-installation.yaml -o deploy/compose/.env
make config-verify                                          # would the stack run on it?
```

`make config-check` asks Vault only when the file names `vault.secret_id_file` and that path is
readable where it runs, and otherwise exits 3; `uv run python tools/config_check.py
config/my-installation.yaml --without-vault` checks everything else ([`../DEPLOYMENT.md`](../DEPLOYMENT.md)
§0 Step 0 lists what it cannot see).

**Three values the composition of §5a depends on.** The console's upstreams,
`management.upstream` and `gateway.upstream`, are addresses *the console's container* reaches —
under `make up-integrated` the service names `http://management:8002` and `http://gateway:8001`,
never `127.0.0.1`, which is the console's own container. `oidc.jwks_uri` must be set: the external
overlay refuses an empty one. And `directory.url` must name your Keycloak as Management reaches it:
left empty, Compose replaces it with the bundled stack's `http://keycloak:8080`, and the directory
cannot be asked. The gateway reads it too, for the address a mail goes to.

**The file ranks above the deployment, and `config-verify` is what makes that a fact rather than an
intention.** Compose fills every gap from `${VAR:-default}`, so a value left empty or a `.env`
edited afterwards runs on a default silently. It reads the bundled stack's Compose files, though,
not the composition of §5a: an empty `redis.url` — beside Redis Sentinel, or a deliberate none — is
reported as the bundled `redis://redis:6379/0` taking over, which `make up-integrated` does not do,
and the collector's keys (`otel.debug_verbosity`, `otel.arrived_file`, `backend_*`, `forward_*`)
count as received although that composition starts no collector.

**Credentials are refused, not requested.** `config_render.py` raises on a password or an API key:
they come from Vault (§4). The database password needs a word of its own: Compose fills both planes'
`AIRA_POSTGRES_PASSWORD` from `POSTGRES_PASSWORD`, an infrastructure variable whose default is the
published `aira-local`, and gives both planes the same `AIRA_POSTGRES_USER`. With Vault holding
`AIRA_POSTGRES_PASSWORD`, Vault's value wins; without Vault the gateway refuses the published one
outside `local`, and Management does not check it. A plane's own user and password (§4) replace
both, and the gateway's refusal reads the password it actually connects with.
`KEYCLOAK_ADMIN_PASSWORD` and `KC_DB_PASSWORD` belong to the bundled stack and play no part here.

---

## 5a. Running the published images, against your infrastructure

Everything above is what the deployment must be *given*. This is how it is *started* — two Compose
overlays on the applications' own file, and no build.

AIRA publishes three images per release:

```
ghcr.io/inigoimago/aira-gateway:vX.Y.Z       the data plane — API, consumer, retention, migrations
ghcr.io/inigoimago/aira-management:vX.Y.Z    the control plane — API, outbox relay, migrations
ghcr.io/inigoimago/aira-frontend:vX.Y.Z      the console
```

`make current-version` prints the newest version this checkout's tags name; GitHub's Releases page
carries each one's own description. They are `linux/amd64` only.

### What to run

```bash
make release-pull RELEASE=vX.Y.Z     # fetch the three images, and nothing else
make up-integrated RELEASE=vX.Y.Z    # start the applications, and no infrastructure at all
```

Or without the Makefile, whose target runs this without the fourth file, after `make config-verify`
— and the form to use when a fourth file mounts the Vault secret-id (§4):

```bash
cd deploy/compose
AIRA_VERSION=vX.Y.Z docker compose \
  -f docker-compose.apps.yml \
  -f docker-compose.external.yml \
  -f docker-compose.release.yml \
  -f secrets/vault-secret-id.yml up -d
```

Three shipped files, each about one thing. `docker-compose.apps.yml` is what the product is
configured with — the same file every other deployment shape uses, so there is no second copy of
forty defaults to keep in step. `docker-compose.external.yml` starts **no** Postgres, Keycloak,
Kafka, Redis, Vault or collector, and makes every address they live at a **required** variable.
`docker-compose.release.yml` takes the three images from the registry and removes their build, so
nothing can be built here by accident.

### Six addresses it asks for

That is the point of the second file. `docker-compose.apps.yml` defaults every address to the
container names of the stack AIRA ships beside — `postgres`, `kafka:9092`,
`keycloak:8080/realms/aira` — which are right there and resolve to nothing here.

| Variable | Is |
|---|---|
| `AIRA_POSTGRES_HOST` | your PostgreSQL host (§3). Port and user are the usual `AIRA_POSTGRES_*`, the password is Vault's (§5); Management's database is `aira_mgmt`, fixed in the Compose file, and the gateway's is `AIRA_POSTGRES_DB`, `aira_gateway` by default |
| `AIRA_KAFKA_BOOTSTRAP_SERVERS` | `host:port[,host:port]` (§3) |
| `AIRA_OIDC_ISSUER` | your realm's issuer, exactly as the **browser** obtains tokens from it (§2) |
| `AIRA_OIDC_JWKS_URI` | the JWKS URL **these containers** can reach. Not necessarily the issuer's address: the issuer must match the `iss` claim of a token the browser obtained, while the key set is fetched by the containers, which may reach Keycloak by another name |
| `AIRA_REDIS_URL` | the shared counters, **or empty** — a deliberate none, Sentinel's `AIRA_REDIS_SENTINELS` instead, or a URL with a password that Vault holds (§4) |
| `VAULT_ADDR` | your Vault, **or empty** — a deliberate none (§4) |

The first four are refused, before a container starts, while unset or empty. The last two are
refused only while unset, because for both of them "nothing" is a real configuration and a
consequential one:

- **No Redis** means rate limits hold per process and budgets use the older racy path. `/readyz`
  reports `degraded: true`. With N gateway processes each limit then allows N times what it says.
- **No Vault** means every credential comes from your environment files instead, silently. §4 is
  the step this is here to stop you skipping.

**Answer them from your own file.** `make up-integrated`, like every `up*` target, copies
`deploy/compose/.env.example` to `deploy/compose/.env` when there is none — and that file answers
the last two for you: `AIRA_REDIS_URL` pointing at a `redis` container this composition does not
start, and an empty `VAULT_ADDR`. Render your configuration file first (§5).

**Everything else defaults to `docker-compose.apps.yml`'s values, and those are the bundled stack's
development values**: `AIRA_ENVIRONMENT=local`, which switches every deployment check off,
`AIRA_DEBUG=true`, `AIRA_ALLOWED_HOSTS=*`, the development `AIRA_SECRET_KEY`, the development realm's
directory secret, `PLAINTEXT` Kafka. A rendered configuration file that sets `environment` to
anything but `local` — the integrated example sets `production` — makes the start-up checks refuse
whatever of that is left. [`../CONFIGURATION.md`](../CONFIGURATION.md) is the full list.

A version is **not** in that file, and neither is a registry: they are Compose's own variables, not
settings of either plane. `AIRA_VERSION` comes from `RELEASE=` on the command line, which is one
place rather than two; `AIRA_IMAGE_REGISTRY`, set in the same shell, names a mirror instead of
`ghcr.io/inigoimago`.

### The topics are not created for you

`make up-integrated` does not create the nine compacted Kafka topics. Against a broker reached over
SASL or TLS it cannot: the one-shot that creates them would need a client properties file it has no
way to assemble, and two services wait for it to finish successfully and would never start at all.

So have them created — §3 and [`../INTEGRATIONS.md` §3](../INTEGRATIONS.md#3-apache-kafka) list the
names, all with `cleanup.policy=compact`, which is not optional. `make kafka-topics-external` creates
them from a one-shot container, but only on a `PLAINTEXT` broker, which nothing outside `local`
accepts.

### What `up` then does, in order

1. `gateway-migrate` runs `alembic upgrade head` and `management-migrate` runs
   `manage.py migrate` — **from the published images**, so §7's two commands are already done, and
   the schema is the one that version expects. They wait for nothing: your database's readiness is
   not something this composition can see.
2. The five long-running processes that open a database start once the migration that prepares
   their schema has finished: the gateway, its consumer and the retention sweep after
   `gateway-migrate`, Management and its relay after `management-migrate`.
3. The console starts after both APIs have started.

There is no step where this project reaches your infrastructure to configure it. It is handed
addresses.

### Verifying the version, which is the point of all this

```bash
docker inspect --format '{{.Name}} {{.Config.Image}}' $(docker ps -q) | sort
```

Every AIRA container must name the same `ghcr.io/…:vX.Y.Z`. If one of them says `aira-gateway:dev`,
something built it — and that container is this machine's code under a version number from the
registry, which is the one thing a version number exists to prevent.

### A note on the two moving tags

`latest` and `sha-<commit>` are published too. `AIRA_VERSION` deliberately has **no default**, so
neither is used unless you type it: `latest` moves under a restart, which means a deployment that
was pinned yesterday is not pinned today and nothing says so. If you do run a moving tag, set
`AIRA_PULL_POLICY=always` — otherwise Compose keeps the copy it already has.

---

## 6. Ingress and TLS

Two hostnames, because the console and the API are separate origins:

| Hostname | Points at | Notes |
|---|---|---|
| `aira.example.com` | the console container | It also proxies `/api` to the control plane and `/gw` to the gateway |
| `gateway.example.com` | the gateway | This is what callers use |

Management needs no hostname of its own: the console proxies `/api` to it. `make up-integrated`
publishes the console on port 4200, the gateway on 8001 and Management on 8002 — on loopback,
because `AIRA_BIND_HOST` and `AIRA_BIND_HOST6` default to `127.0.0.1` and `::1`. That suits a proxy
on the same host; one on another needs `AIRA_BIND_HOST` set to an address it can reach. It and
`AIRA_PUBLISH_FRONTEND_PORT`, `AIRA_PUBLISH_GATEWAY_PORT` and `AIRA_PUBLISH_MANAGEMENT_PORT`, which
move the ports, are Compose's own, like `AIRA_VERSION`: set them in the shell that starts the
composition.

What the proxy in front must do — append `X-Forwarded-For`, send HSTS, leave streamed answers
unbuffered, keep `?key=` out of its access log, and wait longer than your slowest model — is
[`../INTEGRATIONS.md` §8](../INTEGRATIONS.md#8-reverse-proxy-and-tls). The console's own proxy sets
no body limit on `/gw`: how large a request may be is the gateway's `AIRA_MAX_REQUEST_BYTES` to
decide, unless your proxy refuses it first, in a page of its own that no client can read.

The console takes its issuer and its content policy **at deployment time** — see
[`../INTEGRATIONS.md` §7](../INTEGRATIONS.md#7-the-spa-is-configured-at-deployment-time). Its
container writes `runtime-config.js` on every start from `AIRA_OIDC_ISSUER`, `AIRA_OIDC_CLIENT_ID`
and `AIRA_TRACE_URL`, and derives its content policy from the issuer's origin; changing any of them
is a restart with a new value, not a rebuild. `AIRA_CSP_CONNECT_SRC` is only for what that
derivation cannot guess.

---

## 7. Migrate, then start

**If you started it the way §5a says, this is already done** — the two migration containers ran
`alembic upgrade head` and `manage.py migrate` from the published images, and the processes waited
for them. This section is what those containers do, for a deployment that runs the processes some
other way (a scheduler, a chart, systemd):

```bash
# control plane
python manage.py migrate --noinput
# gateway
alembic upgrade head
```

Then start the gateway, the control plane, the console and the gateway's config consumer as
long-running processes, and **schedule the other two**, which each do one pass and exit:

- **the relay** (`python manage.py relay`) — every minute is the intended shape, and the Compose
  services loop every ten seconds. Without it nothing configured in the console reaches the
  gateway.
- **the retention sweep** (`python -m aira_gateway.retention`) — hourly is right. It is what deletes
  expired prompts, and if it does not run, nothing is deleted and your retention policy is a
  sentence in a document.

---

## 8. The first administrator

AIRA keeps no passwords. The first administrator is whoever is in the group `AIRA_ROLE_GROUPS` maps
to `global-admin`; signing in to the console creates their record automatically.

Then, in the console:

1. **Models & prices** — catalogue every model you intend to use, price it, declare what it can do,
   press **Check reachability**, and **approve** it. Nothing is callable until this is done: an
   uncatalogued model is refused by name, and so is a catalogued one nobody approved. By default a
   model with no price is refused under a cost budget.
2. **Use cases** — create the first one (you are its administrator), release the models it may call
   on its **Overview** tab — **a use case with nothing released can call nothing** — grant a group
   access on **Members**, and issue a key on **API keys**.

The use case reaches the gateway over Kafka once the relay has run; until then a request for it is
refused `403` *"not known to this gateway"*.

---

## 9. Verify, in this order

```bash
# 1. The gateway is up and says where its secrets came from — as an operator
curl -s https://gateway.example.com/readyz -H "Authorization: Bearer $TOKEN" | jq

# 2. A token from your realm is accepted
curl -s https://gateway.example.com/v1beta/models -H "Authorization: Bearer $TOKEN"

# 3. A real request is served and recorded
curl -s https://gateway.example.com/uc/<use-case>/v1beta/models/<model>:generateContent \
  -H "x-goog-api-key: aira_..." -H "content-type: application/json" \
  -d '{"contents":[{"parts":[{"text":"Say OK"}]}]}'
```

The first needs an operator's token — somebody whose role holds `operations.diagnose`, such as IT
Security or a Global Administrator — or the break-glass key ([`../DEPLOYMENT.md`](../DEPLOYMENT.md)
§6c); anyone else is answered with the verdict and `degraded` alone. The second proves the token is
accepted and nothing more: a listing names no use case. The third uses the key from §8, for a model
released to its use case, and names that use case: every model call does, a key bound to one
included — `/uc/<use-case>` in front of the path, or the `X-AIRA-Use-Case` header. Without it the
gateway answers `400` and says which use case to name.

Then open the console's **Requests** view and confirm the third call is there, with its tokens, its
cost and its use case. If it is not, the request never reached the gateway or the audit writer could
not write — both are visible in the gateway's log.

---

## 10. Before you call it production

- [ ] `/readyz` reports `"source": "vault"` and no plaintext credential remains in any env file
- [ ] `AIRA_ENVIRONMENT` is not `local`, and both planes started anyway
- [ ] The relay and the retention sweep are scheduled, and each has run once
- [ ] All nine topics exist and are compacted
- [ ] Every model in the catalog is approved deliberately, not by the migration that approved
      pre-existing rows
- [ ] `AIRA_TRUST_FORWARDED_FOR` and `AIRA_TRUSTED_PROXY_HOPS` match reality: on, with the right
      count, behind your proxies; off otherwise
- [ ] Someone other than you can sign in and sees what their role should see
      ([`../ROLES.md`](../ROLES.md))
- [ ] A budget and a rate limit exist on at least one use case, and you have watched one refuse
- [ ] Backups cover both databases **and** the topics, taken together: the gateway's database holds
      your audit trail, and nothing re-sends configuration ([`../DEPLOYMENT.md`](../DEPLOYMENT.md)
      §6b)

---

## Known gaps at this stage of the product

Stated here rather than discovered later. See [`../GAP-ANALYSIS.md`](../GAP-ANALYSIS.md) for the
full list.

- **No Kubernetes or Helm charts yet.** Compose is what exists; the images are ordinary containers.
- **One gateway container per Compose stack**: its fixed `container_name` blocks `--scale`, and the
  schema rule two versions serving at once would need is stated, not enforced.
  More gateway workers in one container are possible
  ([`../DEPLOYMENT.md` §1a](../DEPLOYMENT.md#1a-sizing-what-one-instance-carries)). Management stays
  a single instance.
- **Alerts go by mail, and by nothing else.** With a relay (§1), a person whose own budget crosses
  a threshold is mailed, and so are a use case's administrators about its shared budget and — unless
  they switch it off — its anomaly findings. The installation's own budget and a global rule's
  finding about one person stay in the console; nothing calls a webhook, a chat or a pager.
- **Personal data is not masked in stored payloads**, deliberately — names and customer numbers are
  what the payload is stored *for*. A use case can replace personal data before the prompt leaves
  with a `pii_filter` step, and the stored request is then the rewritten one; for data
  that must not be kept at all, the control is the per-use-case storage switch.

---

Next: [Showcase](showcase.md) · [Development](dev.md) · [Standalone](standalone.md)
