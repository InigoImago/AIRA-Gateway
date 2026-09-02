# FRD-617 — Watching the wire to another system

> Phase: 1 (observability) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: a day spent unable to see why OTLP pushes were not arriving, with an integration round
> for Vault, Kafka and Keycloak still ahead.
> Related: [`FRD-001`](FRD-001-observability-baseline.md) (the OTel baseline),
> [`FRD-116`](FRD-116-vault-secrets.md) (Vault), [`FRD-204`](FRD-204-config-distribution-kafka.md)
> (Kafka), [`FRD-134`](FRD-134-clock-skew-in-token-validation.md) (token validation),
> [`ADR-0022`](../adr/ADR-0022-a-call-to-another-system-says-so.md).

## 1. Problem

Everything this system says about itself is about **requests it received**. There is a server span
per request, an audit row per answer, a degradation log per feature. There is almost nothing about
the calls it *makes* to the systems it is integrated with — and integration is precisely the phase
where those are the only calls that matter.

Three concrete holes, each measured rather than supposed:

**OTLP export is a black box, and the SDK's own explanation is fed back into the thing that is
broken.** `configure_observability` hands three OTLP exporters to `BatchSpanProcessor`,
`PeriodicExportingMetricReader` and `BatchLogRecordProcessor` and never hears from them again: a
success is reported nowhere at any log level, and a failure is reported by the SDK on a stdlib
logger under `opentelemetry.*`. That logger propagates to the root logger — where, with
`AIRA_OTEL_ENABLED=true`, **the only handler is the OTLP `LoggingHandler`**. So the sentence
explaining why the export failed was itself queued for export through the exporter that had just
failed, and reached no terminal, no file and no collector. `logging.lastResort` could not save it
either: it only prints when a record finds *no* handler, and this one found the broken one.

That is not a metaphor for the problem the user hit; it is the mechanism. A collector pointed at
the wrong port produced perfectly ordinary-looking output on both sides and no diagnosis anywhere.
`make lab-status` (`tools/lab_status.py`) answers the *second* leg — collector to a SIEM, out of
`otelcol_exporter_sent_*` against `send_failed_*` — and there was no equivalent, of any kind, for
the first leg from a service to the collector.

**Kafka's connect is unobserved.** `AiokafkaProducer.start()` is where `SASL_SSL`, a mechanism
name, a trust store and a broker address are all first tested against reality, and it was three
lines with no logging and a `# pragma: no cover`. `send_and_wait` returns the partition and offset
a record landed on and the return value was discarded. On the consumer side, `apply_one_message`
logs an event it could not *apply*; nothing logs an event that arrived.

**An unreachable Keycloak is indistinguishable from a bad token.** `JwtVerifier.verify` fetches
the signing key inside the same `try` as the decode, and `PyJWKClientError` is a subclass of
`PyJWTError` — so a refused connection, a DNS failure and a read timeout against the JWKS endpoint
all came out as `oidc_token_rejected`, at `INFO`, with `reason` naming a class nobody reads, and a
`401` to the caller. Every user of the installation is told their credential is invalid at the
moment the identity provider goes away.

And underneath that, a second defect the first one hid: `build_jwks_client` constructed a
`PyJWKClient` with **no timeout**, so PyJWT's default of 30 seconds applied, and `PyJWKClient`
fetches with `urllib` — synchronously, from `resolve_principal`, which is an `async` dependency.
A Keycloak that accepts connections and does not answer therefore blocked the gateway's **event
loop** for thirty seconds per fetch: not one slow request, every concurrent request on that
worker, including the ones authenticating with an API key and the ones asking `/readyz`.

## 2. What it costs to be missing

Integration work is a loop of *change one thing, look at what happened*. Without the second half
the loop degrades into changing several things and guessing, which is how a wrong port survives an
afternoon. The three systems still to be integrated — Vault, Kafka with SASL, Keycloak — are
exactly the three whose failures are silent, slow, or mislabelled as somebody else's fault.

## 3. Design

### 3.1 One channel, six systems, one switch

