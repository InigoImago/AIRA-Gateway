"""OpenTelemetry bootstrap shared by AIRA services (FRD-001).

Sets up tracer/meter/logger providers exporting via OTLP/HTTP to the OpenTelemetry
Collector, plus helpers for W3C trace-context propagation over Kafka message headers.
Everything is gated by an ``enabled`` flag so tests and low-resource setups can run
without a collector.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

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


def _redact_arg(value: object) -> object:
    if not isinstance(value, str) or "?" not in value:
        return value
    path, _, query = value.partition("?")
    return f"{path}?{redact_query_string(query)}"


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


def context_from_kafka_headers(headers: KafkaHeaders | None) -> Context:
    """Extract an OTel context from Kafka-style headers for the consumer side."""
    carrier = {
        key: (value.decode() if isinstance(value, bytes) else value)
        for key, value in (headers or [])
    }
    return extract(carrier)


def build_resource_attributes(service_name: str, environment: str) -> Attributes:
    """Helper exposing the standard resource attributes (used in tests/tools)."""
    return {"service.name": service_name, "deployment.environment": environment}
