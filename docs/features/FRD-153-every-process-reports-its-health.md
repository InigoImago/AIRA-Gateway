# FRD-153 — Every process reports its own health

> Phase: 7 (deployment) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: an operability ticket — *the workers report their own health* — after installations
> found the three worker containers either permanently `unhealthy` or not monitored at all, and
> built checks of their own in Compose. Related: `FRD-204` (configuration over Kafka, the consumer
> and the relay), `FRD-404` (retention), `FRD-117` (diagnostics), `ADR-0053` (the decision).

## 1. Summary

Both Python images carried one healthcheck, an HTTP request to `/healthz`, and three processes run
from them without a server: the gateway's configuration consumer, the outbox relay and the retention
sweep. The shipped Compose files switched the check off for them, and ran the relay and the sweep in
shell loops whose `|| true` swallowed every failure. A consumer waiting for Kafka or holding an event
the database could not take, a relay failing every run, a sweep failing every hour: each kept its
container "Up", and nothing showed it.

Each worker now writes a **heartbeat** when it makes progress — and only then — carrying the age it
may reach. One command, `python -m aira_common.healthcheck`, reads the heartbeat where there is one
and asks the server's `/healthz` where there is not; both images run it. The relay and the sweep loop
by themselves (`--loop`), so a failed pass is said by the process and is not a heartbeat.

## 2. Goals & Non-Goals

- **Goals**: every long-running container reports what its process is doing, with no health logic
  in Compose; the consumer is unhealthy in exactly the states that cost configuration; a readable
  reason; one command an orchestrator can use for every role; nothing to change for an installation
  beyond the images and the worker commands — no new setting, no infrastructure, no start-up
  refusal.
- **Non-Goals**: metrics of the workers' progress and the age of the oldest pending outbox event in
  Management's readiness (§6) — a state is reported, not a trend; restarting what is unhealthy, which
  is the orchestrator's choice; a Kubernetes chart.

## 3. User Stories

- As an **operator**, I see a container read `unhealthy` when its worker stopped doing its job, and
  `docker inspect` tells me why — *waiting for Kafka*, *an event is being applied or held for
  retry*, *its last pass failed: …*.
- As an **operator** of an integrated deployment, I remove the checks I built in Compose — a file the
  shell loop touched, a look for a broker connection — and the images' own check does better.
- As **whoever writes the Helm chart**, I give every process the same `exec` liveness probe.

## 4. Functional Requirements

- **FR-1 Heartbeat.** `aira_common.heartbeat.Heartbeat(worker, max_age_seconds)` writes
  `{"worker", "written_at", "max_age_seconds"}` to `AIRA_HEARTBEAT_FILE` (default
  `/tmp/aira-heartbeat`), beside the file and renamed over it, at most once a second. A write that
  fails does not stop the worker and is said once (`heartbeat_not_written`). A worker removes the
  file an earlier run of its container left before it starts.
- **FR-2 Reading.** A heartbeat is healthy while `now - written_at <= max_age_seconds`; one dated
  ahead of the clock counts as just written up to its own age, and beyond it is unhealthy. A file
  that can be read and is not a heartbeat — not JSON, no worker, a time or an age that is not a
  finite number, more than 4096 bytes — is unhealthy, never a reason to ask anything else.
- **FR-3 One command.** `python -m aira_common.healthcheck` reads the heartbeat; with none — missing,
  or not readable — it asks `http://127.0.0.1:$AIRA_HEALTH_PORT/healthz` directly, never through a
  proxy the environment names, within four seconds. It prints one line and exits 0 or 1. It imports
  nothing of the services it checks. Both images run it, with a start period of 60 seconds;
  `AIRA_HEALTH_PORT` is `8001` in the gateway's image and `8002` in Management's.
- **FR-4 The consumer** (`consumer.worker.Health`) beats when its broker accepted it, after each event
  applied or skipped, and every 15 seconds while idle — with partitions assigned and its broker
  having answered a fetch for each within 30 seconds. It does not beat while it waits for Kafka,
  while an event is in hand (applied, held for retry, its offset committed), with no partition, or
  with a broker silent past 30 seconds. Maximum age 90 seconds.
- **FR-5 The loops** (`aira_common.periodic.run_every`). A pass runs now and again `--interval`
  seconds after each ends. A pass that finished beats; a pass that raised is logged —
  `worker_pass_failed`, its message for the failures it expects, with the traceback otherwise — and
  the loop goes on. SIGTERM ends the loop between passes: a pass that has begun finishes, a pause does
  not. A maximum age no longer than the pause is refused before the first pass.
- **FR-6 The relay** — `manage.py relay --loop [--interval 10] [--max-age …]`: a run that published,
  found nothing pending, or left the outbox to the run holding it is a heartbeat; one that raised
  `CommandError` or a database error is said by its message. Each run closes its database
  connections after it, as the process per run did. Maximum age five pauses, at least 120 seconds.
  The one-pass form is unchanged.
- **FR-7 The retention sweep** — `python -m aira_gateway.retention --loop [--interval 3600]
  [--max-age …]`: a sweep that finished is a heartbeat; a database's failure is said as
  `SweepFailed` with its `DETAIL` cut (`FRD-406`). Maximum age two pauses and 600 seconds. The
  one-pass form is unchanged.
