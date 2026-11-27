# FRD-631 — Every event a structured line, and each line safe to send

> Phase: 7 (operations) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner connected Stage T's OTLP export to Graylog — logs only, over gRPC; traces are
> not exported there — and filed five tickets from what the log store showed and did not show:
> output that bypassed the logger, startup lines that never arrived, a consumer killed on every stop,
> a directory search that wrote the names it looked up, and personal data limited by a list of what
> to hide. With them: the SIEM team needs **more** data, not less — with traces off they had not one
> record of a request. Related: `FRD-616` (the audit trail as an event stream — its record stream,
> built here as log lines), `FRD-618` (the delivery channel), `FRD-627` (strict data mode, FR-14
> pseudonyms), `FRD-625` (the privacy notice), `FRD-632` (the configuration history), `ADR-0054`.

## 1. Summary

Every process writes everything it has to say as one JSON line through one pipeline, and that
pipeline is the only way out: to stdout, and to the OTLP export where one is configured. A line is
written from a closed register of fields, so a person leaves as a pseudonym, an address as the
installation writes one, an error as its type and place, and a field nobody registered not at all.
On top of that, the records a SIEM correlates on are lines now rather than spans only: one per
request, one per refused attempt, one per configuration change, content read, stop and finding,
and one when a process starts and when it stops.

## 2. Goals & non-goals

**Goals**

- Nothing a Python process writes bypasses the pipeline: uvicorn's start and stop, Django's request
  failures, Python's warnings and a process's death are JSON lines, and the export ships them once.
- The first lines of a process — Vault's, the per-process key warning — reach the export.
- Every process stops when told to, says so, and hands over what the export holds.
- A SIEM fed with logs alone sees every request, every refused attempt and every change.
- No person and no exact address in plain text outside the audit trail, by rule rather than by each
  call site remembering.

**Non-goals**

- **No refusal to start without `AIRA_PSEUDONYM_SECRET`** (owner's decision, 2026-11-25): Stage T
  runs without one, and adding a secret is a change to somebody else's Vault. It is said instead —
  a warning that reaches the log store, and `degraded` readiness with the reason (FR-10).
- **No second, SIEM-only stream with names in plain text.** The SIEM receives what the log store
  receives; a person is re-identified through the request's id in the console, by the roles that
  may (`FRD-627`).
- No OTLP attributes per field: the body is the rendered JSON line, one rendering for both sinks.
- The frontend's nginx does not export over OTLP; its JSON lines are on stdout for the platform to
  collect.

## 3. User stories

- As **operations**, I want to see a service start and stop, and what it did, in the log store, so
  that I can tell a quiet service from a stopped one without a shell on the host.
- As **IT Security**, I want every request, every refused sign-in and every change in the SIEM, so
  that I can detect an attack and trace a problem, without traces being exported.
- As a **works council**, I want no person's name and no workstation address in a log anybody with
  access to the log store can read.

## 4. Functional requirements

- **FR-1 — The first lines reach the export.** Lines written after logging is configured and before
  the export exists are held (at most 1000) and handed to the export when it is attached, each with
  the time it was written; a process that exports no logs drops them. Both planes configure the
  export before their first own line; the migration containers report `migrations_applied` or
  `migrations_failed` through it and flush before they end.
- **FR-2 — Every output a structured line.** uvicorn's and Django's records, Python warnings and an
  uncaught exception (`process_crashed`, `thread_crashed`) are rendered by the pipeline; the export
  ships only rendered lines (`aira.app`). The relay writes `relay_run` (at `debug` when idle),
  `relay_event_parked` once per event and `relay_events_still_parked` at most hourly; the retention
  sweep's `print`s are gone; Django's 4xx warnings are dropped as the access line says the same.
- **FR-3 — Every line names its process** (`process`: `api`, `consumer`, `retention`, `relay`,
  `migrate`, …; a Django command by its own name, whether started as `manage.py`, `python -m
  django` or `django-admin`) — named before the process reads its settings, so the lines reading
  them from Vault writes carry it too — and the first exported line of a process is `process_started` with its version,
  stage, strict data mode, address mode, key stability, exported signals and log level.
- **FR-4 — Every process stops when told to.** The consumer handles SIGTERM and SIGINT: it stops
  between two events, finishes and commits an event being applied, leaves one held for retry
  uncommitted, leaves its consumer group, writes `worker_stopped`, and flushes. Both APIs write
  `process_stopping` and `process_stopped` and flush on their lifespan's end; Management's ASGI
  application answers the lifespan protocol it did not speak. Relay and retention flush after
  `worker_stopped`.
- **FR-5 — One access line per request**, written by each plane, never by the server:
  `http_request` with method, route template (path only where none matched), status, duration, the
  address as FR-9 says, and the plane's own fields (request id and caller in the gateway, the actor
  in Management). No query string, ever; no line for `/healthz` or `/readyz`. uvicorn's access log is
  off. **One spelling of a route on both planes**, the gateway's: parameters as `{name}` and a
  leading `/` — `/v1beta/usage/{use_case}`, `/api/v1/use-cases/{slug}/groups/`. Django writes a
  router's route as the regular expression it matches (`api/v1/use-cases/(?P<slug>[^/.]+)/groups/$`)
  and a `path()` without its slash; `aira_common.logging.access.readable_route` writes both as the
  pattern, for every line that carries a route (`http_request`, `auth_refused`, `unhandled_error`),
  through one helper per plane. A test walks every registered pattern of each plane and fails on
  anything of an expression left in what a line would write. The console's nginx writes the same shape as JSON, naming `/api/` and `/gw/` requests by that
  prefix only, without address, `X-Forwarded-For`, user agent or query string, and not the
  container's healthcheck. nginx's own error lines about a request are not written (`error_log …
  crit`): they name the caller's address and the request line with its query string, a Gemini
  client's `?key=` included, whenever an upstream fails.