`aira_common.integration_debug` is a single, deliberately small module. A call to another system is
wrapped in `watch(system, operation, **fields)`, which times it, classifies the outcome as `ok`,
`timeout` or `failed`, and emits **one structured line** carrying the system, the operation, the
target, the duration in milliseconds, and — where it failed — the exception type and its message.

The systems are named, not free text: `otel`, `kafka`, `auth`, `vault`, `redis`, `postgres`. A
closed vocabulary is what makes the switch a switch rather than a grep, and an unknown name in the
setting is **refused at startup** rather than silently watching nothing (`LESSONS.md` §3).

    AIRA_DEBUG_INTEGRATIONS=            # off — the default, and what a working installation runs
    AIRA_DEBUG_INTEGRATIONS=otel        # one system
    AIRA_DEBUG_INTEGRATIONS=kafka,auth  # several
    AIRA_DEBUG_INTEGRATIONS=all         # every system this build knows

**Off is genuinely off.** Disabled, `watch` performs one frozenset membership test and yields a
shared no-op; nothing is formatted, timed or allocated per field. There is no second knob: the
lines are emitted at `INFO` (`WARNING` when the call failed) so that turning the channel on is
sufficient, and an installation does not have to discover that it also needs `AIRA_LOG_LEVEL=DEBUG`
before anything appears.

### 3.2 The line about OTel must not travel by OTel

The loop in §1 is not only a bug in the SDK's logging path; it is a property of anything this
channel says about `otel`. A line reporting a failed span export becomes a log record, which is
queued for export, which fails, which produces another line. Left alone it is a self-sustaining
trickle that never lets the log pipeline go quiet and makes *"did my logs get through"*
self-referential.

So the channel marks its `otel` events **local-only**, and `aira_common.logging` honours that:
`hold_back_from_export` — a processor that runs immediately before the renderer — takes the flag
off the event dict and remembers it, and `forward_to_stdlib`, which runs *after* the renderer and
is the only bridge into the OTLP log pipeline, returns without forwarding. The line is on stdout,
byte-for-byte like every other line, and it is nowhere else. The flag has to be carried in a
context variable because a structlog processor sees the rendered string and not the event dict, and
the two processors run synchronously in the same call.

The same reasoning fixes the SDK's own diagnostics: `configure_observability` now attaches a relay
to the `opentelemetry` logger, sets `propagate = False` on it, and re-emits those records through
this channel. They are printed instead of being posted into the exporter that could not post them.

### 3.3 What each system reports

| System | Operations | The question it answers |
| --- | --- | --- |
| `otel` | `export` (per signal), `sdk` | Did this batch of spans/metrics/logs reach the collector, how long did it take, and what did the SDK say when it did not |
| `kafka` | `producer.start`, `producer.send`, `producer.stop`, `consumer.start`, `consumer.receive` | Did the broker accept our credentials; what topic/partition/offset did this record land on; what arrived |
| `auth` | `jwks.fetch`, `token.verify` | Is the identity provider answering, how fast, and is a `401` about the token or about the provider |
| `vault` | `login`, `read` | Did AppRole authenticate, and did the path exist |
| `redis` | `script` | Is the counter store answering, and how slowly |
| `postgres` | `connect`, `error` | Did a new pooled connection open, and what did the driver say when it could not |

`postgres` and `redis` are deliberately at **connection** granularity and not per statement. A line
per query is not integration debugging, it is a second slow query log, and the trace already
carries the statements (`FRD-117` §5.3).

### 3.5 Told apart, through whatever the client wrapped it in

*"The port is wrong"* and *"something is swallowing the packets"* are answered by different people
looking at different things, so `outcome` separates `failed` from `timeout`. Three ways of asking,
because clients agree on none of them: `TimeoutError`, the type's **name** (httpx, redis-py,
aiokafka and urllib3 each define their own), and the `__cause__` chain, bounded at three.

The third was added after the demonstration in §7 rather than designed in. `PyJWKClient` raises
`PyJWKClientConnectionError` for a refused connection **and** for a read timeout, with the real
answer one `__cause__` down — so every identity-provider failure read as `failed`, and the field
that exists to separate those two separated nothing on the one system where the question is asked
per request.

### 3.10 The payload itself

Asked for directly: *print the JSON that goes over.* Two places, and they answer different
questions — which is the whole of the design here.

