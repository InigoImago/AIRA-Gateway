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
from typing import Any

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

    tracer_provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(sample_ratio))
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces"))
    )
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{base}/v1/metrics"))
        ],
    )
    metrics.set_meter_provider(meter_provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs"))
    )
    set_logger_provider(logger_provider)
    # Bridge stdlib logging (framework logs) to OTLP; structlog app logs keep their
    # trace-id/span-id correlation via the logging processor and stream to stdout.
    logging.getLogger().addHandler(LoggingHandler(logger_provider=logger_provider))

    _configured = True
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
