"""OpenTelemetry bootstrap shared by AIRA services (FRD-001).

Sets up tracer/meter/logger providers exporting via OTLP/HTTP to the OpenTelemetry
Collector, plus helpers for W3C trace-context propagation over Kafka message headers.
Everything is gated by an ``enabled`` flag so tests and low-resource setups can run
without a collector.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Status, StatusCode
from opentelemetry.util.types import Attributes

KafkaHeaders = list[tuple[str, bytes]]

_configured = False


def configure_observability(
    *,
    service_name: str,
    service_version: str = "0.0.0",
    environment: str = "local",
    endpoint: str | None = None,
    enabled: bool = True,
    sample_ratio: float = 1.0,
) -> bool:
    """Configure global OTel providers exporting OTLP/HTTP to ``endpoint``.

    Returns True if observability was configured, False if it was disabled or no
    endpoint was given (a no-op). Safe to call more than once (idempotent).
    """
    global _configured
    if not enabled or not endpoint:
        return False
    if _configured:
        return True

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )
    base = endpoint.rstrip("/")

    # **Before the first exporter exists.** What the SDK says about a failed export must stop
    # propagating to the root logger, because the handler installed there a few lines below is the
    # OTLP one — see :class:`SdkDiagnostics` for what that cost.
    route_sdk_diagnostics()

    traces = f"{base}/v1/traces"
    metrics_endpoint = f"{base}/v1/metrics"
    logs = f"{base}/v1/logs"

    tracer_provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(sample_ratio))
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(watched_export(OTLPSpanExporter(endpoint=traces), "traces", traces))
    )
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                watched_export(
                    OTLPMetricExporter(endpoint=metrics_endpoint), "metrics", metrics_endpoint
                )
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(watched_export(OTLPLogExporter(endpoint=logs), "logs", logs))
    )
    set_logger_provider(logger_provider)
    # Bridge stdlib logging (framework logs) to OTLP; structlog app logs keep their
    # trace-id/span-id correlation via the logging processor and stream to stdout.
    logging.getLogger().addHandler(LoggingHandler(logger_provider=logger_provider))

    _configured = True
    return True


class WatchedExport:
    """An OTLP exporter that says what each export attempt did (`FRD-617` §3.1).

    The SDK hands a batch to an exporter on a background thread and keeps the answer to itself: a
    success is reported nowhere at any log level, and a failure is reported on a stdlib logger
    whose only handler — with OTLP logging on — is the exporter that just failed. So the question
    *"did anything actually get through"* had no answer inside the process at all, and
    `tools/lab_status.py` answers only the leg **after** this one.

    Written by delegation rather than by subclassing, because the three signals do not share a
    base: `SpanExporter.export(spans)`, `LogExporter.export(batch)` and
    `MetricExporter.export(metrics_data, timeout_millis, **kwargs)` differ in signature, and
    `PeriodicExportingMetricReader` reaches past the interface entirely to read
    `_preferred_temporality` and `_preferred_aggregation` off whatever it is given. `__getattr__`
    forwarding satisfies all of that without this module knowing which of the three it holds.

    **Always wrapped, gated per call.** The alternative — wrap only when the channel is on — makes
    the wiring itself conditional on a setting read at start-up, and this project has paid for
    exactly that shape often enough to name it (`LESSONS.md` §1). `watch` costs one set membership
    test when nobody is watching, and an export happens on a timer, not per request.
    """

    __slots__ = ("_endpoint", "_exporter", "_signal")

    def __init__(self, exporter: Any, signal: str, endpoint: str) -> None:
        self._exporter = exporter
        self._signal = signal
        self._endpoint = endpoint

    def __getattr__(self, name: str) -> Any:
        # `_exporter` itself is a slot, so it is found by normal lookup and never arrives here —
        # except before `__init__` has run, where forwarding would recurse until the stack ends.
        if name == "_exporter":
            raise AttributeError(name)
        return getattr(self._exporter, name)

    def export(self, *args: Any, **kwargs: Any) -> Any:
        from aira_common.integration_debug import watch

        with watch(
            "otel", "export", signal=self._signal, target=self._endpoint, items=_batch_size(args)
        ) as call:
            result = self._exporter.export(*args, **kwargs)
            # **The failure that raises nothing.** An OTLP exporter answers `FAILURE` as a *value*
            # after it has exhausted its own retries, so a channel watching only for exceptions
            # would report every unsuccessful export as a success that took a while — which is the
            # exact reassurance this feature exists to stop giving.
            name = getattr(result, "name", str(result))
            call.note(result=name)
            if name != "SUCCESS":
                call.failed(f"exporter returned {name}")
            return result


def watched_export[Exporter](exporter: Exporter, signal: str, endpoint: str) -> Exporter:
    """Wrap ``exporter`` in :class:`WatchedExport`, keeping its type for the SDK's signature.

    The cast is the one place this file is less than honest with the type checker, and it is a true
    statement about run time: `WatchedExport` forwards every attribute it does not define, so it is
    a structural stand-in for whichever of the three exporter protocols it holds. The three
    protocols have no common base — `SpanExporter`, `MetricExporter` and `LogExporter` are
    unrelated classes with different `export` signatures — so the alternative is three casts at
    three call sites, and a cast repeated three times is one that is eventually written a fourth
    time around something that is *not* a stand-in.
    """
    return cast(Exporter, WatchedExport(exporter, signal, endpoint))


def _batch_size(args: tuple[Any, ...]) -> int | None:
    """How many items this export carried, where that is a countable thing.

    Spans and log records arrive as a sequence; metrics arrive as a `MetricsData` tree that has no
    meaningful single count. `None` rather than `0` for the third: "we did not count" and "there
    was nothing" are different statements, the same distinction `FRD-117`'s prober draws between
    unprobed and healthy.
    """
    if not args:
        return None
    try:
        return len(args[0])
    except TypeError:
        return None


class SdkDiagnostics(logging.Handler):
    """Print what the OpenTelemetry SDK says about itself, instead of posting it to itself.

    **The mechanism behind a whole day of not being able to see why OTLP pushes failed.** The SDK
    reports an export failure — with the endpoint and the underlying error — on a stdlib logger
    under `opentelemetry.*`. That record propagates to the root logger, and with
    `AIRA_OTEL_ENABLED=true` the only handler there is :class:`LoggingHandler`, the OTLP one. So
    the sentence explaining why the exporter could not post was queued for export **through the
    exporter that could not post**, and reached no terminal, no file and no collector.
    `logging.lastResort` is no help: it prints only when a record finds *no* handler, and this one
    found the broken one.

    So the `opentelemetry` tree stops propagating and comes here instead, where it is written to
    stdout like every other line and marked local-only so it cannot re-enter the pipeline it is
    about.

    Its level is left alone deliberately — inherited, so `WARNING` and above, which is what the SDK
    uses for the things worth reading. Lowering it to see successful exports would buy every
    instrumentation library's `DEBUG` chatter as well, and successful exports are already reported,
    with a duration, by :class:`WatchedExport`.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Imported here, not at module scope: `aira_common.logging` imports this module for
            # the access-log redaction, and a second edge the other way is an import cycle.
            from aira_common.logging import get_logger

            log = get_logger("opentelemetry.sdk")
            say = log.warning if record.levelno >= logging.WARNING else log.info
            say(
                "otel_sdk",
                level=record.levelname,
                logger=record.name,
                message=redact_url_query(record.getMessage())[:400],
                local_only=True,
            )
        except Exception:  # noqa: BLE001 — a diagnostic must never be the reason something fails
            return


