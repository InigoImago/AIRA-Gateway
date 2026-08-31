# FRD-616 — The audit trail as an event stream

> Phase: 7 · Status: **Draft** · Owner: Vadim Scheibe
>
> Origin: asking what it would take to feed a SIEM over OTLP by changing a URL. The transport is a
> configuration change; the **content** is this document.
>
> Related: [`FRD-122`](FRD-122-complete-audit-trail.md) (the audit trail),
> [`FRD-128`](FRD-128-one-post-dispatch-sequence.md) (every request that reached an upstream is
> recorded), [`FRD-204`](FRD-204-config-distribution-kafka.md) (the outbox and the topics),
> [`FRD-505`](FRD-505-requests-and-prompts.md) (payload reads are recorded),
> [`FRD-608`](FRD-608-governance-overview.md) (the register), [`FRD-615`](FRD-615-a-trace-crosses-the-bus.md)
> (what telemetry carries), `ADR-0016`.

**Nothing here is built.**

## 1. Why the obvious answer does not work

Pointing the OpenTelemetry exporter at a SIEM is one line of collector configuration, and it
delivers the wrong thing. A SIEM asks four questions of a system like this:

| The question | Where the answer is today |
| --- | --- |
| Who called which model, and how did it end? | `request_logs`, in the gateway's Postgres |
| Who read somebody's stored prompt? | `payload_access`, same database |
| Who was stopped, by whom, and why? | `access_suspensions` and `anomaly_events`, same database |
| Who changed access, keys or limits? | Management's outbox → Kafka |

**Telemetry answers none of them.** `FRD-615` established what actually travels: spans carrying
attribution, model, outcome, tokens and cost — good for *"why is this slow"* and useless for
*"who read Frau Müller's prompt on Tuesday"*. AIRA's own security lines go to stdout past the
standard library, so they are not in OTLP either. A SIEM fed from the collector today receives a
performance feed.

## 2. What already exists, and it is half of this

**Management already publishes a governance event stream.** `usecase.upserted`,
`membership.upserted`, `membership.removed`, `use_case_group.granted` / `.revoked`,
`api_key.created` / `.revoked`, `budget.*`, `ratelimit.*`, `anomaly_rule.*`, `model.*` — written
through a transactional outbox, published to Kafka, at-least-once, ordered per key. A SIEM can
consume those **today**, with no change to Management at all: it is a second consumer group on
topics the gateway already reads.

That is worth saying first, because it means this feature is not *"build an event stream"*. It is
**"give the data plane the half Management has had since `FRD-204`"** — the acts that happen inside
the gateway, which no other system can see.

## 3. What is missing

Four kinds of record, all in the gateway's database and published nowhere:

- **`request.recorded`** — one per request that reached a model, with the closed `Outcome`
  vocabulary (`served`, `rate_limited`, `budget_exceeded`, `blocked_by_pipeline`, `suspended`,
  `client_gone`, …). This is the volume; everything else is rare.
- **`payload.read`** — who read a stored prompt, which request, on what ground
  (`incident` / `use_case_admin` / `use_case_member`). `ADR-0016` reopened that view *on the
  condition that every read is recorded*; a SIEM is the natural second reader of that record.
- **`access.suspended` / `access.lifted`** — the kill switch, with its author and reason.
- **`anomaly.detected`** — what the detector found, per rule and target.

## 4. Goals and non-goals

**Goals.** One event per act, in a stable schema, at least once, with the closed vocabularies this
system already has. Consumable by a SIEM without AIRA knowing which SIEM.

**Non-goals.**

- **Not a replacement for the audit trail.** The database stays the record; this is a copy for
  somebody else's tooling. If the two disagree, the database is right.
- **No payloads, ever.** Prompts and responses are behind a storage switch, a retention clock and
  a role check. An event stream has none of the three, and a SIEM's retention is measured in years.
  Names of things, never content — the line `audit.tool_summary` already draws.
- **No connector per product.** AIRA emits; a collector, a Kafka connector or the SIEM's own
  ingest translates. Writing a Splunk exporter here is how a project acquires four of them.

## 5. Three transports, and what each costs

**a) A Kafka topic from the gateway.** Symmetrical with `FRD-204` — the same broker, the same
security (`KafkaSecurity`), the same at-least-once semantics, and every SIEM has a Kafka connector.
The gateway would need an **outbox of its own**: it has no producer today, and writing to Kafka
inline on the request path is exactly what `FRD-204` refused.

**b) OTLP logs.** No new infrastructure — the collector is already there and already fans out, so a
SIEM is one exporter. But OTLP/JSON is protobuf-JSON, not an event shape any SIEM parses without a
`transform` processor, and the collector's sending queue is in memory by default: a SIEM outage
loses events unless a file-backed queue is configured. Measured during the round that produced this
document — a dead endpoint produced retries at 24 s, 36 s, 44 s with the spans held in memory.

**c) A pull API with a cursor.** `/v1beta/traces` already pages this way, and a pull model makes
back-pressure the reader's problem rather than ours. It needs an endpoint per record kind, an
authenticated service account, and it makes completeness the *reader's* responsibility — which for
an audit feed is the wrong way round.

(b) can be tried today without touching the reference stack:
`make up-lab LAB_SIEM_ENDPOINT=…` layers a collector configuration that fans out to your endpoint
in OTLP/JSON beside Grafana (`docs/deployment/dev.md`). What arrives is what §1 describes, which is
the point of being able to look before building anything.

**Recommendation: (a), with (b) available.** Kafka because the reliability semantics are already
decided and already operated here; OTLP as the low-ceremony option for an installation that has a
collector and modest requirements.

## 6. The hard parts, named

**Completeness is not free, and it is nearly there.** `FRD-128` guarantees that a request reaching
an upstream is recorded however it ended. But `RequestLogWriter` is an async queue, and a full queue
writes **inline** rather than dropping — so the guarantee holds and is paid for in latency under
pressure. An event stream inherits exactly that trade and must not weaken it: dropping an event to
keep the queue short would turn a guarantee into a best effort, silently.

**Retention becomes the SIEM's.** `request_logs` metadata is kept and payloads expire on the use
case's clock; the register (`FRD-608`) states that. Shipping events to a system that keeps them for
seven years changes the answer to *"how long is this kept"* without changing any screen that
answers it. **This is a decision for the owner, not a design detail**, and it is the reason §4 puts
payloads out of scope rather than behind a switch.

**Personal data crosses a boundary.** `subject`, `username`, `source_ip` and the credential prefix
are what make the feed useful and what make it a disclosure. A new recipient is new processing: the
register has to name it.

**Ordering.** Per use case, keyed like the existing topics. Global ordering across use cases is not
offered and should not be implied — a SIEM correlating by timestamp is doing what it does anyway.

## 7. Open questions for the owner

1. **Which records?** All four of §3, or only the acts (payload reads, suspensions, findings) —
   which are rare, high-value, and avoid shipping the full request volume off-box.
2. **Retention**, per §6: is a SIEM allowed to keep what this system deliberately expires?
3. **Kafka or OTLP** — that is really *"does the SIEM team want a topic or an endpoint"*, and they
   should answer it rather than us.
4. Should Management's existing topics be **documented as a SIEM feed** in `INTEGRATIONS.md`? They
   are consumable today and nobody has been told.