- **FR-6 — A refused attempt is a line**: `auth_refused` with category (`authentication` or
  `authorization`), outcome, status, the caller where known, the key prefix, the use case asked for,
  AIRA's reason, the request id and the address — at most ten lines per source per minute, the next
  saying how many were held back (`suppressed`). Gateway: wherever a refusal before attribution is
  marked on its span. Management: 401, 403 and the 429 of the failed-sign-in bound.
- **FR-7 — A closed register of fields.** Every field a line may carry is in
  `aira_common.logging.fields`, as `plain`, `person` or `address`, with its one type and its meaning.
  A field not in it is removed and named in `withheld_fields`. A test reads every log call in the
  code and fails on a field the register does not know; `docs/LOGGING.md` is generated from it.
- **FR-8 — An error says where and not what.** A line given an exception carries its type, where in
  AIRA's code it was raised, and for a database error the constraint, table and SQLSTATE; the message
  and traceback only at `AIRA_LOG_LEVEL=DEBUG`. Messages written on purpose are cut of a database's
  `DETAIL` (`loggable`, now shared by both planes).
- **FR-9 — An address as the installation says** (`AIRA_TELEMETRY_IP`, owner's decision:
  `pseudonym` by default). `pseudonym`: `source_net` (the /24 or /48) and `source_id` (a keyed
  pseudonym of the whole address); `network`: `source_net`; `full`: `source_ip`. The same for log
  lines, spans (`aira.source_net`, `aira.source_id`, `aira.source_ip`) and the description of a
  `new_source_ip` finding. The audit row keeps the address whole.
- **FR-10 — A key the process drew itself is said, not refused**: `pseudonym_key_per_process`
  warning after the export exists, and `/readyz` of both planes `degraded` with
  `checks.pseudonyms`. Pseudonyms in use means strict data mode, or addresses written as pseudonyms.
- **FR-11 — The record stream of `FRD-616`, as lines.** `request_recorded` for every audit row (said
  where every row passes, before it is written), `payload_read`, `suspension.created`,
  `suspension.lifted`, `anomaly_detected` and `config.changed` (`FRD-632`), each with a `category`.
- **FR-12 — A directory search names nobody it looked for.** `POST /api/v1/directory/search` with
  the term in the body; the line says the actor, the term's length, the results and the source; the
  console asks after a 300 ms pause.

## 5. Design

`aira_common.logging` became a package, one module per concern: `pipeline` (the processors),
`fields` (the register), `exceptions`, `foreign` (a library's records), `access`, `refusals`,
`lifecycle`. The processor order is: context, level, process, trace, time, stack, **describe the
exception**, **keep known fields**, pseudonymise persons, **write addresses**, hold back local-only
lines, render, forward to the export logger. `aira_common.addresses` holds the address rule;
`aira_common.observability.early` the held lines; `flush_telemetry` and `exported_signals` are in
`observability.providers`, which also filters the OTLP handler to rendered lines.

## 6. Data model

None. (`FRD-632` adds the history table.)

## 7. API

`POST /api/v1/directory/search` replaces `GET /api/v1/directory/?q=`. `/readyz` gains
`checks.pseudonyms`. Span attributes `aira.source_net` and `aira.source_id` replace `aira.source_ip`
unless `AIRA_TELEMETRY_IP=full`.

## 8. Security & privacy

New processing that stores nothing — log lines per request, refusal, change, search and read — is
described in the privacy notice's telemetry activity, with the address mode (`FRD-625`, new
edition). A finding's description no longer names a whole address by default (`FRD-625` §11,
decision 2, taken). Persons stay pseudonymous under strict data mode in every new line.

## 9. Observability

This document is the observability.

## 10. Testing

`libs/tests`: the register, the address modes, error description, a library's lines, the held
lines, the access line, the refusal bound, start and stop lines; the AST scan of every log call.
`gateway/tests`: the consumer's stop, every request a line, the unexpected error. `management/
backend/tests`: the relay's lines, the search, Management's lines. Console specs: the search's pause
and body. The nginx format is checked by `tools/tests`. Mutations `LG-*`.

## 11. Stated gaps

- The OTLP batch processor drops what it cannot queue (2048 records) and what a collector refuses
  after its retries; the audit trail stays the record (`FRD-616` §6).
- A span's exception event, recorded by the OpenTelemetry instrumentations, still carries the
  message; this FRD governs lines.
- A first search after an upgrade from a console bundle still open in a browser answers 405 until
  the page is reloaded.
- `error_at` names the innermost frame in AIRA's code, not a library's.

## 12. Rollout

Images only. Behaviour a running client or consumer must follow: the directory search moved to
`POST` (the console ships with it); spans carry `aira.source_net`/`aira.source_id` instead of
`aira.source_ip` by default — `AIRA_TELEMETRY_IP=full` restores it; a log parser expecting uvicorn's
access line reads `http_request` instead; Stage T reads `degraded` until `AIRA_PSEUDONYM_SECRET` is
set.
