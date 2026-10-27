# Deployment

How to bring AIRA up — standalone on one machine, or connected to infrastructure you already
run — and how to keep it running. Every value in here is taken from the code; where something is
not implemented, this document says so rather than describing an intention.

- [0. The runbook — an integration, in order](#0-the-runbook--an-integration-in-order)
- [1. What actually runs](#1-what-actually-runs)
- [1a. Sizing: what one instance carries](#1a-sizing-what-one-instance-carries)
- [1b. Releasing: cutting a version and publishing the images](#1b-releasing-cutting-a-version-and-publishing-the-images)
- [2. On one machine](#2-on-one-machine)
- [3. Integrating with existing infrastructure](#3-integrating-with-existing-infrastructure)
- [4. Configuration reference](#4-configuration-reference)
- [5. Preparing Keycloak](#5-preparing-keycloak)
- [6. Production checklist](#6-production-checklist)
- [6a. Upgrading](#6a-upgrading)
- [6b. Backups and restore](#6b-backups-and-restore)
- [6c. When the control plane is down: the break-glass key](#6c-when-the-control-plane-is-down-the-break-glass-key)
- [7. Known gaps](#7-known-gaps)

---

## 0. The runbook — an integration, in order

**§3 below is the reference — what each system must provide. This is the procedure**: the order to
do it in, and the command that says whether each step worked. Every step ends with something you
can run, because a step whose success you cannot check is a step you will do twice.

The order is not arbitrary, and §9 of [`INTEGRATIONS.md`](INTEGRATIONS.md) gives the reasoning:
**prices before budgets** (a cost budget refuses a model with no price, §3.5), **alert before
block** (a detection rule that blocks wrongly once is switched off for ever), and **every caller
names a use case from the start**: `AIRA_REQUIRE_USE_CASE` is on by default and refuses to be
switched off anywhere but `local`, because a request with no use case has nothing to budget, limit
or attribute. The break-glass key is the one credential exempt from it, because it exists for when
Management itself is what is broken (§6c).

### Step 0 — one file, before you touch anything

Everything below is driven by one YAML file naming every external system, with **no secrets**.
Copy the example closest to your case and edit it:

```bash
cp config/integrated.example.yaml config/my-installation.yaml
$EDITOR config/my-installation.yaml
```

> Everything in `config/` except the three examples is invisible to git, on purpose: an
> installation's hostnames, project ids and account names are not secrets in the sense Vault means,
> and are exactly what should not reach a public repository by being dropped in a folder.

**Check it before it reaches a machine.** An environment that is not `local` turns on a list of
hardening checks in each plane, and for most deployments the first time all of them are met is when
a container exits during a maintenance window:

```bash
make config-check CONFIG=config/my-installation.yaml
```

It renders the file and hands it to the gateway's and Management's own `unsafe_settings`, in a
subprocess holding nothing but that environment. The outcomes are kept apart on purpose:

| Output | Means | Exit |
| --- | --- | --- |
| `accepts this configuration` | that plane would pass its start-up checks | 0 |
| a line marked `!` | the file's own problem | 1 |
| a line marked `·` | a credential the file deliberately does not carry; **Vault** supplies it, and it is not counted against the file | 0 when nothing else is wrong |
| `error: …` before any plane is asked | the file does not render: not a mapping of sections, or a credential written into it | 2 |
| *this machine cannot use it* | the file declares a Vault this machine cannot reach or authenticate to. Not a pass and not the file's fault | 3 |

**The probe receives only the rendered environment**, so it reaches Vault only when the file names
`vault.secret_id_file` and that path is readable where the check runs. Otherwise a file that
declares Vault exits 3; `uv run python tools/config_check.py config/my-installation.yaml
--without-vault` checks everything else (`make config-check` cannot pass the flag). With it, the
shipped `integrated.example.yaml` is refused for exactly two things, both marked `·`:
`AIRA_POSTGRES_PASSWORD` (the gateway's check) and `AIRA_SECRET_KEY` (Management's).

**What the check does not see.** It runs the start-up checks and the settings' own validators. What
the gateway checks only when it builds its upstreams — every configured model's region against
`AIRA_ALLOWED_REGIONS`, the Vertex model specs and credentials, the Foundry deployments — and the
CORS refusal surface at start-up and not here. Nor does any start-up check ask for every credential:
the Kafka SASL password, the directory client's secret or a model platform's key are needed for the
deployment to *work*, not for it to *start*. The example's `secrets.in_vault` lists every credential
setting there is.

### Step 1 — PostgreSQL: two databases

Names and the split are in [§3.1](#31-postgresql). AIRA never shares a table between the planes, so
two databases on one server is the normal arrangement. Under the shipped Compose files both planes
connect as **one role** (§3.1), so give it rights on both.

```sql
CREATE DATABASE aira_gateway;
CREATE DATABASE aira_mgmt;
```

**Check:** the migrations are the check. `gateway-migrate` and `management-migrate` are the first
things to connect when you deploy (Step 6), and a wrong host, database, role or password stops them
with the database's own error before any service starts.

### Step 2 — Keycloak: realm, groups, client, **and the audience mapper**

[§5](#5-preparing-keycloak) has the detail and [`INTEGRATIONS.md` §2](INTEGRATIONS.md) has the
group model. Three things, and the third is the one that bites:

1. **Groups decide roles.** `AIRA_ROLE_GROUPS` maps a Keycloak group path to each built-in role; a
   deployment that names no group for `global-admin` is refused at start-up, because nobody could
   administer it.
2. **A client for the console**, with the console's URL in its redirect URIs and web origins.
3. **An audience mapper on that client.** Keycloak does **not** add one by itself. Without it the
   token carries no `aud` naming `AIRA_OIDC_AUDIENCE`, both planes refuse it, and the console —
   after signing you in again a few times — stops and says *"Signing you in is not the problem"*:
   the identity provider accepts you and this installation refuses the token it issues.

**Check** — the token, not the configuration:

```bash
curl -s "$ISSUER/.well-known/openid-configuration" | jq -r .issuer
# then, with a real token from the console's own login:
python3 -c 'import sys,json,base64; t=sys.argv[1].split(".")[1];   print(json.loads(base64.urlsafe_b64decode(t+"==")))' "$TOKEN" | grep -o "'aud'[^,]*"
```

`aud` must name what `AIRA_OIDC_AUDIENCE` says. If it does not, the mapper is missing.

### Step 3 — Kafka: nine compacted topics

Names and settings in [`INTEGRATIONS.md` §3](INTEGRATIONS.md). They must be **compacted**: the
gateway's read-model is built by replaying them, so a topic that expires its records loses
configuration rather than history.

Authentication is not optional outside `local`: a deployment with
`AIRA_KAFKA_SECURITY_PROTOCOL=PLAINTEXT` is refused at start-up, because every configuration change
would cross the broker in the clear and anybody who can reach it could publish their own.

**Check:** `make config-check` covers the setting. **Nothing in AIRA checks the topics before they
are used**: `/readyz` asks only whether a broker answers. List them with your broker's own tooling —
each one present, under the stage prefix if you set `AIRA_KAFKA_STAGE`, with
`cleanup.policy=compact`. A missing topic stops the relay at the first event for it (§3.3).

### Step 4 — one model platform

Whichever you have ([`INTEGRATIONS.md` §5](INTEGRATIONS.md)). One is enough to start; the catalogue
is filled in Step 7. Every model's region is checked against `AIRA_ALLOWED_REGIONS` at start-up;
Google AI Studio is `global` and needs that region named.

### Step 5 — Vault, and the secrets, before anything is deployed

Both planes read Vault **once, at start-up**, and a configured Vault that cannot be used stops the
process rather than falling back to the environment. So the secrets go in before Step 6.

The config file withholds every credential: `AIRA_SECRET_KEY` and `AIRA_POSTGRES_PASSWORD` always,
and whichever of the others your installation uses — the Kafka SASL password, each plane's own
database and Kafka passwords where it has them, the directory client's secret, the model platforms'
keys, a Redis password (the example's `secrets.in_vault`). Vault ranks
**above** the environment on purpose, so a value written there wins over anything a `.env` holds.
[`deployment/integrated.md` §4](deployment/integrated.md) has the procedure, including how the
AppRole's secret-id reaches the containers — which Compose does not do for you.

**Check:** once the services run, `/readyz` asked with an operator's credential (Step 6) reports
`"source": "vault"` under `secrets`, with the key **names** Vault supplied.

### Step 6 — deploy the product

```bash
uv run python tools/config_render.py config/my-installation.yaml -o deploy/compose/.env
make up-integrated RELEASE=vX.Y.Z    # the published images, against the infrastructure above
```

`make current-version` prints the newest release. `up-integrated` starts the applications and no
infrastructure at all: six processes and the two migrations that go before them. See §1b *Running
one*, and [`deployment/integrated.md` §5a](deployment/integrated.md) for the six addresses it then
requires rather than defaults.

**Not `make up-apps`.** That target starts the applications together with this repository's
infrastructure file, whose Keycloak imports the development realm — six accounts with published
passwords, a global administrator among them — and whose Vault runs in `-dev` mode with the root
token `root`. It is the product on development infrastructure, for a machine nobody else can reach.
`docker-compose.apps.yml` itself carries nothing for the demo; the infrastructure file beside it
does.

**Check**, and this is the one that matters:

```bash
make config-verify        # the deployment runs what the file says, or it says where it does not
curl -s https://gateway.example.com/readyz -H "Authorization: Bearer $TOKEN" | jq
```

`config-verify` is not an errand. Compose fills every gap it is given from `${VAR:-default}`, so a
value left empty, a variable your file does not name, a `.env` edited afterwards or a source edited
without re-rendering all end the same way: a stack that starts, healthy, on a value nobody chose.
`make up-integrated` runs the check before starting — and starts anyway, so read what it says.

`/readyz` answers everybody with the verdict and `degraded`; outside `local` and demo mode the rest
of the body — the checks, the upstreams, the fallbacks, where the secrets came from — goes only to
an operator: a token whose holder has `operations.diagnose` (IT Security, a Global Administrator,
or a role you define with it) or the break-glass key (§6c).

### Step 7 — governed, then defensible

Then follow [`INTEGRATIONS.md` §9](INTEGRATIONS.md): Redis for shared counters, prices and
capabilities in the catalogue, the retention worker on a schedule, OTLP, and only then budgets,
rate limits, and anomaly rules in **alert** first.

### What this runbook does not cover

- **Several gateway instances.** The shape is right — the API, the Kafka consumer, the retention
  worker and Management's relay are separate containers, `/readyz` distinguishes *degraded* from
  *not ready*, and no session means no sticky sessions. Two gaps remain: Compose's fixed
  `container_name` blocks `--scale`, and the schema rule for two versions serving at once (§6a) is
  stated and not enforced. Management stays a single instance. **If availability is a requirement,
  both gaps are yours to close before the cutover**: run the gateway somewhere its container name
  is not fixed, and hold every migration to that rule.
- **A stream falling back mid-answer.** Conditions are checked before dispatch, and a chain moves to
  its next candidate until the first chunk is on the wire; after that it is not retried.

---

## 1. What actually runs

AIRA is six processes plus infrastructure, and two one-shot migrations that run before them. Two
processes serve the APIs, one is the console, and three are workers.

| #   | Process                            | Command                                                  | Listens    | Required                                                                                   |
| --- | ---------------------------------- | -------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------ |
| 1   | **Gateway API** (data plane)       | `uvicorn aira_gateway.main:app`                          | HTTP :8001 | yes                                                                                        |
| 2   | **Management API** (control plane) | `uvicorn aira_management.config.asgi:application`        | HTTP :8002 | yes                                                                                        |
| 3   | **Config consumer**                | `python -m aira_gateway.consumer.worker`                 | —          | **yes** — the gateway serves a use case only once its configuration has arrived            |
| 4   | **Outbox relay**                   | `python manage.py relay`, scheduled                      | —          | **yes** — without it no configuration reaches the gateway                                  |
| 5   | **Retention pruner**               | `python -m aira_gateway.retention`, scheduled            | —          | **yes** — nothing deletes stored prompts otherwise                                         |
| 6   | **Console** (SPA)                  | the built bundle behind nginx, which proxies `/api` and `/gw` | HTTP :8080 | yes, for the UI                                                                       |

The migrations are `alembic upgrade head` (gateway) and `python manage.py migrate` (Management).

Infrastructure:

| Component                           | Required                                         | Used for                                                                                       |
| ----------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| **PostgreSQL**                      | **yes**                                          | two databases, `aira_gateway` and `aira_mgmt`; the only dependency readiness waits for          |
| **Keycloak** (or any OIDC provider) | **yes**                                          | sign-in, and the groups that confer roles and reach use cases                                  |
| **Apache Kafka**                    | **yes**                                          | configuration from Management to the gateway, nine compacted topics                            |
| Redis                               | recommended; needed for exact limits with more than one gateway process | shared rate-limit buckets and budget reservations; without it limits hold per process and budgets use a racy path |
| HashiCorp Vault                     | when `VAULT_ADDR` and a role id (or a token) are set | every credential, read once at start-up and ranked above the environment                     |
| OpenTelemetry collector             | optional                                         | traces, metrics and logs (`AIRA_OTEL_ENABLED`), and the delivery channel to a SIEM             |
| Schema Registry                     | no                                               | runs in the bundled stack; **no code uses it** — events are plain JSON with an `event_type` header |

All six run from **three images**, built from this repository:

| Image             | Dockerfile                       | Runs                                                                               |
| ----------------- | -------------------------------- | ---------------------------------------------------------------------------------- |
| `aira-gateway`    | `gateway/Dockerfile`             | the gateway, the config consumer, the retention pruner, and `alembic upgrade head` |
| `aira-management` | `management/backend/Dockerfile`  | the API, `manage.py migrate`, and the outbox relay                                 |
| `aira-frontend`   | `management/frontend/Dockerfile` | the built SPA behind nginx, which also proxies `/api` and `/gw`                    |

Both Python images are multi-stage (build with `uv`, ship only the resolved virtualenv), run as a
non-root user (uid 10001) and carry a `HEALTHCHECK`. The worker containers switch that check off,
because they serve no HTTP, and in Compose their loops carry on after a failed run (`|| true`) —
their logs are where a failure shows.

Two pieces of background work run **inside every gateway process**, not in a container of their
own: the anomaly evaluator (one evaluator per tick across all of them, claimed by a PostgreSQL
advisory lock) and the probe of every configured upstream that `/readyz` reports.

### How the two planes talk

The planes never call each other over HTTP. Management writes to a **transactional outbox** in its
own database; the **relay** publishes those rows to Kafka; the gateway's **consumer** applies them
into local read-model tables. A change made in the console therefore becomes effective in the
gateway only after the relay has run and the consumer has applied it; the Compose stack runs the
relay every ten seconds.

```
Management API ──▶ outbox table ──▶ relay ──▶ Kafka ──▶ consumer ──▶ gateway read-model
                                  (scheduled)  (9 compacted topics)
```

The **console** is the exception: it calls the gateway directly, through its own `/gw` proxy, for
every view whose data the gateway holds — the requests and their stored content, reporting and the
register, findings, suspensions and content reads, a use case's consumption, the model lists and
reachability checks, pipeline dry runs, and the question catalogue's model tests. That is why the
gateway needs `AIRA_OIDC_ENABLED`: without it those views are refused.

---

## 1a. Sizing: what one instance carries

Measured against a free upstream double, so the figures are the **gateway's own** cost with the
model's latency subtracted. The runs were taken on one eight-core machine that also ran the rest of
the stack, the double and the load driver, so an absolute throughput figure is about that machine;
the figures that carry over to yours are per request and per core. The raw runs and their reports
are in [`docs/measurements/`](measurements/), and `make loadtest` takes a fresh set
([`TESTING.md`](TESTING.md)).

**One uvicorn worker is one core, and the image starts one.** `uvicorn aira_gateway.main:app` with
no `--workers` is a single process and a single event loop; throughput scales linearly with worker
count (43 → 80 → 125 requests a second at 1, 2 and 4 workers) and does not scale without being
told to. The request path is stateless and the shared counters are in Redis, so workers and
instances are interchangeable. The consumer, the retention worker and Management's relay do not
scale with them and stay at one each: each already works for every instance at once — the consumer
fills the read-model all of them read, the pruner clears expired payloads from the one audit trail
all of them write — and relay runs never overlap (§6).

**How to ask for more.** With no `--workers`, uvicorn reads `WEB_CONCURRENCY` from the environment —
which `docker-compose.apps.yml` does not pass, so under Compose more workers means overriding the
gateway's `command` or `environment` in a file of your own (the load-test overlay does it with
`AIRA_GATEWAY_WORKERS`). More gateway *instances* are blocked in Compose by the fixed
`container_name`.

### What a request costs

| Workload | Gateway CPU per request |
| --- | --- |
| chat with a retrieved context (4 000 tokens in, 220 out, streamed) | **48 ms** |
| agentic coding (long prompt, tools, thinking, token-by-token stream) | **93 ms** |
| embedding, per text in a batch | **0.59 ms** |
| idle background work, **per worker** | 0.16–0.23 cores |

The cost follows **streamed events**, not requests: the same chat workload from a runtime that
packs eight tokens into an SSE event instead of two cost 31% less CPU. Prompt size, tools and
thinking all move the figure; the shape of the upstream's stream moves it most.

### Turning that into instances

Multiply the seats by how often a seat asks. Measured: an agentic seat issues 0.066 requests a
second, a chat seat 0.044.

    cores of request work  =  seats × requests-per-second-per-seat × CPU-per-request

**Then size for about half that per worker, not all of it.** A single event loop degrades well
before its core is full — at 66% utilisation the chat workload's median overhead went from 74 ms to
763 ms and its 99th percentile to 16.5 s — because each request's work arrives in one
unpreemptible burst and time to first token is what waits.

Worked, for 300 agentic and 300 chat seats at once: 1.83 + 0.64 = **2.5 cores of request work**, so
**six gateway cores** — two instances of four workers, or four of two, once several instances are
possible (above). Four workers were measured carrying 300 agentic seats at 2.72 cores and already
lengthening.

### Three limits to set deliberately

- **Upstream connections.** Every adapter builds its HTTP client on the library default: **100
  connections, 20 kept alive, per worker**. Long answers mean high concurrency, so this binds
  before the processor does — measured, a worker plateaued at 5.8 requests a second while using
  **0.21 cores**, purely waiting for a free connection, and the median doubled when callers
  outnumbered the pool. Count the concurrent calls you expect (`seats × answer-seconds ÷
  cycle-seconds`) and put enough workers or instances behind them.
- **Postgres connections.** Each worker keeps its own pool (SQLAlchemy's default: 5, plus 10
  overflow). Eight workers is 120 connections before Keycloak, Management, the consumer and the
  retention worker have asked for theirs, against a stock `max_connections` of 100.
- **The audit volume.** See below; it is the number that surprises.

### What an instance past its capacity looks like

**Nothing is refused.** The gateway queues and slows: no 429, no 503, latency climbing and the
99th percentile climbing faster. It looks exactly like a slow model. The first internal sign is the
audit queue saturating — `request_log_queue_full` in the gateway's log, after which rows are
written on the request path as designed backpressure. That log line is currently
the **only** exposure of it; there is no metric.

Memory is not a constraint: 300 concurrent streamed answers held one instance at 152 MB.

### The audit trail is what fills the disk

Measured per row, with payload storage on (the default) and prompts with the entropy of prose:

| Workload | Bytes per audit row |
| --- | --- |
| chat with a retrieved context | **17.7 kB** |
| agentic coding | **28.3 kB** |
| agentic with the reasoning returned | **32.5 kB** |

At 300 agentic plus 300 chat seats that is roughly **68 GB a day**. Stored prompts are pruned per
use case (`retention_days`, 7 by default, and only if the retention worker runs); **rows are never
deleted** — `AIRA_LOG_RETENTION_DAYS` is 0 and means *never*, so the spend history survives. Plan
for a payload working set of several hundred gigabytes and a row count that only grows, or switch
payload storage off for the use cases that do not need it (`AIRA_STORE_PAYLOADS`, or per use case).

### Sizing a rate limit for batch work

A rate limit counts a request's **weight**, and an embedding batch weighs its own size. An
allowance of 600 "requests" a minute is 4.7 batches of 128 texts, and a **burst** below
the batch size refuses that request outright however long the caller waits. For a nightly
re-embedding, size the allowance in **texts** per minute and the burst at or above the largest
batch.

---

## 1b. Releasing: cutting a version and publishing the images

**A release is an annotated Git tag `vX.Y.Z`, its message is the release notes, and pushing it
publishes the three images.** The version lives in the tags and nowhere else — there is no
`CHANGELOG.md`, and the `0.1.0` in the three `pyproject.toml` files and in each package's
`__version__` (`0.0.0` in `package.json`) is a placeholder no release changes. It
is still what telemetry reports as `service.version` and what the gateway's API document calls its
version, so neither says which release is running; the image reference does (*Running one*, below).

```bash
make current-version                      # the newest release tag
make next-version                         # what a patch release would be
make next-version BUMP=minor              # or minor, or major

make release-tag VERSION=vX.Y.Z           # opens your editor, then checks what you wrote
git push origin vX.Y.Z                    # this is the release
```

`make release-tag` creates the annotated tag, validates the message, and **deletes the tag again if
it does not pass** — a rejected attempt must not consume a version number. It then prints the notes
the release would publish. Pushing is the irreversible step and stays a separate command.

### The message is the release notes, and it is checked

A release mechanism with nowhere to put a description produces releases that say nothing — `v0.1.0`
carries the annotation `Release v0.1.0`. Because the message is now load-bearing, it is **refused if
it says nothing**:

| Rule | Why |
| --- | --- |
| the tag is **annotated** | `git tag v0.1.1` leaves no message at all, and the difference from `git tag -a` is invisible in `git tag --list` |
| there is a **subject** | the line that appears in every list, and the Release's title: `vX.Y.Z — subject` |
| the subject does not **restate the version** | `Release v0.1.0` and `AIRA Gateway v0.1.1` tell a reader exactly what the version number already told them |
| there is a **body** — anything after the subject line | a headline is what the reader already has; the body is what changed and whom it affects. Leave a blank line between them, as Git does; the check splits at the first line either way |
| no **placeholder** from the template survives | an unfilled template is the right shape with nothing in it |

**You do not start from a blank buffer.** `make release-tag VERSION=vX.Y.Z` opens your editor on
[`RELEASE-TEMPLATE.txt`](RELEASE-TEMPLATE.txt) with the version substituted. It asks the two
questions a reader of the previous version has, in that order — *what changed*, and *do I have to
do anything* — and its guidance lines start with `#`, so `git tag --cleanup=strip` removes them and
they are never published. **That removes every line starting with `#`**, a Markdown heading you
type included, which is why the template's headings are plain capitals. Delete any section you have
nothing for; `UPGRADING` is the exception, because *"Nothing to do."* is an answer and a valuable
one.

If the annotation fails a rule, `make release-tag` **deletes the tag it just created**, so a
rejected attempt does not spend a version number. The same refusal is available on its own as
`make release-check VERSION=vX.Y.Z`, and in CI it arrives **within a minute**, from a job with no
dependencies, before any image is built.

> **`v0.1.1`'s Release page carries a commit message as its notes and title.** The tag itself is
> right: the check then read a ref the checkout had flattened to the commit, which the jobs no
> longer allow. To re-publish its page with the tag's own words and the image list the workflow
> adds:
>
> ```bash
> uv run python tools/release_notes.py v0.1.1 \
>   --image ghcr.io/inigoimago/aira-gateway:v0.1.1 \
>   --image ghcr.io/inigoimago/aira-management:v0.1.1 \
>   --image ghcr.io/inigoimago/aira-frontend:v0.1.1 > /tmp/v0.1.1.md
> gh release edit v0.1.1 --notes-file /tmp/v0.1.1.md \
>   --title "v0.1.1 — $(make -s release-subject VERSION=v0.1.1)"
> ```

### What a release does

Pushing the tag runs the whole workflow. The three images are published only if the Python job
(lint, types, unit tests), the console job (format, build, unit tests), the integration job, the
browser job (with the Windows sign-on) *and* the description check all pass on the tagged commit —
the job that pushes the images declares `needs` on all five, and every later release job waits for
it, so a green release is a green commit by construction. A **GitHub Release** is created last,
carrying your words, then the commits since the previous release, then the image references, then
the files attached to it: the OpenCode budget plugin as an archive, and the software bills of
materials below — each beside its `.sha256`, and each, like every image, **signed** (below).

The release is four jobs, cut by what each may write: one checks the description and writes
nothing; one pushes the images and can write packages, running Docker's build actions and nothing
else from outside GitHub; one describes the images and writes nothing; and the last writes the
Release and signs what it names, running GitHub's own actions alone. Every action is pinned to the
commit of a release, no job keeps the token in its clone, and none of them restores a cache.

Each image lands under three tags:

| Tag | For |
| --- | --- |
| `vX.Y.Z` | what a deployment pins. Never moves; a version already released is refused |
| `sha-<commit>` | *"is this instance running what I think it is?"*, without trusting that a version tag was never moved. The reference a rolling update advances to |
| `latest` | a reader trying AIRA out |

```
ghcr.io/<owner>/aira-gateway:vX.Y.Z
ghcr.io/<owner>/aira-management:vX.Y.Z
ghcr.io/<owner>/aira-frontend:vX.Y.Z
```

### What a release contains — the SBOMs

Every release describes its contents twice, for two kinds of reader:

| Where | Format | Describes |
| --- | --- | --- |
| beside each image in the registry | SPDX, written by BuildKit, with the build's provenance | exactly the digest you pulled |
| on the GitHub Release, `aira-<image>-vX.Y.Z.cdx.json` | CycloneDX 1.6 JSON | each image, scanned back from the registry by the digest just pushed |
| on the GitHub Release, `aira-source-vX.Y.Z.cdx.json` | CycloneDX 1.6 JSON | the locked dependencies of the tagged commit |

```bash
docker buildx imagetools inspect ghcr.io/<owner>/aira-gateway:vX.Y.Z --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/<owner>/aira-gateway:vX.Y.Z --format '{{ json .Provenance }}'
sha256sum -c aira-gateway-vX.Y.Z.cdx.json.sha256     # a downloaded file, beside its checksum
```

Read the console's own dependencies in the **source** document, not in `aira-frontend`'s: the console
image holds a compiled bundle, in which no scanner finds the Angular and npm packages it was built
from, so its document lists nginx and the Alpine packages and nothing else. The source document
reads the console's runtime dependencies from its lock file, and Python's from `uv.lock` — which
includes the development tools (`pytest`, `ruff`), because uv locks the workspace whole. The images'
documents are what runs.

The two formats come from two scanners (BuildKit's and Syft), so they can differ slightly in what
each counts as a package. BuildKit's documents beside the images are not signed beyond the
registry's own integrity; the files on the Release are, like the images (below). Releases up to
`v0.1.7` have no documents. Feeding one to a scanner — Dependency-Track, Trivy, Grype — and deciding
what a finding means for an installation is the reader's step:

```bash
grype sbom:aira-gateway-vX.Y.Z.cdx.json
```

### Checking where a file or an image came from

A `.sha256` proves that a download is the file that was uploaded — not who uploaded it. Every
image a release pushes and every file it attaches also carries a **signed build provenance**: a
statement of which workflow run, commit and tag produced it, signed with a short-lived certificate
issued to that run and recorded in a public transparency log. The GitHub CLI checks one, signed in
to GitHub (and, for an image, to the registry while the packages are private):

```bash
v=vX.Y.Z
gh attestation verify "opencode-aira-budget-$v.tar.gz" --repo InigoImago/AIRA-Gateway \
  --signer-workflow InigoImago/AIRA-Gateway/.github/workflows/ci.yml --source-ref "refs/tags/$v"
gh attestation verify "oci://ghcr.io/inigoimago/aira-gateway:$v" --repo InigoImago/AIRA-Gateway \
  --signer-workflow InigoImago/AIRA-Gateway/.github/workflows/ci.yml --source-ref "refs/tags/$v"
```

A file or image that verifies was built by this repository's release workflow from that tag; one
that does not was not, whatever its checksum says. Releases up to `v0.1.7` are not signed.

### Things worth knowing before the next release

- **The bases are lines, and a line ends.** The console is served by nginx's **stable** line
  (`nginx:1.30-alpine`), which its maintainers rebuild whenever Alpine ships a fix, so a release
  build carries the patched OpenSSL and libraries of its day — until nginx publishes the next
  stable line, once a year in spring, and stops rebuilding this one. Move the `FROM` in
  `management/frontend/Dockerfile` when that happens: a check refuses a mainline line, and cannot
  know that a stable one has ended. The installer both Python images are built with is pinned to
  a uv release **and its digest**, and raised by hand in both Dockerfiles at once.
- **Nothing to configure.** GHCR accepts the workflow's built-in `GITHUB_TOKEN` for the
  repository's own namespace, so there is no personal access token and no stored registry secret.
- **The packages start private**, and GitHub does not link them to the repository automatically.
  Making them pullable is a one-time step per package in its own settings page — otherwise the
  first `docker pull` fails for a reason that looks like a bug.
- **A failed release leaves a tag with no Release against it**, which is visible in the one list
  people read. The tag exists before the gates run, and the gate that refuses need not be the
  description: a red suite stops a perfectly good annotation too. The publish job refuses only a
  version that already **has** a Release, so there are two recoveries. Tag the next patch, carrying
  the words over (`git tag -l --format='%(contents)' vX.Y.Z` prints them), which leaves a gap in the
  series; or delete the tag locally and on the remote and tag the fixed commit under the same
  number, which moves a ref somebody may already have fetched. `v0.1.2` was released the second
  way.
- **The images are a rebuild of the tested commit, not the tested bytes.** The release builds
  them again, from nothing — no cache an earlier run left feeds it — so they are that commit's and
  nobody else's, and still not the bytes the test jobs ran. It is a known limit. Closing it means
  handing the images the test jobs built to the release, so that the digests published are the
  digests tested; that is not done.
- **Every action the workflow runs is pinned to a commit**, with its release noted beside it. A tag
  can be moved by whoever holds the action's repository; a commit cannot. Raising one means editing
  both, and a check refuses a tag where a commit belongs.

Refused on purpose: a tag whose commit is not an ancestor of `main`, and a tag older than the newest
release — `latest` would move backwards, so releases here go forward only.

### Running one

Two overlays, one per question — *where do the images come from*, and *whose infrastructure do they
run on*. `docker-compose.release.yml` takes the three images from the registry and **removes**
their `build:`; `docker-compose.external.yml` starts no infrastructure and turns every address a
deployment must supply into a required variable.

```bash
make release-pull RELEASE=vX.Y.Z     # fetch the three images, and nothing else
make up-full RELEASE=vX.Y.Z          # on this repository's infrastructure, demo included
make up-integrated RELEASE=vX.Y.Z    # on infrastructure you provide — §3 and deployment/integrated.md §5a
```

`RELEASE=` is one switch for two things: it layers the overlay on **and** drops the `--build` that
the `up*` targets pass by default. Either half alone is a contradiction. It applies to every target
that starts the applications in containers — `up-apps`, `up-full`, `showcase`, `up-integrated`;
`make up` and `make up-core` start infrastructure only, and the load-test targets run the image
built from this checkout.

`AIRA_VERSION` has no default, so no composition can quietly run `latest` — which moves under a
restart, and a stack that cannot say which version it is running is the state these version numbers
exist to end. To check: `docker inspect --format '{{.Name}} {{.Config.Image}}' $(docker ps -q)`, and
every AIRA container must name the same reference.

**`GET /version-info`** on the gateway (unauthenticated, like its KIRA twin) answers the service
name and environment, and build metadata only when `AIRA_BUILD_NUMBER`, `AIRA_BUILD_TIME`,
`AIRA_GIT_COMMIT` and `AIRA_GIT_BRANCH` reach the gateway's environment. Neither the published images
nor the Compose files set them, so a released image answers `null` there; the image reference is
the answer to *what is deployed*.

---

## 2. On one machine

Everything on one host is described step by step, for somebody doing it for the first time:

- [`deployment/showcase.md`](deployment/showcase.md) — the whole product in containers, with a local
  model and real traffic: `make showcase`.
- [`deployment/standalone.md`](deployment/standalone.md) — the same containers without the traffic:
  `make up-full`.
- [`deployment/dev.md`](deployment/dev.md) — infrastructure in containers, the applications from
  source with reload on save.

What matters operationally about those stacks:

- **They are development-grade.** The infrastructure file's Keycloak imports the development realm
  with published passwords, its Vault runs in `-dev` mode and keeps nothing, and the applications
  run with `AIRA_ENVIRONMENT=local`, which switches every start-up check off.
- **The relay and the retention pruner run as loops** — the relay every `AIRA_RELAY_INTERVAL`
  seconds (default 10), the pruner every `AIRA_RETENTION_INTERVAL` seconds (default 3600) — so
  configuration propagates and payloads expire on their own. Outside Compose they are scheduled
  jobs (§6).
- `make logs-apps` follows the gateway, its consumer, Management, the relay and the console;
  `make logs-apps FOLLOW= TAIL=500` prints once and returns, as CI does on a failure. The retention
  pruner is not in that list: `docker logs aira-gateway-retention`.
- `make build-images` builds the three images without starting anything; `make down` stops
  everything this repository can start and keeps the volumes; `make destroy` deletes them (§6b).
- The live test layers need more than a started stack: `make wait-healthy` and then
  `make demo-ready` before `make test-integration` or `make test-e2e`
  ([`TESTING.md`](TESTING.md)).

---

## 3. Integrating with existing infrastructure

Point AIRA at what you already run. Each subsection lists the minimum you have to provide.

> **This is the reference; [§0](#0-the-runbook--an-integration-in-order) is the procedure.** The
> variables below are set in one place — a `config/*.yaml` rendered into the environment both
> planes read — not service by service. `make config-check` says whether they would be accepted
> before anything is deployed, and `make config-verify` says whether the deployment is using them
> afterwards.

### 3.1 PostgreSQL

AIRA needs **two databases**. They may live on the same server; the gateway and management never
share tables.

```sql
CREATE DATABASE aira_gateway;
CREATE DATABASE aira_mgmt;
```

Both services take the same connection variables and a different `AIRA_POSTGRES_DB` — and, where
each plane has a role of its own, each its own user and password:

| Variable                                        | Gateway          | Management       |
| ----------------------------------------------- | ---------------- | ---------------- |
| `AIRA_POSTGRES_HOST`                            | your host        | your host        |
| `AIRA_POSTGRES_PORT`                            | `5432`           | `5432`           |
| `AIRA_POSTGRES_DB`                              | `aira_gateway`   | `aira_mgmt`      |
| `AIRA_POSTGRES_USER` / `AIRA_POSTGRES_PASSWORD` | the shared role  | the shared role  |
| … or a role per plane, replacing the shared one | `AIRA_GATEWAY_POSTGRES_USER` / `_PASSWORD` | `AIRA_MANAGEMENT_POSTGRES_USER` / `_PASSWORD` |

**One role by default, a role per plane if you set one.** `docker-compose.apps.yml` gives both
planes the same `AIRA_POSTGRES_USER` and fills both planes' `AIRA_POSTGRES_PASSWORD` from
`POSTGRES_PASSWORD` (default `aira-local`), and points both at one Vault path — so with the shared
names alone, a password in Vault is one password, and that role needs `CREATE` on both databases.
For a role per database, set `AIRA_GATEWAY_POSTGRES_USER` and `AIRA_MANAGEMENT_POSTGRES_USER`
(configuration) and write `AIRA_GATEWAY_POSTGRES_PASSWORD` and `AIRA_MANAGEMENT_POSTGRES_PASSWORD`
into the same Vault path: a plane's own name replaces the shared one wherever either was read from,
and a plane never reads the other's. Each role then needs `CREATE` on its own database only. On one
path each plane's AppRole can still read the other's password; a path and an AppRole per plane
(`VAULT_PATH`, `VAULT_ROLE_ID`, set per service) is the stronger separation.

Schema management differs per service — run both on every deployment, before the new code starts:

```bash
cd gateway && alembic upgrade head            # gateway
cd management/backend && python manage.py migrate   # management
```

There is no TLS or connection-pool setting; the URL is assembled in
`GatewaySettings.database_url()` and `aira_management.config.database`.

### 3.2 Keycloak / OIDC provider

Any OIDC provider works in principle — the code verifies a JWT against the issuer's JWKS. What
AIRA _requires from the token_ is specific, see [§5](#5-preparing-keycloak).

```bash
AIRA_OIDC_ISSUER=https://sso.example.com/realms/aira
AIRA_OIDC_AUDIENCE=aira-gateway     # required outside local, see below
AIRA_OIDC_ENABLED=true              # gateway only; management is always OIDC
# AIRA_OIDC_JWKS_URI=...            # only if it is not <issuer>/protocol/openid-connect/certs
```

> **The audience.** With `AIRA_OIDC_AUDIENCE` empty, audience verification is skipped and _any_
> token the issuer minted — including one for an unrelated client — is accepted. Outside `local`
> both planes therefore refuse to start without one: the gateway when OIDC is on (`AIRA_DEMO_MODE`
> waives it there), Management whenever an issuer is set. Where the check is waived the gateway
> logs `oidc_audience_unset` at start-up instead.

The `AIRA_OIDC_JWKS_URI` override exists for a provider that does not follow Keycloak's URL layout,
and for a container that reaches Keycloak at another address than the browser does: the issuer must
match the token's `iss` exactly, the JWKS URL is fetched by the service. **Several realms** at once
are `AIRA_OIDC_ISSUERS` (`issuer|audience|jwks_uri` per entry) — on the gateway only;
Management and the console read one issuer.

### 3.3 Kafka

Nine **compacted** topics carry the configuration. Auto-creation is off in the reference stack, so
create them (partition and replication counts are yours to choose; compaction is not optional — the
gateway's read-model is built by replaying them).

| Topic                | Carries                                                      |
| -------------------- | ------------------------------------------------------------ |
| `aira.usecases`      | use-case create/update/delete                                |
| `aira.memberships`   | membership changes and group grants                          |
| `aira.api-keys`      | API-key issuance and revocation (hash only, never plaintext) |
| `aira.pipelines`     | pipeline configuration                                       |
| `aira.budgets`       | budget definitions                                           |
| `aira.rate-limits`   | request-rate limits                                          |
| `aira.anomaly-rules` | anomaly rules                                                |
| `aira.models`        | model catalog and prices                                     |
| `aira.roles`         | what each stored role may do, and its group                  |

On a cluster several stages share, set `AIRA_KAFKA_STAGE` on both planes: every name above becomes
`aira.<stage>.<entity>` and the consumer group `aira-gateway.<stage>`.

```bash
kafka-topics.sh --create --topic aira.usecases --config cleanup.policy=compact ...
```

`make kafka-topics` does this in this repository's own broker container, and
`make kafka-topics-external` from a one-shot container against `AIRA_KAFKA_BOOTSTRAP_SERVERS` — for a
PLAINTEXT broker only, which nothing outside `local` accepts. A secured broker's topics are created
with your platform's own tooling.

**Create all of them — a missing one stops every change, loudly.** The relay publishes the outbox
in order; a send it cannot make — a topic the broker does not have, after the client's metadata
timeout — stops the run there, and the run fails naming the event and its topic. That event **and
every event after it, on every topic,** stay pending, so no configuration reaches the gateway until
the topic exists. The symptom in the console is a setting that saves and does nothing; the relay's
output says why. An event the broker will never accept because of its size is the one exception: it
is set aside (*parked*) with its reason rather than holding the queue.

**Authentication is configured, and required outside `local`.** Both planes take the same
settings and give the producer and the consumer the same client configuration:

```bash
AIRA_KAFKA_BOOTSTRAP_SERVERS=kafka-1.internal:9093,kafka-2.internal:9093
AIRA_KAFKA_SECURITY_PROTOCOL=SASL_SSL     # PLAINTEXT | SSL | SASL_PLAINTEXT | SASL_SSL
AIRA_KAFKA_SASL_MECHANISM=SCRAM-SHA-512   # empty means SCRAM-SHA-512
AIRA_KAFKA_SASL_USERNAME=aira-gateway     # one broker identity per plane
AIRA_KAFKA_SASL_PASSWORD=…                # a credential: from Vault
AIRA_KAFKA_SSL_CAFILE=/etc/ssl/certs/internal-ca.pem   # empty uses the system trust store
```

The two `SASL_*` lines are one identity for whichever plane reads them. Where both planes share an
environment or a Vault path, give each its own instead — `AIRA_GATEWAY_KAFKA_SASL_USERNAME` and
`_PASSWORD` for the consumer, `AIRA_MANAGEMENT_KAFKA_SASL_USERNAME` and `_PASSWORD` for the relay —
which replace the shared two in that plane.

Outside `local`, `PLAINTEXT` stops both planes at start-up (the gateway waives it under
`AIRA_DEMO_MODE`; Management does not). The check refuses only `PLAINTEXT`: `SASL_PLAINTEXT`
authenticates and does not encrypt, and is accepted. What each identity may do on the topics is in
[`INTEGRATIONS.md` §3](INTEGRATIONS.md) — the bus is a trust boundary, because the gateway builds its
authorization from what arrives there.

The consumer joins the group `aira-gateway` (`aira-gateway.<stage>` with a stage) with
`auto_offset_reset=earliest`, and resumes from the offsets the group has committed — which matters
when a database is restored (§6b).

### 3.4 Observability

```bash
AIRA_OTEL_ENABLED=true
AIRA_OTEL_ENDPOINT=http://otel-collector:4318     # OTLP/HTTP, no trailing path
AIRA_OTEL_SAMPLE_RATIO=1.0                        # by trace id; below 1 also thins what a SIEM receives
```

Traces, metrics and logs are exported over OTLP/HTTP; with `AIRA_OTEL_ENABLED` off (the default)
nothing is, the delivery channel included. Spans carry `aira.*` attributes (subject, use case,
model, tokens, latency); credential-bearing query parameters are redacted before they reach a span.
**Sampling is the installation's**: a request is sampled by its trace id and the ratio, and a
caller's own trace flag decides nothing — its trace id is continued all the same.

### 3.5 Model prices

Cost budgets are calculated from prices maintained under **Models & prices** (`/api/v1/models/`) by
whoever holds `catalog.write` — a Global Administrator, unless a role you define holds it too: per
model, the price of 1,000,000 input and 1,000,000 output tokens, in `AIRA_CURRENCY`. Input and
output must both be set — a one-sided price would produce a cost figure that looks complete and
silently omits half the spend.

**A model with no price is refused wherever a cost limit binds the request** — a cost budget on its
use case or on the person within it, or, for a request that names no use case, on the installation
(`AIRA_ENFORCE_PRICED_MODELS`, on by default). In a fallback chain it is skipped and a priced candidate serves; a request whose
only candidate is unpriced is refused with the reason. Where no cost limit binds, it serves, and
its consumption is counted under `unpriced_requests` and left out of the spend figures rather than
counted as free. Turning the switch off keeps unpriced models serving under a cost budget that then
cannot count them; with budget enforcement off the switch has nothing to protect and does nothing.

### 3.6 Upstream LLM provider

Four adapter families, each configured on its own — Vertex AI in an EU region (§4a), Microsoft
Foundry, any OpenAI-compatible server, and Google AI Studio; the variables are in
[`CONFIGURATION.md` §3](CONFIGURATION.md) and what each platform must provide in
[`INTEGRATIONS.md` §5](INTEGRATIONS.md). A deterministic **mock** provider (`mock-1`) is registered
only with `AIRA_ENVIRONMENT=local` or `AIRA_DEMO_MODE`; anywhere else a gateway without a
configured upstream serves nothing.

```bash
AIRA_GOOGLE_API_KEY=...                                   # registers Google AI Studio
AIRA_ALLOWED_REGIONS=global,eu,europe-west1               # AI Studio is `global`: name it, or the gateway refuses to start
AIRA_GEMINI_MODELS=                                       # empty is the default — see below
AIRA_GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
```

`AIRA_GEMINI_MODELS` names models to offer and is empty by default: the catalogue's discovery lists
what a key actually serves, and a model catalogued under the provider `generative-language` is
served without being named here. `AIRA_GEMINI_BASE_URL` is what you point at a proxy;
the key is sent in the `x-goog-api-key` header — never in a URL, where proxies and access logs keep
it — and is never logged or exported.

### 3.7 Reverse proxy and TLS

The `aira-frontend` image already contains an nginx that serves the SPA and proxies `/api` and
`/gw` (upstreams configurable via `AIRA_MANAGEMENT_UPSTREAM` and `AIRA_GATEWAY_UPSTREAM`). It
listens on **8080** and terminates no TLS — put your own proxy in front of it.

If you serve the SPA yourself instead, route:

| Path     | To                               |
| -------- | -------------------------------- |
| `/`      | the SPA's static bundle          |
| `/api/…` | Management API                   |
| `/gw/…`  | Gateway (strip the `/gw` prefix) |

The gateway's own API (`/v1beta/…`) is normally published separately for API clients — it is the
data plane and does not need to sit behind the SPA's origin.

**The client address in the audit trail.** Behind proxies, decide which address is recorded:

```bash
AIRA_TRUST_FORWARDED_FOR=true    # read X-Forwarded-For at all — only behind proxies you run
AIRA_TRUSTED_PROXY_HOPS=1        # how many of them append to it in front of this service
```

A proxy **appends** the address it saw, so the left end of `X-Forwarded-For` is whatever the caller
sent and is never read: the gateway takes the entry `AIRA_TRUSTED_PROXY_HOPS` places from the right,
and the socket peer when the chain is shorter than that (it did not come through your proxies). Left
at `AIRA_TRUST_FORWARDED_FOR=false` (the default), the socket peer is recorded. Management reads
`AIRA_TRUSTED_PROXY_HOPS` always (default 1, the console's nginx): for the address its
failed-authentication bound counts against, and to decide whether a proxy's `X-Forwarded-Proto`
is believed for HSTS. Add one hop for each proxy of yours that appends in front of nginx.

### 3.8 The SPA is configured at deployment time

`runtime-config.js` ships beside the bundle and is loaded before the app, so one image serves any
realm. **The `aira-frontend` image writes it on every start** from two variables, and derives its
content policy from the first:

```bash
AIRA_OIDC_ISSUER=https://sso.example.com/realms/aira   # the issuer as the browser reaches it
AIRA_OIDC_CLIENT_ID=aira-gateway                       # the console's client in that realm
# AIRA_CSP_CONNECT_SRC=…   only for what the derivation cannot guess: a second origin, a websocket host
```

Left empty, `AIRA_CSP_CONNECT_SRC` is `'self'` plus the issuer's origin — the only thing the console
calls cross-origin. Set explicitly, it replaces that derivation and no longer follows the issuer.
Do not edit the file inside a running container: the next start overwrites it.

When it goes wrong, the console says which half. An issuer it cannot reach at start-up gives *"The
console cannot sign you in"*, naming the issuer address it was given. A token the identity provider
issues and this installation refuses — a missing audience, a mismatched issuer, clocks too far
apart — ends, after a few attempts to sign in again, in *"Signing you in is not the problem"*, with
a button that ends the session at Keycloak as well.

Serving the bundle from a web server of your own, you write `runtime-config.js` yourself
(`window.__AIRA_CONFIG__ = { issuer: …, clientId: … }`) and send a content policy whose
`connect-src` names the issuer's origin: the token request goes to Keycloak cross-origin, so an
issuer the policy does not name produces a login that fails in the browser and nowhere else.

The `redirectUri` derives from `window.location.origin`, so it follows wherever you host the
bundle — but that origin must be registered in the Keycloak client (§5).

---

## 4. Configuration reference

All settings are environment variables with the prefix `AIRA_`. They are also read from a `.env`
file in the **process working directory** (see `aira_common.config.BaseAiraSettings`), and from
Vault above both when Vault is configured. The complete list, with what breaks without each one,
is [`CONFIGURATION.md`](CONFIGURATION.md); this section holds the ones an operator meets first.

### Common to both services

| Variable                                                       | Default                                                    | Meaning                                                                                                                                                    |
| -------------------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_ENVIRONMENT`                                             | `local`                                                    | `local` \| `staging` \| `production`. `local` — or `AIRA_DEMO_MODE` — is also what makes the gateway serve its API documentation (`/docs`, `/redoc`, `/openapi.json`) and the whole `/readyz` body to anyone; nowhere else. Anything but `local` turns on both planes' start-up checks (§6) and Management's HSTS header — sent only on a request that reached it over HTTPS, directly or through a proxy reporting `X-Forwarded-Proto: https` (believed with `AIRA_TRUSTED_PROXY_HOPS` ≥ 1). The console's nginx terminates no TLS and reports its own scheme, so set HSTS on the TLS terminator in front of it. |
| `AIRA_LOG_LEVEL`                                               | `INFO`                                                     | `DEBUG`/`INFO`/`WARNING`/`ERROR`                                                                                                                           |
| `AIRA_LOG_JSON`                                                | `true`                                                     | JSON lines (`true`) or human-readable console output                                                                                                       |
| `AIRA_DEMO_MODE`                                               | `false`                                                    | Gateway: seeds the published demo API key, registers the mock upstream, serves `/docs`, `/redoc`, `/openapi.json` and the whole `/readyz` body to anyone, and waives six start-up checks — the published Postgres password, a missing audience, `PLAINTEXT` Kafka, and plain-HTTP issuer, JWKS and Vault addresses — never authentication or the use-case requirement. Management: lets `seed_demo` run, and waives none of its checks. **Never in production.** |
| `AIRA_CURRENCY`                                                | `EUR`                                                      | Currency all model prices and cost budgets are expressed in. One per installation; no conversion happens, so it must match the prices you enter. |
| `AIRA_OTEL_ENABLED`                                            | `false`                                                    | Export OTLP                                                                                                                                                |
| `AIRA_OTEL_ENDPOINT`                                           | `http://localhost:4318`                                    | OTLP/HTTP endpoint                                                                                                                                         |
| `AIRA_OTEL_SAMPLE_RATIO`                                       | `1.0`                                                      | Share of requests sampled, by trace id; a caller's trace flag decides nothing. Below 1 also thins the delivery channel a SIEM reads                       |
| `AIRA_POSTGRES_HOST` / `_PORT` / `_DB` / `_USER` / `_PASSWORD` | `localhost` / `5432` / per service / `aira` / `aira-local` | Database connection                                                                                                                                        |
| `AIRA_KAFKA_BOOTSTRAP_SERVERS`                                 | `localhost:29092`                                          | Comma-separated broker list; the security settings are in §3.3                                                                                             |
| `AIRA_OIDC_ISSUER`                                             | `''`                                                       | OIDC issuer URL                                                                                                                                            |
| `AIRA_OIDC_AUDIENCE`                                           | `''`                                                       | Expected `aud`. **Empty disables audience verification**, and is refused outside `local` (§3.2).                                                           |
| `AIRA_OIDC_JWKS_URI`                                           | `''`                                                       | Override; derived from the issuer when empty                                                                                                               |
| `AIRA_ROLE_GROUPS`                                             | `''`                                                       | Which Keycloak group confers each built-in role, `role=/path;…`. Empty grants nobody a role; outside `local` Management refuses a mapping with no `global-admin` group |
| `AIRA_TRUSTED_PROXY_HOPS`                                      | `1`                                                        | How many proxies append to `X-Forwarded-For` in front of the service (§3.7)                                                                                |
| `AIRA_TEST_DATABASE`                                           | `false`                                                    | In-memory SQLite. Test harness only; both planes refuse it outside `local`.                                                                                |

### Gateway only

| Variable                             | Default                             | Meaning                                                                                                                                                                                                                                                                                                                     |
| ------------------------------------ | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AIRA_OIDC_ENABLED`                  | `false`                             | Accept OIDC bearer tokens. **Required for every console view that reads the gateway** (§1). Without it only API keys are accepted.                                                                                                                                                                                          |
| `AIRA_AUTH_REQUIRED`                 | `true`                              | `false` opens all API routes with a synthetic `demo` principal. Refused at start-up anywhere but `local` — `AIRA_DEMO_MODE` does not waive it.                                                                                                                                                                             |
| `AIRA_REQUIRE_USE_CASE`              | `true`                              | Reject an authenticated model call that names no use case — a bound key's included; `false` refuses to start outside `local`                                                                                                                                                                                                                    |
| `AIRA_STORE_PAYLOADS`                | `true`                              | Persist request/response bodies in `request_logs`. **Kill switch**: `false` here means no use case can store them, whatever its own setting says.                                                                                                                                                                 |
| `AIRA_DEFAULT_RETENTION_DAYS`        | `7`                                 | Payload retention for requests that carry **no** use case. Use-case traffic follows the period set on its use case.                                                                                                                                                                                               |
| `AIRA_LOG_RETENTION_DAYS`            | `0`                                 | Delete whole `request_logs` rows older than this. `0` keeps them forever — the cost reporting reads them, so opt in deliberately.                                                                                                                                                                                           |
| `AIRA_ENFORCE_BUDGETS`               | `true`                              | Reject over-budget requests with 429                                                                                                                                                                                                                                                                                        |
| `AIRA_ENFORCE_PRICED_MODELS`         | `true`                              | Refuse a model with no price wherever a cost limit binds the request; a chain skips it for a priced candidate (§3.5). Does nothing while `AIRA_ENFORCE_BUDGETS` is off.                                                                                                                                       |
| `AIRA_REDIS_URL`                     | `redis://localhost:6379/0`          | Shared counter store for rate-limit buckets and budget reservations. **Set this whenever more than one gateway process runs**: without it each process keeps its own counters, so N processes allow N × the configured rate and a budget can be overshot. Empty disables it — see the degradation table below. With Redis Sentinel set `AIRA_REDIS_SENTINELS` and `AIRA_REDIS_SENTINEL_SERVICE` instead, which replace it ([CONFIGURATION.md](CONFIGURATION.md)). |
| `AIRA_ENFORCE_RATE_LIMITS`           | `true`                              | Enforce the per-use-case/per-member request rate. A use case with no configured limit stays unlimited either way.                                                                                                                                                                                                           |
| `AIRA_BUDGET_ESTIMATE_OUTPUT_TOKENS` | `1024`                              | Tokens assumed for a request whose output nothing bounds — no cap from the caller, and neither a default nor a maximum in the catalogue — used to reserve budget before the real usage is known. A model with a declared maximum reserves that instead. Corrected the moment the response arrives.                                  |
| `AIRA_LOG_QUEUE_SIZE`                | `512`                               | Request-log rows buffered off the request path. A full queue writes inline rather than dropping. **`0` writes every row synchronously** — choose it if a request must be durably logged before its response goes out.                                                                                                       |
| `AIRA_TRUST_FORWARDED_FOR`           | `false`                             | Honour `X-Forwarded-For` for the recorded source IP, read `AIRA_TRUSTED_PROXY_HOPS` entries from the right (§3.7)                                                                                                                                                                                                          |
| `AIRA_MAX_REQUEST_BYTES`             | `8388608` (8 MiB)                   | Hard ceiling on a request body; larger bodies get 413 before being buffered                                                                                                                                                                                                                                                 |
| `AIRA_GOOGLE_API_KEY`                | `''`                                | Registers Google AI Studio when set — and stops the gateway at start-up unless `AIRA_ALLOWED_REGIONS` names `global`                                                                                                                                                                                                       |
| `AIRA_GEMINI_MODELS`                 | empty                               | Models offered by name; empty, the catalog's discovery asks the key, and a model catalogued under `generative-language` is served all the same                                                                                                                                                                              |
| `AIRA_GEMINI_BASE_URL`               | Google's v1beta endpoint            | Upstream base URL                                                                                                                                                                                                                                                                                                           |

#### The SPA's reverse proxy must re-resolve its upstreams

The container image serves the SPA through nginx and proxies `/api` to management and `/gw` to the
gateway, so the browser keeps calling one origin and the bearer token never crosses to a third.

nginx resolves a hostname written literally in `proxy_pass` **once**, when the configuration
loads, and holds that address for the life of the process. In an orchestrator every redeploy
hands a container a new address, so a literal upstream leaves the proxy talking to an address
nobody is listening on — the SPA reports the backend as unreachable while both services are
perfectly healthy, and only restarting nginx fixes it.

The shipped config therefore passes each upstream through a variable and configures a `resolver`,
which defers resolution to request time. If you replace this config, keep that shape.
`AIRA_DNS_RESOLVER` defaults to Docker's embedded DNS (`127.0.0.11`); set it to your platform's
resolver elsewhere (in Kubernetes, `kube-dns.kube-system.svc.cluster.local`).

#### What happens when Redis is unavailable

Deliberate, not accidental — a cache outage must not become a product outage:

|                    | Behaviour                                          | Consequence for you                                                                                                                                            |
| ------------------ | -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Rate limiting      | falls back to a **per-instance** bucket            | Still bounded, but N instances allow N × the limit until Redis returns. Not fail-open: Redis being down is exactly when a runaway caller does the most damage. |
| Budget enforcement | falls back to the **Postgres** read-then-book path | Still enforced, but concurrent requests stop seeing each other's reservations, so a limit can be overshot by requests in flight.                               |

Both log a warning, and `/readyz` reports `degraded: true`. Postgres remains the system of record
for budget usage in every case, so a Redis restart costs the in-flight reservations and never the
period's accounting.

**Production Redis**: the reference stack runs it without authentication or TLS, which is fine on
a Compose network and not fine anywhere else — and nothing in AIRA refuses it. Use
`rediss://user:password@host:6380/0` and give it its own instance or database — the keys are
namespaced (`rl:*`, `budget:*`) but not isolated. With `AIRA_REDIS_URL` the credentials go **in the
URL**; `AIRA_REDIS_USERNAME` and `AIRA_REDIS_PASSWORD` are read only with Sentinel. A URL that
carries a password is a secret: keep the whole URL in Vault.

#### Response schemas and embedding batches

| Variable                              | Default   | Meaning                                                                |
| ------------------------------------- | --------- | ---------------------------------------------------------------------- |
| `AIRA_MAX_RESPONSE_SCHEMA_BYTES`      | `32768`   | Largest `responseSchema` accepted, measured on the submitted document. |
| `AIRA_MAX_RESPONSE_SCHEMA_DEPTH`      | `8`       | Deepest nesting accepted.                                              |
| `AIRA_MAX_RESPONSE_SCHEMA_PROPERTIES` | `256`     | Total properties, counted across the whole tree.                       |
| `AIRA_MAX_EMBEDDING_BATCH`            | `256`     | Most texts in one embedding call.                                      |
| `AIRA_MAX_EMBEDDING_CHARS`            | `1000000` | Most characters across a batch.                                        |

The schema bounds are the gateway's **entire** exposure to a caller-supplied schema: it is
forwarded to the provider and never executed here, so there is no expression evaluation to attack
and the defaults only need to be generous enough for real schemas.

**The batch bound interacts with your rate limits, and they have to be chosen together.** A batch
of _n_ texts weighs _n_ against a use case's bucket — otherwise a limit of 10 requests per minute
would allow 5 000 texts per minute, intact on paper and gone in practice. So a batch larger than a
bucket's capacity can never be admitted, however long the caller waits: it is refused immediately
with a message naming which of the two said no. If you set `AIRA_MAX_EMBEDDING_BATCH` above your
smallest configured per-minute limit, large batches will fail permanently by design.

### Management only

| Variable                  | Default   | Meaning                                                                         |
| ------------------------- | --------- | ------------------------------------------------------------------------------- |
| `AIRA_SECRET_KEY`         | dev value | Django signing key. **Must be unique and secret** — from Vault.                 |
| `AIRA_DEBUG`              | `true`    | Honoured only in `local`; anywhere else `true` stops Management at start-up     |
| `AIRA_ALLOWED_HOSTS`      | `*`       | Comma-separated hostnames                                                       |
| `AIRA_PRIVACY_CONTROLLER` | `''`      | The controller the privacy notice names; required outside `local`               |
| `AIRA_DIRECTORY_CLIENT_ID` / `_SECRET` / `_URL` | `''` | The read-only directory client (§5.6); without it no role can be bound to a group and nobody who has not signed in can be granted |

**Outside `AIRA_ENVIRONMENT=local` Management refuses to start** while `AIRA_SECRET_KEY` is the
development default, `AIRA_ALLOWED_HOSTS` contains `*`, `AIRA_DEBUG` is on, no issuer or no audience
is set, `AIRA_ROLE_GROUPS` names no `global-admin` group, `AIRA_PRIVACY_CONTROLLER` is empty, Kafka is
`PLAINTEXT`, the issuer, JWKS or Vault address is plain HTTP to anything but loopback, or
`AIRA_TEST_DATABASE` is on. Unlike the gateway it has no demo waiver. That is deliberate: a silent
start with a well-known signing key is worse than a failed one. The full list for both planes is
[`CONFIGURATION.md` §8](CONFIGURATION.md).

### Minimal production environment

Both planes start on these, and refuse on anything less: each was checked against the gateway's
application factory and Management's settings module, with nothing else in the environment. The
credentials are not written here — they come from Vault, at the path the `VAULT_*` lines name.

Gateway:

```bash
AIRA_ENVIRONMENT=production
AIRA_POSTGRES_HOST=db.internal
AIRA_POSTGRES_DB=aira_gateway
AIRA_POSTGRES_USER=aira
AIRA_KAFKA_BOOTSTRAP_SERVERS=kafka-1.internal:9093,kafka-2.internal:9093
AIRA_KAFKA_SECURITY_PROTOCOL=SASL_SSL
AIRA_KAFKA_SASL_MECHANISM=SCRAM-SHA-512
AIRA_KAFKA_SASL_USERNAME=aira-gateway
AIRA_OIDC_ENABLED=true
AIRA_OIDC_ISSUER=https://sso.example.com/realms/aira
AIRA_OIDC_AUDIENCE=aira-gateway
AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung
AIRA_VERTEX_PROJECT=my-gcp-project
AIRA_VERTEX_MODELS=europe-west1/google/gemini-2.5-flash
AIRA_TRUST_FORWARDED_FOR=true      # behind proxies you run …
AIRA_TRUSTED_PROXY_HOPS=1          # … this many of which append to X-Forwarded-For
AIRA_OTEL_ENABLED=true
AIRA_OTEL_ENDPOINT=http://otel-collector:4318
VAULT_ADDR=https://vault.internal:8200
VAULT_ROLE_ID=<the AppRole's role id>
VAULT_SECRET_ID_FILE=/run/secrets/vault-secret-id
# in Vault: AIRA_POSTGRES_PASSWORD, AIRA_KAFKA_SASL_PASSWORD, AIRA_VERTEX_API_KEY
#           (or AIRA_VERTEX_CREDENTIALS), and AIRA_REDIS_URL, since it carries a password —
#           on a path Management reads too, the two passwords as AIRA_GATEWAY_POSTGRES_PASSWORD
#           and AIRA_GATEWAY_KAFKA_SASL_PASSWORD, and the user names as AIRA_GATEWAY_*
```

Management (same database server, different database):

```bash
AIRA_ENVIRONMENT=production
AIRA_DEBUG=false
AIRA_ALLOWED_HOSTS=aira.example.com
AIRA_POSTGRES_HOST=db.internal
AIRA_POSTGRES_DB=aira_mgmt
AIRA_POSTGRES_USER=aira
AIRA_KAFKA_BOOTSTRAP_SERVERS=kafka-1.internal:9093,kafka-2.internal:9093
AIRA_KAFKA_SECURITY_PROTOCOL=SASL_SSL
AIRA_KAFKA_SASL_MECHANISM=SCRAM-SHA-512
AIRA_KAFKA_SASL_USERNAME=aira-management
AIRA_OIDC_ISSUER=https://sso.example.com/realms/aira
AIRA_OIDC_AUDIENCE=aira-gateway
AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung
AIRA_PRIVACY_CONTROLLER="Example GmbH, Musterstraße 1, 12345 Musterstadt, datenschutz@example.com"
AIRA_DIRECTORY_CLIENT_ID=aira-directory
AIRA_OTEL_ENABLED=true
AIRA_OTEL_ENDPOINT=http://otel-collector:4318
VAULT_ADDR=https://vault.internal:8200
VAULT_ROLE_ID=<the AppRole's role id>
VAULT_SECRET_ID_FILE=/run/secrets/vault-secret-id
# in Vault: AIRA_SECRET_KEY, AIRA_POSTGRES_PASSWORD, AIRA_KAFKA_SASL_PASSWORD,
#           AIRA_DIRECTORY_CLIENT_SECRET — on a path the gateway reads too, the two passwords
#           as AIRA_MANAGEMENT_POSTGRES_PASSWORD and AIRA_MANAGEMENT_KAFKA_SASL_PASSWORD
```

The audience is the same on both: the console's token goes to both planes. The consumer and the
relay read the same variables as their service (gateway and management respectively) — give them
the identical environment.

---

### 4a. Vertex AI in the EU

The Generative Language adapter (`AIRA_GOOGLE_API_KEY`) is the **laptop** path: one key, no GCP
project, a global endpoint. It cannot make a residency statement and is not a production candidate
where one is required.

```bash
AIRA_VERTEX_PROJECT=my-gcp-project
AIRA_VERTEX_CREDENTIALS='{"client_email":"…","private_key":"…"}'   # the service account's JSON — from Vault
# AIRA_VERTEX_API_KEY=…                                            # or an API key; the service account wins if both
AIRA_ALLOWED_REGIONS=eu,europe-west1               # empty → the EU regions of every cloud
AIRA_VERTEX_MODELS=eu/google/gemini-2.5-pro,eu/anthropic/claude-sonnet-4-5@20250929
```

Each model is `region/publisher/model` — the three things the URL and the dialect choice need.

**Five things fail at startup rather than at dispatch**, on purpose. A gateway that starts and then
fails every request looks like an upstream outage; one that starts and quietly serves a non-EU
region is worse than one that will not start at all.

| Refused at boot                                       | Because                                                                       |
| ----------------------------------------------------- | ----------------------------------------------------------------------------- |
| A model in a region outside `AIRA_ALLOWED_REGIONS`    | residency is a configuration claim, and this is what makes it hold            |
| Credentials that are not a usable service-account key | a credential problem must not present as an upstream problem                  |
| A model spec that is not `region/publisher/model`     | a typo here would otherwise become a 404 per request                          |
| A publisher no Vertex adapter here speaks             | the request would be sent in a dialect the model does not understand          |
| The same model name on two adapters                   | otherwise the region and credential that served a request are a silent choice |

**`AIRA_ALLOWED_REGIONS` is not Vertex's list.** It is the deployment's residency policy and every
transport is measured against it — Google's `europe-west1` and Azure's `westeurope` sit in the same
setting, because "which regions may we use" is one question with a vendor-specific vocabulary. A
per-cloud list would mean a per-cloud audit, and the one added last is the one nobody remembers to
check. Left empty it means the EU regions of every supported cloud, never "no constraint".
**`AIRA_ALLOWED_MODEL_REGIONS`** permits a region for named models alone (`region=model,model;…`)
— for a model served only where the list does not reach, such as the open models that answer only
at `global` — without permitting that region for every model.

**Provenance.** Every request records `provider`, `publisher` and `region`; reporting can break
down by them. That is what turns "we are in the EU" from an assertion into something an auditor can
check per request.

**The service-account key is the most valuable secret here.** It is never logged, never in a span
and never in an error message. Keep it in Vault: an environment variable appears in
`docker inspect`, in process listings and in orchestrator manifests.

## 5. Preparing Keycloak

The reference realm is `deploy/compose/keycloak/realms/aira-realm.json`. Reproduce these six things
in your own realm.

### 5.1 A public client for the SPA

| Setting              | Value                                                    | Why                                                                                  |
| -------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Client ID            | `aira-gateway`, or your own — then set `AIRA_OIDC_CLIENT_ID` on the console container (§3.8) |                                                  |
| Access type          | **public**                                               | it is a browser app                                                                  |
| Standard flow        | **on**                                                   | authorization code                                                                   |
| Direct access grants | **off**                                                  | the password grant is a credential-phishing surface on a public client               |
| PKCE method          | **S256**                                                 | without it, an intercepted code can be redeemed                                      |
| Valid redirect URIs  | your SPA origin, e.g. `https://aira.example.com/*`       | **never `*`** — a wildcard lets an attacker redirect the code to a site they control |
| Web origins          | your SPA origin                                          |                                                                                      |

### 5.2 The three built-in roles, each conferred by a group

`global-admin`, `it-security`, `it-steuerung`. There is no use-case role: administering or
belonging to a use case is a grant on that use case.

**A role is held through a Keycloak group**, and `AIRA_ROLE_GROUPS` says which group confers which
built-in role:

```
AIRA_ROLE_GROUPS=global-admin=/aira/global-admins;it-security=/aira/it-security;it-steuerung=/aira/it-steuerung
```

The paths are yours, the match is exact, and a name that is not one of the three is refused at
start-up. A role an installation defines is bound to its group in the console instead, and reaches
the gateway over `aira.roles`. **A Keycloak realm role grants nothing** — neither plane reads
`realm_access.roles`. That is the guarantee: group membership is the only way to hold a role, and
a realm role assigned to a person directly is not a second one.

Keycloak is the **source of truth**: on every authenticated request the roles the token's
**groups** confer are synced onto the user's Django groups, and a role that exists only in Django
is removed at the next login.

### 5.3 A groups mapper

The gateway derives use-case membership and roles from group paths, so the token must carry them:

| Mapper setting      | Value            |
| ------------------- | ---------------- |
| Type                | Group Membership |
| Claim name          | `groups`         |
| Full group path     | **on**           |
| Add to access token | **on**           |

### 5.4 An audience mapper

Keycloak puts no audience a client of yours can name into a public client's token by itself. Add an
**Audience** mapper to the console's client: *Included Client Audience* the value of
`AIRA_OIDC_AUDIENCE` (`aira-gateway` in the reference realm), *Add to access token* on. Without it
both planes refuse every token outside `local` (§3.2), and the console says so after a few
attempts (§0 Step 2). A machine client of your own that calls the gateway with its own token needs
the same two mappers.

### 5.5 Groups per use case

**Three routes reach a use case**:

1. the group `/use-cases/<slug>`, read from the token alone — for a use case Management has created
   and whose configuration has reached the gateway; any other slug is refused `403` *"not known to
   this gateway"*;
2. a grant on **any group path your realm already uses**, given in the console;
3. a grant naming **one person**, where no group fits.

Routes 2 and 3 are replicated to the gateway over Kafka and read on every request, so a member
added in the console can call the gateway for that use case without anybody creating a group named
after it. A group grant is checked for its shape, not against the realm, so a grant may name a
group that does not exist yet; one picked from the console's directory search cannot be mistyped.

### 5.6 A read-only directory client

A **confidential** client with a service account holding `view-users` and `query-groups` on the
realm, and nothing else (`aira-directory` in the reference realm), given to Management as
`AIRA_DIRECTORY_CLIENT_ID`, `AIRA_DIRECTORY_CLIENT_SECRET` (from Vault) and, when Management reaches
Keycloak at another address than the issuer's, `AIRA_DIRECTORY_URL`. Management uses it to search
people and groups when access is granted, to invite somebody who has not signed in yet, and to
check that a group exists before a role is bound to it — which it refuses while the directory
cannot be asked.

---

## 6. Production checklist

Security:

- [ ] `AIRA_ENVIRONMENT` set to something other than `local` on every real deployment. Both
      services then **refuse to start** with a development default in place, naming every reason
      at once. The gateway: authentication off, the use-case requirement off, the published Postgres
      password, OIDC with no issuer or no audience, `PLAINTEXT` Kafka, a plain-HTTP issuer, JWKS or
      Vault address, the test database. Management: a development `SECRET_KEY`, `DEBUG`,
      `ALLOWED_HOSTS=*`, no issuer, no audience, no `global-admin` group, no privacy controller,
      `PLAINTEXT` Kafka, a plain-HTTP issuer, JWKS or Vault address, the test database. Management
      does not check the Postgres password
- [ ] `AIRA_DEMO_MODE` off — on the gateway it waives six of those checks (§4) — and `seed_demo`
      never run: it creates the demo accounts and deterministic API keys derived from a salt
      published in this repository
- [ ] `AIRA_OIDC_AUDIENCE` set on **both** services, and the audience mapper on every client
      that reaches AIRA (§5.4)
- [ ] Keycloak client: no wildcard redirect URIs, direct access grants off, PKCE S256
- [ ] `AIRA_TRUST_FORWARDED_FOR` and `AIRA_TRUSTED_PROXY_HOPS` match reality — on only behind
      proxies you run, the hop count equal to the proxies that append (§3.7)
- [ ] TLS terminated in front of both services; the SPA served over HTTPS (the client refuses the
      code flow against a remote issuer over plain HTTP)
- [ ] Database credentials, `AIRA_SECRET_KEY`, the Kafka SASL password, the directory secret and
      the provider keys in Vault, not baked into images, `.env` files or commits — and
      `/readyz` (asked as an operator) reporting `"source": "vault"`

Operations:

- [ ] Both migration commands run on every deploy, before the new code starts
- [ ] All nine Kafka topics exist and are **compacted** (§3.3) — `make kafka-topics` creates them
      only in this repository's own broker
- [ ] The consumer runs as a long-lived process and is restarted on failure
- [ ] **The retention pruner is scheduled.** `python -m aira_gateway.retention` does one pass and
      exits. If nothing runs it, **nothing is ever deleted** and the period configured per use
      case is a promise nobody keeps. The reference stack runs it hourly in a loop; outside
      Compose use cron, a systemd timer or a CronJob.
- [ ] **The relay is scheduled.** `manage.py relay` publishes pending rows once and exits — a cron
      job, systemd timer or Kubernetes CronJob every minute is the intended shape; the reference
      stack loops every 10 seconds. Without it, nothing a user configures in the UI ever reaches
      the gateway. One run publishes at most 500 events, oldest first, and says how many are still
      pending. Runs never overlap: each takes a PostgreSQL advisory lock and leaves at once when
      another holds it — so its database connection must be a session, not a transaction-pooled
      one — and a CronJob may say so too (`concurrencyPolicy: Forbid`). A send that fails stops the
      run there and fails it, naming the event and its topic: that event and every later one stay
      pending, so one missing topic holds back every change (§3.3). An event the broker will never
      take (too large) is **parked** with its reason, said on every run, and never sent. Delivered
      and parked rows are deleted after 30 days.
- [ ] Prices on file for every model in use — check the unpriced warning on **Models & prices**.
      Under a cost budget an unpriced model is refused (§3.5); elsewhere its traffic is excluded
      from every spend figure
- [ ] `AIRA_STORE_PAYLOADS` reviewed against your data-protection rules — with it on, full prompts
      and responses are written to `request_logs`. They are deleted after each use case's
      retention period (default **7 days**), and **credential-shaped strings are masked before
      storage**. That redaction is deliberately narrow: it removes API keys, bearer tokens, JWTs
      and private keys, and leaves names, customer numbers and prose alone, because those are
      what the payload is stored for. A use case can replace personal data before the prompt
      leaves with a `pii_filter` step — the rewritten request is then what is stored. For data
      that must not be persisted at all, the control is the per-use-case storage switch below
- [ ] `AIRA_DRAIN_LEFT_STREAMS_SECONDS` reviewed: a stream whose caller left is read on — 600 s at
      most — so the vendor's own figure is booked; the answer is then billed whole, and the stream
      holds its upstream connection until the vendor finishes. `0` closes it with the caller
- [ ] `AIRA_COUNT_CANCELLED_STREAMS` reviewed: on by default, a stream the caller left before the
      vendor reported its usage is counted at the vendor's own free endpoint — the request and the
      part of the answer received are sent to it a second time (Vertex Gemini).
      Off, nothing is sent again and such a stream's usage stays unknown
- [ ] `AIRA_REDACT_PATTERNS` set if your organisation has its own token or identifier format —
      added to the built-ins, never replacing them
- [ ] Retention periods reviewed with whoever is accountable for each use case, and payload
      storage switched **off** for any use case whose data must not be persisted at all
- [ ] `/healthz` (liveness) and `/readyz` (readiness) wired into your orchestrator on both services.
      **Postgres alone decides readiness** (`200` ready, `503` not ready). Kafka is reported and
      never makes an instance not ready: while no broker answers, `degraded` is `true` and the
      services keep serving on what they hold — changes wait in the outbox. Redis, a fallback in
      use or an unreachable or stale upstream probe set `degraded` too. Alert on `degraded`; take an
      instance out of rotation only on `not_ready`. `/readyz` is unauthenticated by design — a probe
      carries no credential — and outside `local` and demo mode the gateway answers probes with the
      **verdict and `degraded` only**; the body naming the upstreams, the fallbacks in force and the
      Vault address, path and key names needs an operator's credential (§0 Step 6). No check names a
      host: a cluster is reported as reachable, unreachable or *"1 of 2 reachable"*. Management's
      `/readyz` returns its checks to anyone
- [ ] `AIRA_API_KEY_DEFAULT_DAYS` (30) and `AIRA_API_KEY_MAX_DAYS` (180) reviewed against your
      rotation policy. Every key issued carries a date; neither plane can mint an unbounded one
- [ ] **A key that carries no end date still works.** Neither plane mints one any more, but a key
      stored before every key had an end date keeps working: expiring it would be an outage chosen
      on your behalf, and a silent one — nothing tells an integration why it stopped. The console
      marks such keys **"no end date"** in the API-keys table; re-issue them on a schedule you pick
- [ ] A backup of both databases and of the topics, taken together (§6b)

---

## 6a. Upgrading

Migrations run **before** the new code starts: `gateway-migrate` and `management-migrate` in
Compose, or the two commands of §3.1 as a pre-deploy job. Nothing else creates tables — the gateway
calls `create_all` only on the in-memory SQLite of the test suites, and the consumer not at all.

**Deploy every component that opens a database**, not only the one whose feature changed: the
gateway, its consumer and the retention pruner all read the gateway's schema, and a component left
on the previous image reads the new schema with the old code. When a release contains a migration
that renames or drops, check that it held (`\dt`) before assuming it did.

**Two versions serving at once** — the state a rolling update passes through — need one rule of
every migration: a column is dropped one release after the code stopped reading it, and a rename
is two releases. It is stated, not enforced.

A new topic in a release is created before the release's code runs (§3.3); the topic step is
idempotent, and it creates every topic that release's code uses.

**A stopping gateway waits five seconds for its requests**, then cuts short the streams it is still
reading for callers who left, books what they used and writes their rows, and closes. The image
starts uvicorn with `--timeout-graceful-shutdown 5`, inside Docker's default of ten seconds before
it kills the process; an orchestrator with a shorter grace period than that loses the rows of the
requests still running when it kills.

---

## 6b. Backups and restore

What an installation keeps, and where:

| Where | Holds | Back it up |
| --- | --- | --- |
| PostgreSQL `aira_gateway` | **the audit trail** — a row for every request past authentication and attribution, served or refused, with its stored prompt and answer — budget usage, findings, suspensions, the record of who read stored content, retention runs, break-glass keys; and the read-model of Management's configuration | **yes**: everything but the read-model exists nowhere else |
| PostgreSQL `aira_mgmt` | **the configuration** — use cases, members and group grants, API keys (as hashes), budgets, rate limits, pipelines, anomaly rules, the model catalogue and its prices, roles and their change log — the outbox, the console's accounts, privacy acknowledgements, the question catalogue and its results | **yes**: it is the source every configuration event comes from |
| the Kafka topics | the current configuration, compacted, which a gateway's read-model is built from; and the consumer group's committed offsets | **yes** — see below |
| Redis | rate-limit buckets and budget reservations in flight | no: deliberately not persisted (`--save "" --appendonly no` in the bundled stack). Budgets are booked in Postgres; buckets refill |
| Vault | every credential | with your Vault. The bundled `-dev` Vault keeps nothing across a restart; `vault-init` refills it from `deploy/compose/.env` on every start when `VAULT_ADDR` is set |

In the bundled stack the three databases — the two above and Keycloak's `keycloak` — live in one
volume, `aira_pgdata`, and the topics in `aira_kafka-data` (prefixed with `AIRA_STACK`, `aira` by
default). **`make destroy` deletes both**, and with them the audit trail, every configuration and
the development realm; `make down` keeps them.

**Nothing re-sends the configuration.** The relay publishes only outbox rows that are still
pending, a delivered row is deleted 30 days after delivery, and no command republishes Management's
state to Kafka. So the configuration lives in three places that are not rebuilt from each other —
`aira_mgmt`, the topics and the gateway's read-model — and a restore must bring them back together.
The safe backup is one taken of all three at the same moment, with the applications stopped.

What restoring one of them alone implies:

- **`aira_mgmt` alone, to an earlier point.** The console shows the configuration of the backup;
  the topics and the gateway still hold everything changed since. An API key issued after the
  backup keeps authenticating at the gateway and appears nowhere in the console, so it cannot be
  revoked there — only on the gateway's command line (§6c). Outbox rows that were still pending at
  the backup are sent again, and the consumer applies them over whatever newer state it holds.
- **`aira_gateway` alone, to an earlier point.** The audit trail and usage since the backup are
  gone, and so is every break-glass key minted since. The read-model is as old as the backup, and
  **the consumer does not catch up by itself**: it resumes from the offsets its group committed in
  Kafka, which are already past everything published since. Rebuilding the read-model means
  replaying the topics from their start — the group's offsets reset to the earliest, with the
  consumer stopped (Kafka's own consumer-group tool does it). Nothing in this repository does that
  step, and no restore has been rehearsed here.
- **The topics alone, or lost.** A running gateway keeps its read-model, and the next change to
  each object travels as usual. A gateway whose database is new or restored has nothing complete
  to replay until every object has been changed again.

---

## 6c. When the control plane is down: the break-glass key

An operator with access to the gateway's database mints an API key on the gateway's own command
line — no Management, no Keycloak. Run it where the gateway's environment is, for
instance in its container:

```bash
docker exec aira-gateway python -m aira_gateway.cli api-key create --subject ops-oncall --label "incident" --days 1
docker exec aira-gateway python -m aira_gateway.cli api-key revoke --prefix <prefix>
```

`create` prints the key once, with its prefix and end date. The key is **unbound**: it may name any
use case the gateway knows without being a member of it, and it is the one credential exempt from
naming a use case at all — a request that names none books against the installation's own budget. It is also an operator's
credential for `/readyz` (§6). Its lifetime is `--days`, by default `AIRA_API_KEY_DEFAULT_DAYS` and
at most `AIRA_API_KEY_MAX_DAYS`; there is no key without an end date. Every request it makes is
recorded under its prefix and subject like any other. Management never hears of it, so it appears
in no console list: `revoke` on the same command line is how it ends early.

---

## 7. Known gaps

Stated plainly, because a deployment guide that hides them wastes your time:

| Gap                                                            | Consequence                                                                                                   | Status                                                                                                                                                                                                          |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **No Kubernetes/Helm**                                         | Compose only; no manifests or charts                                                                          | Planned (see `docs/ROADMAP.md`)                                                                                                                                                                                 |
| **Several gateway instances**                                  | One gateway container per Compose stack; Management stays a single instance by decision                       | Open: `container_name` blocks `--scale`, and the two-version schema rule (§6a) is not enforced                                                                                                                 |
| ~~**Images are not published**~~                               | —                                                                                                             | **Closed** (§1b): pushing an annotated tag publishes `aira-gateway`, `aira-management` and `aira-frontend` to GHCR at `vX.Y.Z` (plus `sha-<commit>` and `latest`), behind every CI gate, with the tag's message as the release notes. The version lives in the Git tags only — `make next-version`. **Running them is closed too**: `docker-compose.release.yml` pulls them (`RELEASE=vX.Y.Z` on every target that starts the applications in containers) and `docker-compose.external.yml` runs them against infrastructure you provide |
| ~~**Vault is not used by any code**~~                          | —                                                                                                             | **Closed**: `aira_common.secrets` reads AppRole + KV-v2 and ranks Vault above the environment for both planes                                                                                                   |
| **Schema Registry is not used**                                | Events are plain JSON with an `event_type` header; no version field, no registered schema                     | Runs in the bundled stack, unused                                                                                                                                                                               |
| ~~**SPA configuration is build-time**~~                        | —                                                                                                             | **Closed**: the console image writes `runtime-config.js` at every start from `AIRA_OIDC_ISSUER` and `AIRA_OIDC_CLIENT_ID`, and derives its content policy from the issuer — no rebuild (§3.8)                  |
| ~~**Kafka has no auth/TLS settings**~~                         | —                                                                                                             | **Closed**: `aira_common.kafka.KafkaSecurity` takes protocol, SASL mechanism and credentials and a CA file (`AIRA_KAFKA_SECURITY_PROTOCOL` and friends), and both planes refuse to start on PLAINTEXT outside `local` |
| **The relay is not a daemon**                                  | Must be scheduled externally, or configuration never propagates                                               | By design (transactional outbox), but unscheduled by default                                                                                                                                                    |
| **No command re-sends the configuration**                      | A restore that brings back one database without the others leaves the planes disagreeing (§6b)                | Open                                                                                                                                                                                                            |
| ~~**Membership is split** between Management and Keycloak groups~~ | —                                                                                                         | **Closed**: a grant on any group path, or on one person, is replicated to the gateway over Kafka and read on every request — no group named after the use case is needed (§5.5)                                  |
| **PII is not masked in stored payloads**                       | A prompt is kept as the caller wrote it, business content included, until retention removes it               | **Credentials are** masked (`build_redactor`); storage-time PII masking is **declined** rather than pending — the sensitive content and the useful content are the same content, so reads are gated by role and recorded instead. A use case may replace personal data before dispatch with a `pii_filter` step |