**`AIRA_DEBUG_OTEL_PAYLOAD=3`** prints three items of every batch as OTLP/JSON on the service's own
stdout, rendered through the **exporter's own encoder** so it cannot drift from what is sent. A
number rather than a flag, because the useful request is "show me three spans" and never "show me
the 512 this batch holds"; what is printed is a real OTLP/JSON document of that many, truncated in
content and faithful in shape. Metrics go whole — they arrive as a tree, and a truncated tree is
not a document.

It is **not the bytes on that leg**: the applications post `application/x-protobuf` and cannot be
made to post JSON (§3 of `INTEGRATIONS.md`). Printing JSON beside a protobuf request invites the
conclusion that the encoding is switchable, so the setting's own documentation says it is not.

**`LAB_PAYLOAD_PATH`** gives the exact bytes the receiver gets, because the collector is already
sending JSON. And it is a path rather than a flag for a measured reason: `/dev/stdout` mangles a
large payload — Docker's json-file driver cuts at a 16 KiB boundary, and a 49 692-character
document came back truncated at 48 KiB and would not parse, while everything under ~8 KiB was
intact. A file in `deploy/compose/payload/` comes out whole. A collector pipeline is a static list,
so the exporter is always in it and writes to `/dev/null` until the path says otherwise.

Held back from the OTLP log pipeline like every `otel` line (§3.2), and for a sharper reason here:
a rendering of a *log* batch, logged, joins the next log batch — a doubling per export rather than
a slow leak.

### 3.9 An export is a timer, not a step in a request

*Reported from use: I watch a request go through the pipeline and I do not see it go through
OTel.* The lines were there; they were **illegible**, and for a reason worth naming.

`redis/script` and `postgres/connect` happen inside the request, so they carry its `trace_id`. An
OTLP export happens on the SDK's own thread every few seconds carrying whatever accumulated — so
it has no `trace_id`, and beside lines that name a trace, one that names nothing reads as belonging
to nothing at all. `items=26` does not help: it is spans, and nobody thinks in spans.

The line now carries `traces=N` — **how many requests are in the batch**, counted from the span
contexts already in memory. `items=26 traces=2` says the thing a person actually wants to know.
Added only for the `traces` signal, because a `traces: null` beside a metrics export is noise.

It still cannot say *whether yours* is in there, and that is honest: a batch is a batch. The
answer to that question is the `x-trace-id` on the response and a lookup in the trace backend.

### 3.8 What a `SUCCESS` from an exporter is worth, and the two hops after it

*Reported after the first day of use: the channel shows a line for OTel and you still cannot see
that anything **arrived**.* Correct, and the reason is worth stating precisely.

`SpanExportResult.SUCCESS` means `resp.ok` — the next hop answered 2xx. It does not mean the batch
was kept. OTLP defines a **partial success**: the collector answers `200` with a body carrying
`rejected_spans` and an `error_message` when it took some of a batch and dropped the rest (a full
queue, an attribute limit, a timestamp it will not accept). `opentelemetry-exporter-otlp-proto-http`
reads `resp.ok` and throws the body away, so every one of those came back `SUCCESS` — and the
channel printed a clean green line about telemetry that had been discarded. That is this project's
own sentence turned back on it: *"no errors" and "it arrived" are different statements.*

Three changes:

- **The response is read.** `WatchedExport` wraps the exporter's `_export` on the *instance* — the
  seam where the `requests.Response` still exists — records it, and parses `partial_success` per
  signal. A rejected count makes the line `failed`, with the collector's own reason.
- **`http_status` is its own field.** The enum says "2xx"; the code says which, and the two are
  reported beside each other rather than one standing in for the other.
- **`make otel-status`** answers the hops a service cannot see, out of the collector's own
  counters: `receiver_accepted` against `receiver_refused`, then `exporter_sent` against
  `send_failed`. Those counters existed only under `make up-lab` — the reference stack's collector
  published nothing at all, so *did it arrive* had no answer anywhere. The reference config now
  carries the `metrics:` reader and the port is published.

```
  application ──▶ collector ──▶ Grafana / your SIEM
     │               │                  │
     │               │                  └── make otel-status  (forwarded / undelivered)
     │               └── make otel-status  (accepted / refused)
     └── AIRA_DEBUG_INTEGRATIONS=otel     (left, took Nms, answered 200, N rejected)
```