- **FR-8 The log tells the same story.** `worker_healthy` at the first progress and at the first
  after a silence longer than the age; `worker_unhealthy` once, with the reason, when the worker
  notices its silence has outgrown the age — the consumer at its next look, a loop at the moment it
  does, waking from its pause for it. A pass that hangs is said when it ends.
- **FR-9 Compose.** No long-running service of the shipped files switches its healthcheck off, no
  command carries `|| true`, and a long-running process of an AIRA image other than its server keeps
  a heartbeat or brings a check of its own.

## 5. Design & Architecture

### 5.1 Progress, not life

The consumer's two waits are right — exiting at start makes a restart loop, skipping a held event
loses it — and both leave the process alive. So the heartbeat is tied to what the worker exists to
do, and nothing else writes it. The idle consumer is the one case with no event to show: it beats
only while the broker answers its fetches, which aiokafka stamps per partition on every answer,
events or none (`last_poll_timestamp`). A broker that went away leaves the consumer its assignment,
idle in `getone`; the stamps stop, and so do the beats.

### 5.2 The age is the worker's

The check is one command for every role, so what differs is in the file: the worker says how old
its heartbeat may grow. The figures follow what each tolerates — the relay a few failed runs while a
broker restarts, the sweep one failed hour — and `--max-age` overrides them where a pass is known to
take longer.

### 5.3 The package's root

`python -m` imports a package's `__init__` first, and `aira_common`'s imported the settings,
OpenTelemetry and an HTTP client: 338 ms a check, every fifteen seconds, inside the gateway's one
core. Nothing imported those names from the root, so it imports nothing now; the check loads in
25 ms.

### 5.4 Docker's retries, and an orchestrator that restarts

Docker marks a container `unhealthy` after its own five failed checks, fifteen seconds apart, on top
of the age: a consumer cut off from Kafka reads unhealthy about three minutes after its broker last
answered, and healthy at the first check after its first progress. Compose only reports. A probe
that restarts — Kubernetes liveness, Swarm — must allow the longest pass a worker may need, or a long
sweep is restarted midway and rolled back.

## 6. What is not built

- **Management's readiness and the outbox.** How long the oldest pending event has waited, reported
  by `/readyz` and degrading it above a threshold, would catch a relay that is healthy and cannot
  keep up, and a deployment where nothing runs the relay at all.
- **Metrics** — pending outbox events, the oldest one's age, the consumer's last applied event, the
  sweep's last success — so monitoring can alert on a trend. A metric is a new telemetry item, and
  goes through the privacy register (`FRD-625`).

## 7. API / Interface Contract

No HTTP or event contract changes. New command-line options: `relay --loop/--interval/--max-age`,
`retention --loop/--interval/--max-age`. New environment variables, read by the healthcheck and the
heartbeat and by no settings class: `AIRA_HEARTBEAT_FILE`, `AIRA_HEALTH_PORT`.

## 8. Testing & Acceptance

- **Unit** — `libs/tests/test_heartbeat.py` (written whole, the age, what is not a heartbeat, a write
  that fails, the log), `libs/tests/test_healthcheck.py` (heartbeat or server, the reason, no proxy,
  the command as the image runs it, nothing heavy imported), `libs/tests/test_periodic.py` (a failed
  pass, SIGTERM in a pass and in a pause, the stale moment in a pause),
  `gateway/tests/test_the_consumer_reports_its_health.py` (start, events, idle, and the four states
  that are not progress — an event held for 121 s reads unhealthy past 90),
  `management/backend/tests/test_the_relay_loop.py`, `gateway/tests/test_the_retention_loop.py`,
  `tools/tests/test_every_container_reports_its_health.py` (FR-9 and both images' check). Mutations
  `HB-1`–`HB-23`.
- **Integration** — `tests/integration/test_the_workers_report_their_health.py`, on the stack: every
  long-running container reads `healthy`; Kafka stopped turns the consumer's and, with an event
  pending, the relay's check unhealthy with its reason, and Docker's log keeps it; Kafka back turns
  both healthy; PostgreSQL stopped turns the relay, a sweep looping beside the stack's and the
  consumer holding an event unhealthy, and back healthy after.
- **Browser**: none; the feature has no screen.
- **Acceptance** (live, on a stack of its own built from this tree): every long-running container
  read `healthy` with the shipped Compose files, 2 minutes after `make up-full`. Kafka stopped: the
  consumer went on beating for the 30 s its broker may be silent, then said *its broker has not
  answered a fetch for aira.anomaly-rules partition 0 in 30 s* — aiokafka kept the assignment — and
  Docker marked it and the relay `unhealthy` about three minutes after the stop; Kafka back, both
  beat within 3 s and Docker read `healthy` within 10 s. PostgreSQL stopped: the relay said *its last
  pass failed: failed to resolve host 'postgres'*, a consumer handed an event from the broker *an
  event is being applied or held for retry*; back, the relay beat within 4 s and the event was
  applied. The three tests took 6 minutes. A consumer built with `HB-3`'s defect — idle beats that
  do not ask whether the broker answers — stayed healthy through the same outage, and the Kafka
  test failed on it.
