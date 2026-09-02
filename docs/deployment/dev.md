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
make seed              # control plane: migrate, then create the role groups and demo users
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

## Sending telemetry somewhere else as well

A second destination beside Grafana — your SIEM, a datalake, another collector — is two variables
in `deploy/compose/.env` and no extra Compose file:

```bash
AIRA_OTEL_FORWARD_CONFIG=/etc/otelcol-contrib/forward.yaml
AIRA_OTEL_FORWARD_ENDPOINT=http://t-siem-otel:4318
```

Then recreate the collector. It merges `otel/collector-forward.yaml` on top of its own
configuration — **fan-out, not replacement**, so Grafana keeps working and an unreachable endpoint
costs you nothing you already had. What arrives there is OTLP/JSON; `docs/INTEGRATIONS.md` §6 says
what that shape actually is before you plan a parser around it.

**And you can look at it without having a receiver.** `make otlp-inspector` starts one in the
`debug` profile — set `AIRA_OTEL_FORWARD_ENDPOINT=http://otlp-inspector:4318` and the page shows
every batch that leaves: the spans with their `aira.*` attributes, the content type, and whether a
credential was on the request (`FRD-618`). `make otlp-inspector-down` when you are done.

**A destination that is not a plain OTLP receiver** varies on seven axes — transport (HTTP or
gRPC), encoding, per-signal URLs, the credential's header name, its kind (header · basic · OAuth2),
a client certificate, and compression. Each is a variable or a fragment;
[`INTEGRATIONS.md` §6](../INTEGRATIONS.md#6-observability) has the table and three worked shapes.

**Sending telemetry *to* this stack needs nothing at all.** The collector takes OTLP on 4317 (gRPC)
and 4318 (HTTP), protobuf or JSON — any conformant producer can point at it.

This used to be a fourth Compose file called the *laboratory overlay*, on the argument that the
Collector merges nothing so a second destination needed a whole second configuration. Measured
against collector-contrib 0.157 on 2026-09-02, that is no longer true: repeated `--config` flags
merge deeply enough to keep each pipeline's receivers and processors. The copy went, and with it
the two configurations drifting apart.

### Did it arrive?

```bash
make otel-status     # accepted / refused / forwarded / undelivered, and the reason when it failed
make otel-arrivals   # follow what the collector receives
```

**"No errors" and "it arrived" are different statements**, and only one of them can be read off a
log: the collector logs a delivery failure and says nothing at all about a success, at any level.
`otel-status` therefore reads its own counters and prints the reason underneath. Measured against
a name that does not resolve:

```
Why, in the collector's own words:
  … Post "http://t-siem-otel:4318/v1/traces":
  dial tcp: lookup t-siem-otel on 127.0.0.11:53: no such host
```

`undelivered` counts a *give-up*, not an attempt, and the retry sender does not give up until
`max_elapsed_time` — so for the first five minutes an unreachable endpoint reads as zero rather
than as a failure. That is stated here because a zero is the one thing a table cannot explain.

Three things worth knowing, all of them measured rather than assumed:

- **Adding DNS settings does not cost you the internal network.** On a user-defined bridge, every
  container resolves through Docker's embedded server at `127.0.0.11`, and `dns:` / `dns_search:`
  set what *it* forwards to and appends — they do not replace it. With `--dns 9.9.9.9` a container
  on this stack still resolved `postgres` and `otel-collector` by name.
- **`host.docker.internal` needs `extra_hosts` to be usable.** Without it the embedded resolver
  answered `fe80::1` here — an IPv6 link-local address the collector cannot reach — and every
  forward failed with `EOF` *after* the connection appeared to open, which is the diagnosis that
  starts at the wrong end. The collector service carries the `host-gateway` mapping for that
  reason.
- **An endpoint that does not exist fails quietly.** An exporter with nowhere to send does not
  stop; it retries with growing backoff while holding telemetry in memory, and the only symptom is
  a line in a log nobody reads. `make otel-status` is how you find out.

What the telemetry actually contains — and why a SIEM wants something else — is
[`FRD-615`](../features/FRD-615-a-trace-crosses-the-bus.md) §9 and
[`FRD-616`](../features/FRD-616-the-audit-trail-as-an-event-stream.md).

---

Next: [Showcase](showcase.md) · [Standalone](standalone.md) · [Integrated](integrated.md)