Reaching for `_export` is a private-API cost taken deliberately: the alternatives are a `requests`
adapter that would see every call the process makes, or reimplementing the exporter.
`test_the_exporter_still_has_the_seam_we_reach_through` asks the **real** exporter whether the
method is still there, so the upgrade that renames it turns something red rather than making the
partial-success reading quietly vacuous (`LESSONS.md` §7).

### 3.7 The one system that runs before the switch is read

`VaultSource` is a settings *source*, so `load_secrets` runs **inside** `GatewaySettings()` — and
every entry point calls `configure_logging` and `configure_integration_debug` with the *finished*
settings, one step later. So the single system whose entire life is start-up was the one the
channel could not describe. Found on the running stack, not in review: a gateway pointed at
`vault:8299` failed closed exactly as `FRD-116` requires, with `AIRA_DEBUG_INTEGRATIONS=all`, and
said nothing at all — while its two Vault log lines came out in structlog's *unconfigured* console
format rather than the JSON every other line in that container uses.

`load_secrets` therefore configures both itself, after the `configured` check and before the first
remote call, reading the switch from the **environment** because settings are what is being built.
A value this build cannot parse is left off rather than raised on: the settings validator refuses
the process a moment later with the better message, and a secret loader is the wrong place to
report a typo in a debug switch.

The general shape is worth carrying past this feature: **a wire configured from settings cannot
cover what runs while the settings are being built.**

### 3.6 `report`, not `emit`

The function that writes a line is `report`. `emit` is this codebase's word for *publishing a
configuration event to the outbox*, and two guards read the source for `emit("…")` to check that
every event Management publishes has a topic and a branch in the gateway's consumer. A second
`emit` with an unrelated first argument made both of them fail on `postgres` — which is the guards
working, and a name worth changing rather than an exclusion worth adding.

### 3.4 Auth, told apart

`JwtVerifier.verify` now fetches the signing key in its own step. A `PyJWKClientError` becomes
`oidc_jwks_unavailable` at `WARNING`, naming the JWKS URI and the underlying error; a signature,
issuer, audience or expiry problem stays `oidc_token_rejected` at `INFO`. Two different people act
on those two lines.

`build_jwks_client` takes a timeout, defaulting to **5 seconds** rather than PyJWT's 30, and
`resolve_principal` runs the whole validation through `asyncio.to_thread`. Both halves are needed:
the timeout bounds how long a hung provider can hold a worker thread, and the thread is what keeps
it off the event loop at all.

**A malformed token stays a `401`, and that took a fix.** Splitting the fetch out of `verify`
narrowed what the fetch's own `except` covers, and `PyJWKClient` parses a token to find its `kid`
*before* it fetches anything — so a truncated or corrupted bearer raised `DecodeError` straight out
of `verify()`: a `500` where the answer had always been a `401`. Found by pointing the
demonstration below at a hand-written token, which is exactly the value a client sends.
`LESSONS.md` §1: **a caller's own value must never become a server error.** The fetch step catches
`PyJWTError` and separates only the *connection* error out of it.

**The status code for an outage stays `401`.** Answering `503` when the provider is unreachable is arguably more
honest, and it is a change to the error contract of both API surfaces and to what a client's retry
logic does — recorded here as a considered decision rather than an oversight, and left to its own
round if the operators of an installation want it.

## 4. Functional requirements

- **FR-1** One setting, `AIRA_DEBUG_INTEGRATIONS`, selects which systems are watched. Empty is off
  and is the default. `all` selects every known system.
- **FR-2** An unknown system name refuses the process at startup, naming the valid ones.
- **FR-3** Disabled, the channel costs one set membership test per call site and emits nothing.
- **FR-4** Every watched call emits exactly one line, whether it succeeded or failed, carrying
  `system`, `operation`, `outcome`, `duration_ms`, and the caller's own fields.
- **FR-5** `outcome` distinguishes `timeout` from `failed`, because they send whoever reads the
  line to different places — including where a client library has wrapped the timeout in a type of
  its own (§3.5).
