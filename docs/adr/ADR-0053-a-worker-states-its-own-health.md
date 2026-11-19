# ADR-0053 — A worker states its own health, in a heartbeat it writes on progress

- **Status:** Accepted
- **Date:** 2026-11-19
- **Deciders:** Vadim Scheibe

## Context

Both Python images carried one healthcheck, an HTTP request to the server's `/healthz`. Three
processes run from them without a server — the gateway's configuration consumer, the outbox relay
and the retention sweep — so the check could never pass for them, and the shipped Compose files
switched it off. The relay and the sweep each did one pass and exited, and Compose ran them in shell
loops, `while true; do … || true; sleep …; done`, whose `|| true` swallowed every failure.

So a worker that had stopped doing its job looked like one that was doing it. The consumer waits
for a broker that is away and holds an event the database cannot take, for as long as it takes —
both deliberately, since exiting or skipping lose configuration — and the process stays up while
nothing is applied, a key revocation included. A relay that cannot reach Kafka fails every run while
the outbox grows; a sweep that cannot reach PostgreSQL fails every hour while payloads stay past
their retention. Installations built checks of their own in Compose: a file the shell loop touched
after a run that succeeded, and a look in `/proc/net/tcp` for a connection to a Kafka port — which a
consumer stuck retrying an event keeps.

## Options considered

- **Health logic in Compose, per service** — what installations did. Every installation, and later
  a chart, rebuilds it; the consumer's version cannot see what it is doing, only that it is
  connected; and a check outside the process can only see what the process leaves behind.
- **An HTTP endpoint in every worker.** A server in each worker so the existing check passes: a
  port, a thread or a task, and a second thing to keep alive beside the work. What it answers would
  still have to come from the worker's own idea of its progress, which is the actual design
  question, and liveness of the server proves nothing about the loop beside it.
- **Restart on failure.** Let the relay and the sweep exit non-zero and let the orchestrator restart
  them. A restart loop is visible, but a container restarting every ten seconds is the wrong signal
  for a broker that is away, and the consumer must not exit at all.
- **A heartbeat file the worker writes on progress, read by the image's one check** — chosen.

## Decision

1. **A worker writes a heartbeat when it makes progress, and never otherwise**: the consumer when
   its broker accepted it, after each event applied or skipped, and every fifteen seconds while it
   is idle with partitions assigned and its broker answering fetches; the relay and the sweep after
   each pass that finished. Not while the consumer waits for a broker, holds an event, has no
   partition or hears nothing from its broker; not after a pass that failed. A timer of its own
   would make a stuck process healthy.
2. **The heartbeat carries its own maximum age** — when it was written, and how old it may grow —
   so the reader needs no configuration: 90 seconds for the consumer, five pauses and at least two
   minutes for the relay, two pauses and ten minutes for the sweep. It is written whole, beside the
   file and renamed over it; a file that can be read and is not a heartbeat is unhealthy.
3. **One command checks every role**, `python -m aira_common.healthcheck`: a heartbeat when there is
   one, the server's `/healthz` on `AIRA_HEALTH_PORT` when there is not. Both images run it, and
   it prints the reason Docker keeps.
4. **The loops move into the processes**: `manage.py relay --loop` and
   `python -m aira_gateway.retention --loop`, with `--interval` and `--max-age`. A failed pass is
   logged and the loop goes on; SIGTERM ends it between passes. The one-pass forms stay, for a
   scheduler, whose record of the exit status is their health.
5. **The package's root imports nothing.** The check runs every fifteen seconds in every
   container, inside the core the service runs on; `aira_common`'s root re-exports cost each run a
   third of a second, and nothing imported them.

## Consequences

- Positive: every long-running container reports its own health with no logic in Compose; the
  consumer is unhealthy in exactly the states that cost configuration; the same command is a
  Kubernetes `exec` probe; the log says `worker_unhealthy` and `worker_healthy` when the state
  changes, with the reason.
- Negative / trade-offs: Docker's own retries come on top of the age — a consumer cut off from its
  broker reads unhealthy about three minutes after the broker last answered. A pass that hangs is
  reported by the check while it hangs, and in the log only when it ends. A probe that **restarts**
  what is unhealthy must allow a worker's longest pass, or a long sweep is restarted midway and
  rolled back. The heartbeat is a file: a root filesystem with no writable `/tmp` leaves the worker
  working and its container unhealthy, said once in its log.
- Follow-ups: how long the oldest pending outbox event has waited, in Management's readiness, and
  metrics of the workers' progress — `FRD-153` §6.
