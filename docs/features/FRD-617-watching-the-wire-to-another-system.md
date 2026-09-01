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

## 8. Rollout

Nothing changes for an installation that does not set the variable, except that the OpenTelemetry
SDK's own warnings now appear on stdout instead of vanishing — which is a fix, not a feature flag.

The intended use during an integration:

    AIRA_DEBUG_INTEGRATIONS=otel   docker compose up -d gateway && make logs
    AIRA_DEBUG_INTEGRATIONS=all    # while wiring a new external system
    AIRA_DEBUG_INTEGRATIONS=       # once it works
