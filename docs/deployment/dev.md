# Deployment 2 of 4: Development

**For:** changing the code and seeing the change immediately.
**You need:** Docker, Python 3.14 with [uv](https://docs.astral.sh/uv/), Node 26.
**Shape:** infrastructure in containers, the six application processes from source with
reload-on-save.

If you only want to *run* AIRA rather than change it, use [Standalone](standalone.md) instead — it
needs nothing but Docker.

---

## Step 1: the toolchain

The versions are pinned, and they are pinned because a mismatch produces failures that look like
code defects (`ADR-0003`).

```bash
python --version   # 3.14.x
node --version     # 26.x
uv --version
docker version
```

Missing something:

| Tool | Install |
|---|---|
| Python 3.14 | `uv python install 3.14` |
| uv | <https://docs.astral.sh/uv/getting-started/installation/> |
| Node 26 | `nvm install` (the repository has an `.nvmrc`) |

---

## Step 2: dependencies

```bash
make sync
```

This installs the Python workspace (three packages: `aira_common`, `aira_gateway`,
`aira_management`) and the frontend's npm dependencies. It is safe to re-run.

---

## Step 3: infrastructure

```bash
make up
```

Starts Postgres, Keycloak, Kafka, the schema registry, Redis, Vault, the OpenTelemetry collector and
Grafana — and **not** the application containers, because you are about to run those from source.

Wait until everything is healthy:

```bash
make ps
```

---

## Step 4: prepare the two databases

Both planes have their own database and their own migration tool. Run all four; each is idempotent.

```bash
make seed              # control plane: migrate, then create the five roles and demo users
make migrate-gateway   # gateway: Alembic migrations
make kafka-topics      # the compacted config topics
```

**`make kafka-topics` is not optional.** Auto-creation is switched off, and a missing topic produces
no error anywhere — the events are simply never delivered. This repository has found that failure
three times; the fourth is prevented by a test, not by memory.

---

## Step 5: run the processes

The first four in their own terminal; the last two publish or delete what is pending and exit, so
run them when you need them.

```bash
make run-gateway-oidc   # gateway            :8001   (long-running)
make run-backend        # control plane API  :8002   (long-running)
make run-frontend       # console            :4200   (long-running)
make consume            # gateway config consumer    (long-running)
make relay              # outbox to Kafka            (publishes what is pending, then exits)
make prune              # payload retention          (optional; hourly in production)
```

Open <http://localhost:4200> and sign in as `admin` / `demo-password`.

---

## The three things that will catch you

### 1. Use `run-gateway-oidc`, not `run-gateway`

The console's dry-run, reporting and request views send the browser's bearer token to the *gateway*,
which needs OIDC enabled to validate it. Without it those views degrade — visibly, but you will
spend ten minutes wondering why.

### 2. The relay carries configuration; nothing happens without it

Changing members, keys, pipelines, budgets, limits or rules in the console writes to Management's
outbox. The **relay** moves it to Kafka and the **consumer** applies it to the gateway's read-model.
If either is not running, the console shows your change and the gateway never learns about it.

The relay is not a daemon — it publishes whatever is pending and exits — so you do not have to keep
a terminal on it. Run it after a change and the consumer picks it up:

```bash
make relay
```

### 3. Two databases, two migration tools

`make seed` migrates the control plane (Django). `make migrate-gateway` migrates the gateway
(Alembic). Neither does the other's work, and a gateway that will not start after you pull is almost
always a missing `make migrate-gateway`.

---

## Running the tests

Four layers, each seeing what the layer below structurally cannot.

```bash
make test              # hermetic: Python + frontend, with the coverage gates CI enforces
make ci                # exactly what CI runs, locally
make mutants           # break each guarded property in turn and require a test to notice
make test-integration  # against the running stack (needs `make up-full` or the app processes)
make test-e2e          # a real browser against the running console
```

`make ci` is the one to run before pushing. It is a thin wrapper around the same targets CI uses, so
the two cannot drift.

**Never lower a coverage gate to make a test pass.** If coverage drops, the missing tests are the
work.

---

## Useful while developing

```bash
make fmt            # ruff format + ruff --fix + prettier --write
make lint           # ruff check, ruff format --check, mypy, prettier --check, ng build
make ps             # what is running and whether it is healthy
make logs-apps      # tail only the application containers
make vault-status   # where the gateway says its secrets came from
curl -s localhost:8001/readyz | jq
```

`readyz` answering `status: ready` with `degraded: false` means Postgres, Kafka and the counter store
all answered. **`degraded: true` with a 200 is a deliberate state**, not a failure: the gateway still
serves, with a named control on a fallback.

---

## Starting over

```bash
make down-full          # stop, keep the data
make destroy            # stop and delete the volumes
```

---

Next: [Showcase](showcase.md) · [Standalone](standalone.md) · [Integrated](integrated.md)