def route_sdk_diagnostics() -> bool:
    """Send the `opentelemetry` logger tree to stdout rather than into OTLP. Idempotent.

    Returns whether it installed the handler, so a caller can say so and a test can assert it
    without reading the logging module's private state.
    """
    logger = logging.getLogger("opentelemetry")
    if any(isinstance(handler, SdkDiagnostics) for handler in logger.handlers):
        return False
    logger.addHandler(SdkDiagnostics())
    # Nothing under `opentelemetry` may reach the root logger, because the root logger is where the
    # OTLP handler lives. This is the whole fix; the handler above is what keeps the records
    # readable once they stop going there.
    logger.propagate = False
    return True


def set_span_attributes(attributes: Mapping[str, object]) -> None:
    """Set primitive (non-None) attributes on the current span; no-op if none is recording."""
    span = trace.get_current_span()
    for key, value in attributes.items():
        if isinstance(value, str | int | float | bool):
            span.set_attribute(key, value)


# Query parameters that may carry a credential (the Gemini wire protocol passes API keys as
# ``?key=``). They must never reach a span attribute, a log line, or an exported trace.
REDACTED = "REDACTED"
SENSITIVE_QUERY_PARAMS = frozenset(
    {"key", "api_key", "apikey", "access_token", "token", "password"}
)