- **FR-6** A watched call **never** changes the behaviour of the call it watches: the exception
  propagates unchanged, the return value is untouched, and a failure inside the channel is
  suppressed.
- **FR-7** No line carries a credential. Targets pass through `redact_url_query`, the same
  definition the access log and both span redactions read (`ADR-0007`).
- **FR-8** Lines about `otel` are written to stdout only and never enter the OTLP log pipeline.
- **FR-9** The OpenTelemetry SDK's own diagnostics are printed rather than exported.
- **FR-9a** An export the collector **partly rejected** is reported as a failure, with the count
  and the collector's reason — not as the `SUCCESS` its exporter returns.
- **FR-9b** The collector's own accepted/refused and sent/failed counters are readable on the
  reference stack, not only under the laboratory overlay.
- **FR-10** A JWKS fetch failure is reported apart from a token rejection.
- **FR-11** Token validation does not block the event loop, and the JWKS fetch has a bounded
  timeout.

## 5. Security & privacy

The channel reports *that* a call happened and how it ended. It never reports a payload: no token,
no secret value, no message body, no SQL parameters. `vault` reports the path and the names
resolved, which is what `FRD-116` already established as the safe half. Targets are redacted with
the shared `redact_url_query`, so a Gemini-style `?key=` cannot reach a line even if a caller
passes a full URL by mistake.

An error *message* from another system can name internal topology — a hostname, a port, a realm.
That is the point of the feature and the reason it is off by default and per-system: the lines go
to the service log, which is already the place this project treats as readable by operators and not
by callers (`/readyz` deliberately says less, `FRD-117`).

## 6. Testing

Hermetic unit tests for the channel itself (on/off, the vocabulary refusal, outcome
classification, redaction, the no-op cost, the suppression of a failure inside the channel), and —
because *"two correct halves and no wire"* is this project's most-repeated defect — a test per
wiring that drives the **real** call path with a fake far end and asserts the line appeared:

- a real `BatchSpanProcessor` around a watched exporter whose far end raises, and a real
  `PeriodicExportingMetricReader` around a watched metric exporter, which is the one that reads
  private attributes off the exporter it is given;
- `AiokafkaProducer` with an injected client factory, so `start`/`send`/`stop` are covered rather
  than pragma'd, and `consume_forever` with a fake consumer;
- `JwtVerifier` with a resolver that raises `PyJWKClientConnectionError`;
- `VaultClient` against a transport that times out;
- a `RedisRunner` whose client raises;
- a SQLAlchemy engine that cannot connect.

And the property that pays for the design in §3.2: a line marked local-only reaches stdout and does
**not** reach `EXPORT_LOGGER`. Mutation entries in `tools/mutation_check.py` for the switch, the
redaction, the local-only hold-back and the JWKS split.

## 7. Demonstrated, not asserted

### 7a. Against fakes, in the harness

Every line below was produced by pointing a real client at a wrong port, a dead host or a socket
that accepts and never answers — the check the owner asked for, and the one that found the `500`
in §3.4 and the mis-classified timeout in §3.5.

| What was done | What the channel said |
| --- | --- |
| OTLP collector on `:4319`, nothing listening | `otel export failed 1165ms result=FAILURE`, and underneath it the SDK's own `Connection refused ... retrying in 1.16s` — the sentence that used to be posted to the exporter that had just failed |
| OTLP to a socket that accepts and never answers | `otel export failed 2004ms`, with `Read timed out. (read timeout=2.0)` |
| Kafka broker on `:9999` | `kafka producer.start failed 64ms KafkaConnectionError: Unable to bootstrap from [('127.0.0.1', 9999, …)]` |
| Keycloak that accepts and never answers | `auth jwks.fetch` **timeout** `2009ms`, plus `oidc_jwks_unavailable` saying in words that this is not the callers' credentials |
| Keycloak on a closed port | `auth jwks.fetch` **failed** `0.6ms` — the same exception type as the row above, told apart by its cause |
| Vault on `:8299`, nothing listening | `vault login failed 0.4ms ConnectError: [Errno 111] Connection refused`, with `secret_id_source` and no secret-id |
| Redis on `:6399` with a password in the URL | `redis script failed 36ms`, `target=redis://aira:REDACTED@127.0.0.1:6399/0` |
| Postgres on port 1 with a password in the URL | `postgres error failed`, the driver's own *"Is the server running on that host"*, `target=postgresql+psycopg://aira:REDACTED@…` |
| The same run with `AIRA_DEBUG_INTEGRATIONS=` | **nothing**. The feature's own fallback lines still appear; the channel says not one word. |

