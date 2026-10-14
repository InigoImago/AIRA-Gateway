# Deployment 2 of 4: Development

**For:** changing the code and seeing the change immediately.
**You need:** Docker, `make`, Python 3.14 with [uv](https://docs.astral.sh/uv/), Node 26.
**Shape:** infrastructure in containers, the application processes from source with
reload-on-save.

If you only want to *run* AIRA rather than change it, use [Standalone](standalone.md) instead — it
runs everything in containers.

---

## Step 1: the toolchain

The versions are pinned, and they are pinned because a mismatch produces failures that look like
code defects.

```bash
python3 --version  # 3.14.x
node --version     # 26.x
uv --version
docker version
```

Missing something:

| Tool | Install |
|---|---|
| Python 3.14 | `uv python install 3.14` |
| uv | <https://docs.astral.sh/uv/getting-started/installation/> |
| Node 26 | `cd management/frontend && nvm install` (the `.nvmrc` is there) |

---

## Step 2: dependencies

```bash
make sync
```

This installs the Python workspace (three packages: `aira_common`, `aira_gateway`,
`aira_management`) and the console's npm dependencies. It is safe to re-run. The browser suite
under `e2e/` has dependencies of its own: `make test-e2e` installs them when it runs, and
`make lint`, which type-checks that suite, needs them there already — `cd e2e && npm ci` once.

---

## Step 3: infrastructure

```bash
make up
```

Starts Postgres, Keycloak (which imports the development realm on its first start), Kafka, the
schema registry, Redis, Vault in development mode, the OpenTelemetry collector and Grafana — and
**not** the application containers, because you are about to run those from source.

Wait until everything is healthy:

```bash
make ps
```

---

## Step 4: prepare the two databases

Both planes have their own database and their own migration tool. Run all three; each is
idempotent.

```bash
make seed              # control plane: migrate, then seed the demo
make migrate-gateway   # gateway: Alembic migrations
make kafka-topics      # the nine compacted config topics, in this stack's broker
```

`make seed` creates the role groups and the demo accounts, the four showcase use cases with their
members, budgets, limits, rules, pipeline and keys, and the question catalogue's use case. All of
it reaches the gateway as outbox events, so it arrives once the consumer runs and `make relay` has
published it (Step 5).

**`make kafka-topics` is not optional.** Auto-creation is switched off, and the relay publishes the
outbox in order: the first event for a topic that does not exist stops the run, and every later
event waits behind it ([`DEPLOYMENT.md` §3.3](../DEPLOYMENT.md#33-kafka)).

---

## Step 5: run the processes

The settings both planes read have two defaults the Make targets do not override, and Compose sets
them only for its own containers. In the gateway's and Management's terminals:

```bash
export AIRA_ROLE_GROUPS='global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung'
export AIRA_OIDC_ISSUER=http://localhost:8080/realms/aira    # the Keycloak port, if you moved it
```

Without `AIRA_ROLE_GROUPS` nobody holds a role — `admin` included — because a role comes only from
the group the mapping names. Without `AIRA_OIDC_ISSUER`, Management can verify no
token, and the console signs nobody in. (`make run-gateway-oidc` sets the gateway's issuer itself.)
For the console's directory search, and for binding a role to a group, give Management the
development realm's read-only directory client as well: `AIRA_DIRECTORY_CLIENT_ID=aira-directory`,
`AIRA_DIRECTORY_CLIENT_SECRET=aira-directory-dev-secret`, `AIRA_DIRECTORY_URL=http://localhost:8080`.

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

Run `make relay` once now, to carry what `make seed` wrote. Then open <http://localhost:4200> and
sign in as `admin` / `demo-password`.

---

## The three things that will catch you

### 1. Use `run-gateway-oidc`, not `run-gateway`

The console sends the browser's bearer token to the *gateway* for every view whose data the gateway
holds — requests and their content, reporting and the register, security findings and suspensions,
consumption, the model lists and checks, pipeline tests and the question catalogue's runs. The
gateway needs OIDC enabled to validate that token; without it those views are refused, and you will
spend ten minutes wondering why.

### 2. The relay carries configuration; nothing happens without it

Changing members, keys, pipelines, budgets, limits or rules in the console writes to Management's
outbox. The **relay** moves it to Kafka and the **consumer** applies it to the gateway's read-model.
If either is not running, the console shows your change and the gateway never learns about it — and
a use case the gateway has not heard of answers `403` *"not known to this gateway"*.

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

Each layer sees what the one below structurally cannot.

```bash
make test              # hermetic: Python + console, with the coverage gates CI enforces
make ci                # lint, types and unit tests — the hermetic half of CI
make mutants           # break each guarded property in turn and require a test to notice
make test-integration  # against the containerised stack
make test-e2e          # a real browser against the running console
```

`make ci` is the one to run before pushing. CI runs the same commands, written out in its own jobs
and held to these targets by a test, and runs the integration and browser suites as well, each on
a stack of its own. The live suites need the containerised stack, not the processes above:
`make up-full`, `make wait-healthy` and `make demo-ready` first. **`make test-integration` calls a
paid cloud model when the stack has Vertex credentials** — [`TESTING.md`](../TESTING.md) says which,
and what each layer needs.

**Never lower a coverage gate to make a test pass.** If coverage drops, the missing tests are the
work.

---

## Useful while developing

```bash
make fmt            # ruff format + ruff check --fix + Prettier --write, console and browser suite
make lint           # ruff check, ruff format --check, mypy, Prettier, the production build,
                    # and Prettier + tsc --noEmit on the browser suite
make ps             # what is running and whether it is healthy
make logs           # follow the containers — here, the infrastructure
make vault-status   # where the gateway says its secrets came from
curl -s localhost:8001/readyz | jq
```

`make logs-apps` follows the application *containers*, and in this setup the applications are your
terminals, so it shows nothing.

`readyz` answering `status: ready` means Postgres answered; `degraded: false` means Kafka, the
counter store and every configured upstream did too. **`degraded: true` with a 200 is a deliberate
state**, not a failure: the gateway still serves, with a named control on a fallback. Locally the
whole body is shown, and the gateway's API documentation is at <http://localhost:8001/docs>.

---

## Starting over

```bash
make seed-reset         # seed the demo again from scratch (seed_demo --fresh), then `make relay`
make down               # stop, keep the data
make destroy            # stop and delete the volumes — the audit trail, the configuration, the realm
```

---

## Sending telemetry somewhere else as well

The run targets export to the collector (`AIRA_OTEL_ENABLED=true`), and Grafana at
<http://localhost:3000> carries a provisioned dashboard, *AIRA — what went through, and what it
did*. A second destination beside Grafana — your SIEM, a datalake, another collector — is two
variables in `deploy/compose/.env` and no extra Compose file:

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
credential was on the request. `make otlp-inspector-down` when you are done.

**A destination that is not a plain OTLP receiver** varies on seven axes. Five are variables:
- the transport (HTTP or gRPC);
- the encoding;
- the credential header and its name;
- a client certificate;
- compression.

The other two are recipes in `deploy/compose/otel/recipes/`:
- a path for each signal;
- a credential other than a header.

Splunk HEC is a recipe too. [`INTEGRATIONS.md` §6](../INTEGRATIONS.md#6-observability) has the
table and the worked shapes.

**Sending telemetry *to* this stack needs nothing at all.** The collector takes OTLP on 4317 (gRPC)
and 4318 (HTTP), protobuf or JSON — any conformant producer can point at it.

### Did it arrive?

```bash
make otel-status     # accepted / refused / forwarded / undelivered, and the reason when it failed
make otel-arrivals   # follow what the collector receives
```

**"No errors" and "it arrived" are different statements**, and only one of them can be read off a
log: the collector logs a delivery failure and says nothing at all about a success, at any level.
`otel-status` therefore reads its own counters and prints the reason underneath. Against a name
that does not resolve:

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
[`INTEGRATIONS.md` §6](../INTEGRATIONS.md#6-observability). In short: the delivery channel carries a
span per request and per model call, sampled like every other span, while the audit trail itself —
the request rows, the record of every content read, the findings and the suspensions — stays in the
gateway's database and is sent nowhere as events.

---

Next: [Showcase](showcase.md) · [Standalone](standalone.md) · [Integrated](integrated.md)