def redact_query_string(query: str) -> str:
    """Return ``query`` with the values of credential-bearing parameters replaced.

    Order and unknown parameters are preserved so the redacted string stays useful for
    debugging (e.g. ``alt=sse&key=REDACTED``).
    """
    if not query:
        return query
    parts: list[str] = []
    for pair in query.split("&"):
        name, separator, _value = pair.partition("=")
        if separator and name.lower() in SENSITIVE_QUERY_PARAMS:
            parts.append(f"{name}={REDACTED}")
        else:
            parts.append(pair)
    return "&".join(parts)


class AccessLogRedaction(logging.Filter):
    """Keep credentials out of the *access* log, not only out of exported spans.

    `redact_span_query` has kept `?key=<api key>` out of OpenTelemetry since `ADR-0007`, and the
    web server's own access log — the one that goes to stdout, is collected by whatever ships
    container logs, and is readable by everyone who can read logs — recorded the request line
    verbatim. A Gemini client authenticating the documented way therefore wrote its API key into
    the log of every request it made. A trace backend is usually the *better*-guarded of the two.

    The filter rewrites the record's arguments rather than the formatted message, because uvicorn
    formats the line itself and the message does not exist yet when a filter runs. It redacts any
    string argument that carries a query string, rather than the one positional index uvicorn
    happens to use today — a filter pinned to `args[2]` is one silently disabled by an upgrade.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_redact_arg(arg) for arg in args)
        elif isinstance(args, dict):
            record.args = {key: _redact_arg(value) for key, value in args.items()}
        return True


def redact_url_query(value: str) -> str:
    """Return ``value`` — a URL or a request line — with credential-bearing parameters replaced.

    The one definition, read by three places that each have a URL and must not export it as
    written: the access log (above), the **inbound** server span (`gateway/app.redact_span_query`)
    and the **outbound** client span (`gateway/telemetry.redact_client_span_url`, `FRD-117` FR-5).
    The third arrived on 2026-08-31 with httpx instrumentation, and it is the one where the
    credential is *ours*: `upstreams/gemini.py` authenticates with ``?key=<api key>`` on every
    call. A fourth reader spelling this out again is how one of them comes to redact a shorter
    list than the others.
    """
    if "?" not in value:
        return value
    path, _, query = value.partition("?")
    return f"{path}?{redact_query_string(query)}"


def redact_url_credentials(value: str) -> str:
    """Replace a ``user:password@`` in a URL's authority with the user alone.

    The **other** place a credential hides in a URL, and the one `redact_url_query` cannot see. A
    Gemini key rides in the query string; a Redis, AMQP or Postgres URL carries its password in the
    authority — `redis://aira:s3cret@redis:6379/0` is the ordinary spelling, and it is the value of
    `AIRA_REDIS_URL` in any deployment that authenticates.

    Written against the `scheme://` marker rather than with `urlsplit`, because two of the three
    readers of this family are handed a **request line** (`GET /v1/x?y HTTP/1.1`) rather than a
    URL, and a parser confident enough to find an authority in that is one that will eventually
    mangle a path. No marker, no authority, nothing to do.
    """
    marker = "://"
    if marker not in value:
        return value
    scheme, _, rest = value.partition(marker)
    authority, slash, path = rest.partition("/")
    if "@" not in authority:
        return value
    userinfo, _, host = authority.rpartition("@")
    # The user is kept: "which account did we connect as" is exactly the question an integration
    # is debugging, and it is not the secret half.
    user = userinfo.partition(":")[0]
    return f"{scheme}{marker}{user}:{REDACTED}@{host}{slash}{path}"


def redact_target(value: str) -> str:
    """Both halves, for an address that is about to be written to a log line.

    `aira_common.integration_debug` reports *where* a call went, and the addresses it is handed
    come from six different clients — an OTLP endpoint, a broker list, a JWKS URI, a Vault path, a
    Redis URL. Between them they use both hiding places, so the one function that takes a target
    applies both rules rather than leaving each call site to know which kind it holds.
    """
    return redact_url_credentials(redact_url_query(value))


def _redact_arg(value: object) -> object:
    return redact_url_query(value) if isinstance(value, str) else value


#: The loggers that emit a request line. Named rather than filtered at the root, because a filter
#: on the root logger does not run for records emitted through a child logger with its own handler
#: — which is exactly how uvicorn is configured.
ACCESS_LOGGERS = ("uvicorn.access", "gunicorn.access", "django.server")


def install_access_log_redaction() -> None:
    """Attach :class:`AccessLogRedaction` to the access loggers, once."""
    for name in ACCESS_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(existing, AccessLogRedaction) for existing in logger.filters):
            logger.addFilter(AccessLogRedaction())


def trace_context_fields() -> dict[str, str]:
    """Return ``trace_id``/``span_id`` (hex) for the current span, if one is active."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return {}
    return {
        "trace_id": trace.format_trace_id(ctx.trace_id),
        "span_id": trace.format_span_id(ctx.span_id),
    }


