# FRD-615 — A trace crosses the bus

> Phase: 1 (observability) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: asking what actually travels over OTLP, and finding that the answer for the two-plane
> path was *nothing*.
> Related: [`FRD-001`](FRD-001-observability-baseline.md) (the OTel baseline),
> [`FRD-204`](FRD-204-config-distribution-kafka.md) (config over Kafka),
> [`ADR-0004`](../adr/ADR-0004-observability-grafana-otel-lgtm.md).

## 1. Problem

`FRD-001` propagates trace context over Kafka headers. Both halves were there and tested — the
producer injects, `context_from_kafka_headers` extracts, with a round-trip test written the same
day — and a trace still stopped dead at the bus. **Two correct halves and no wire**, the shape
`LESSONS.md` §1 lists, and this is its eighth instance.

Broken in *two* places, and the second is the interesting one:

**The consumer never read it.** `context_from_kafka_headers` was called by nothing but its own
test, and `apply_one_message` opened no span at all. So the gateway's application of a
configuration change belonged to no trace.

**The producer never wrote it either.** `kafka_headers_from_context()` reads the **ambient** span —
and the outbox breaks the causal chain on purpose: the console's request writes a row inside its
transaction and returns, and the *relay*, a separate process, publishes it seconds later with no
span of its own. So the injection produced an empty carrier, on every event, in every deployment.
The line looked correct and did nothing.

Nobody could notice, because `AIRA_OTEL_ENABLED` is off by default: the whole chain was
unexercised.

## 2. What it costs to be missing

The one question tracing across two planes exists to answer — *somebody changed this in the
console; when did it take effect, and did it?* — had no answer. Config distribution is exactly
where that matters: `FRD-204`'s consumer is asynchronous and idempotent by design, so "the console
says it is saved" and "the gateway is enforcing it" are genuinely two facts, seconds to minutes
apart, and a revoked key that goes on working is the failure mode `apply_one_message` already
carries a long docstring about.

## 3. Design

**The context is stored, not injected.** `OutboxEvent.traceparent` holds the W3C context of the
request that caused the event, captured in `record_to_outbox` — which runs inside the view's
transaction, under Django's instrumented request, where a span exists. The relay passes it to
`KafkaRecord`, and `kafka_headers_for(traceparent)` publishes under it.

One function rather than a branch at the call site: *which trace does this message belong to* is
one question with two sources — the stored context, or the current span for anything published
inside a request — and a producer that had to choose is a producer where one answer is eventually
forgotten.

**The consumer opens a `CONSUMER` span** parented to it, named `"<topic> process"` after the
messaging conventions rather than after us, so these spans sit beside every other queue consumer an
operator watches. Attributes: `messaging.system`, `messaging.destination.name`,
`messaging.operation`, the Kafka partition and offset, and `aira.event_type`.

**A failure is red.** `apply_one_message` catches every exception on purpose — one bad event must
not take the consumer down — and that is also what stops the failure reaching the span. So the
handle yielded by `consuming()` records it explicitly. A trace that stays green while the log says
otherwise is worse than no trace, because somebody reads the green one.

**Blank is honest.** An event with no request behind it — the seed, a management command — carries
no context and gets a trace of its own rather than being dropped.

## 4. Functional requirements

**FR-1** — An event published through the outbox carries the trace context of the request that
caused it, not the publisher's.

**FR-2** — The gateway's application of an event is a span in that trace, with the topic, partition
and offset needed to find the message.

**FR-3** — An event that cannot be applied is recorded as an error **on the span** as well as in
the log.

**FR-4** — An event with no causing request is applied normally, in a trace of its own.

**FR-5** — All of the above is a no-op when observability is off: `get_tracer` then returns the
API's non-recording implementation.

## 5. Testing

Eight cases in `gateway/tests/test_a_trace_crosses_the_bus.py`, two in `test_outbox.py`, one in
`test_kafka.py`, and **four mutations** (`BUS1`–`BUS4`).

`BUS3` — *the message is published under the stored context, not the publisher's* — **survived its
first run**, twice, for two different reasons, and both are recorded here because both are traps
this harness's own notes name:

1. the property was asserted on `kafka_headers_for` directly, one call short of the producer that
   uses it. `send` is `# pragma: no cover` for its aiokafka I/O, and the header construction above
   it is ours: the test now drives `AiokafkaProducer.send` with the transport replaced.
2. with that test written it still survived, because `TRACE_BUS` did not name the file it lives
   in. *"When you add a mutation, name every file whose tests you expect to fail — not the file the
   code lives beside."*

## 6. What is still true after this

Two things a reader will look for and not find, neither of them changed here:

- **Tokens and cost are not metrics.** `get_meter` appears nowhere in the project; the figures live
  in `request_logs` and are served through the reporting API. They are span *attributes* on a
  single request, not a time series.
- **AIRA's own log lines are not exported.** structlog writes to stdout through
  `PrintLoggerFactory`, past the standard library, so the OTLP log handler on the root logger
  carries framework records only — uvicorn, Django, library warnings. `payload_read`,
  `request_suspended` and `oidc_token_rejected` are in the container log, not in Loki.