### 7b. Against the running Compose stack

Repeated on `make up` + `make up-apps`, with `AIRA_DEBUG_INTEGRATIONS=all` reaching all three
processes. This is where §3.7 was found — the feature was silent for Vault, and no test could have
said so, because every test constructs its settings before it looks.

| What was done to the stack | What the channel said |
| --- | --- |
| Nothing; normal operation | `otel export ok 2.9ms signal=traces items=7 result=SUCCESS`, `kafka consumer.start ok target=kafka:9092 group=aira-gateway`, `postgres connect ok target=postgresql+psycopg://aira:REDACTED@postgres:5432/aira_gateway` |
| A config change through the outbox | producer `send ok topic=aira.usecases partition=0 offset=4897` and, in the other container, `consumer.receive ok offset=4897 event_type=usecase.upserted applied=True` — **the same offset at both ends** |
| A JWT with an unknown `kid`, Keycloak up | `auth jwks.fetch failed 209ms PyJWKClientError: Unable to find a signing key that matches: "nosuchkid"` → `oidc_token_rejected` at `INFO`. The provider answered; the token is wrong. |
| `docker stop aira-keycloak`, same request | `auth jwks.fetch failed 33ms PyJWKClientConnectionError: No address associated with hostname` → `oidc_jwks_unavailable` at **`WARNING`**, saying in words that this is not the callers' credentials. Both are `401` to the caller; only the log tells them apart. |
| `AIRA_OTEL_ENDPOINT=…:4319` | `otel export failed 7738ms items=7 result=FAILURE`, and above it the SDK's own `Connection refused … retrying in 4.61s` — the sentence that used to have nowhere to go |
| `docker stop aira-kafka`, then the relay | `kafka producer.start failed 33ms KafkaConnectionError: Unable to bootstrap from [('kafka', 9092, …)]` |
| `docker stop aira-redis`, then a request | `redis script failed 0.3ms ConnectionError: Connection closed by server`, beside the existing `counters_unavailable` fallback line |
| `VAULT_ADDR=http://vault:8299` | `vault read failed 1.4ms target=http://vault:8299/v1/secret/data/aira ConnectError: [Errno 111] Connection refused` — **after §3.7; before it, nothing** |
| `AIRA_DEBUG_INTEGRATIONS=` and restart | 0 channel lines out of 10 in the whole start-up; the gateway healthy |

Two properties fell out of the run that are worth knowing before switching this on:

**Every line carries `trace_id` and `span_id`** where a span is active, because the channel logs
through the same structlog chain as everything else. A `redis script` line and the request that
caused it are one click apart in the trace backend; no design went into that, and it should not be
removed by accident.

**Volume, measured.** Ten requests produced eleven channel lines: **ten `redis/script`**, one
`postgres/connect` — the pool reuses connections, so that is per *connection*, not per request —
and `otel/export` on its own timer. `redis` is the only per-request system, which is the practical
argument for naming systems rather than only having an on switch: `kafka,auth,vault,otel` is the
quiet set for a busy gateway.

**And the mechanism behind §1, confirmed in the shipped image**, through the same startup path the
app takes:

```
root handlers          : ['LoggingHandler']     <- the OTLP exporter, and nothing else
opentelemetry handlers : ['SdkDiagnostics']
opentelemetry propagate: False
```

## 8. Rollout

Nothing changes for an installation that does not set the variable, except that the OpenTelemetry
SDK's own warnings now appear on stdout instead of vanishing — which is a fix, not a feature flag.

The intended use during an integration:

    AIRA_DEBUG_INTEGRATIONS=otel   docker compose up -d gateway && make logs
    AIRA_DEBUG_INTEGRATIONS=all    # while wiring a new external system
    AIRA_DEBUG_INTEGRATIONS=       # once it works
