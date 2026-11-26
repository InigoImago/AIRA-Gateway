# ADR-0054 — The log line carries the security record, and what a line may say is a register

- **Status:** Accepted
- **Date:** 2026-11-25
- **Deciders:** Vadim Scheibe

## Context

The record of a request was a span and a database row. The delivery channel (`FRD-618`) forwarded
one span per request to a SIEM, and `FRD-616` drafted a record stream nobody built. On Stage T the
apps export logs to Graylog and **no traces**, so the SIEM received not one request, not one refused
sign-in and not one change — and the log lines it did receive were the few a developer had chosen to
write, beside text the servers printed outside the export.

Those lines were also written by the opposite rule to the one they needed. Every field went out as
given, and a short list named what to pseudonymise; so `directory.search` wrote the name somebody
looked up, `anomaly_detected` the person a finding was about, and `unhandled_error` a database's
quote of the row it refused — each in plain text, in a store with no role check and no retention
clock. A SIEM needs more and the works council needs less, and both are right about different
fields.

## Options considered

- **Turn traces on for the SIEM.** Spans carry the request record, but they are sampled, are
  structured for a tracing backend (the identifying attributes on a parent the SIEM does not join),
  and a deployment that sends logs to a log store does not necessarily run a trace pipeline; refusals
  and changes were not spans at all.
- **A Kafka topic from the gateway** (`FRD-616` (a)). The strongest delivery guarantee, and a new
  topic, ACLs and a producer identity on a broker another team operates — an infrastructure change a
  running installation pays for in tickets.
- **A second, SIEM-only stream with names in plain text**, routed apart in the collector. Gives the
  SIEM what the log store must not have, at the price of a collector configuration per installation
  and a stream whose content contradicts strict data mode.
- **Records as log lines through the one pipeline, written by a register.** No infrastructure: the
  OTLP log export already exists. Every consumer gets the same lines, so the rule for a person and an
  address is the same everywhere.

## Decision

1. **The security record travels as log lines** — one per request (`request_recorded`), per refused
   attempt (`auth_refused`, bounded per source), per configuration change (`config.changed`), per
   content read, stop and finding — through the same pipeline and export as every other line,
   alongside spans where those are exported. The audit trail stays the record; a line is its copy
   for somebody else's tooling.
2. **What a line may carry is a register**, not a list of exceptions: a field is `plain`, `person`
   or `address`, with one type; a field not in the register is withheld and named. A test reads every
   log call in the code.
3. **An address leaves as its network and a keyed pseudonym** by default — enough for a SIEM to count
   and correlate by source, not enough to name a workstation — and as a setting
   (`AIRA_TELEMETRY_IP`) for an installation that needs the whole address or only the network.
4. **An error leaves as its type and its place**; its message and traceback only at `DEBUG`.
5. **A key a process drew for itself is said, not refused**: a warning in the log store and
   `degraded` readiness. Refusing to start would make an upgrade wait on a Vault change.
6. **Everything a process prints is a line of this pipeline**, a library's included, and the first
   lines of a process are held until the export exists.

## Consequences

- A SIEM attached to the log stream sees requests, refusals and changes with no trace pipeline; the
  log store receives about one line per request more than before.
- Persons and addresses are written the same way in every line, so a new line cannot reintroduce a
  name in plain text without a registered field saying it is a person.
- Spans carry `aira.source_net`/`aira.source_id` by default instead of `aira.source_ip`.
- Delivery is the OTLP batch processor's: a full queue or a collector that refuses past its retries
  loses lines. The database is the record, as `FRD-616` §4 says.
- Re-identifying a person in the SIEM goes through the request's id in the console, by the roles
  that may — the same path as for a span.