def kafka_headers_from_context() -> KafkaHeaders:
    """Inject the current trace context into Kafka-style headers (``list[(str, bytes)]``)."""
    carrier: dict[str, str] = {}
    inject(carrier)
    return [(key, value.encode()) for key, value in carrier.items()]


def traceparent_from_context() -> str:
    """The W3C ``traceparent`` for the current span, or ``""`` when none is active.

    **For a context that has to be stored rather than sent.** A transactional outbox breaks the
    causal chain on purpose: the console's request writes a row and returns, and a *separate
    process* publishes it seconds later. `kafka_headers_from_context` reads the ambient span, and
    in that publisher there is none — so it injected nothing, on every event, in every deployment
    (`FRD-615`). Captured where the span exists, carried in the row, restored at publish.
    """
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier.get("traceparent", "")


def kafka_headers_for(traceparent: str = "") -> KafkaHeaders:
    """Trace headers for a message: the **stored** context, or the current span's.

    One function rather than a branch at the call site, because the two cases are one question —
    *which trace does this message belong to* — and a producer that had to choose would be a
    producer where one of the two answers is eventually forgotten.
    """
    if traceparent:
        return [("traceparent", traceparent.encode())]
    return kafka_headers_from_context()


def context_from_kafka_headers(headers: KafkaHeaders | None) -> Context:
    """Extract an OTel context from Kafka-style headers for the consumer side."""
    carrier = {
        key: (value.decode() if isinstance(value, bytes) else value)
        for key, value in (headers or [])
    }
    return extract(carrier)


@dataclass(slots=True)
class Processing:
    """The handle a consumer marks its outcome on. Yielded by :func:`consuming`."""

    _span: Any

    def failed(self, exc: BaseException) -> None:
        """Record that this message could not be applied.

        The consumer catches every exception on purpose — one bad event must not take the whole
        consumer down (`worker.apply_one_message`) — so nothing propagates out of the `with` block
        for the span to notice. Without this the failure is a log line and the trace is green,
        which is the state that teaches somebody to trust the trace view least.
        """
        self._span.record_exception(exc)
        self._span.set_status(Status(StatusCode.ERROR, type(exc).__name__))


@contextmanager
def consuming(
    destination: str, headers: KafkaHeaders | None, attributes: Mapping[str, object] | None = None
) -> Iterator[Processing]:
    """Continue the producer's trace while one message is processed.

    **The other end of a wire that had only one.** `kafka_headers_from_context` has put a
    `traceparent` on every message since `FRD-001`, and `context_from_kafka_headers` — the function
    that takes it off again — was read by nothing but its own test. So a configuration change made
    in the console produced a span in Management, the message carried the context correctly, and
    the gateway's application of it appeared as nothing at all: the one thing tracing across two
    planes is *for* did not hold. Two correct halves and no wire, the shape `LESSONS.md` §1 lists.

    Named for the messaging conventions rather than for us: `"<destination> process"` is what a
    trace backend groups on, so these spans sit beside every other queue consumer somebody
    operates rather than in a shape only this project uses.

    A no-op when observability is off — `get_tracer` then returns the API's non-recording
    implementation, so this costs a function call and changes nothing.
    """
    tracer = trace.get_tracer("aira_common.messaging")
    with tracer.start_as_current_span(
        f"{destination} process",
        context=context_from_kafka_headers(headers),
        kind=trace.SpanKind.CONSUMER,
    ) as span:
        span.set_attribute("messaging.system", "kafka")
        span.set_attribute("messaging.destination.name", destination)
        span.set_attribute("messaging.operation", "process")
        for key, value in (attributes or {}).items():
            if isinstance(value, str | int | float | bool):
                span.set_attribute(key, value)
        yield Processing(span)


def build_resource_attributes(service_name: str, environment: str) -> Attributes:
    """Helper exposing the standard resource attributes (used in tests/tools)."""
    return {"service.name": service_name, "deployment.environment": environment}
