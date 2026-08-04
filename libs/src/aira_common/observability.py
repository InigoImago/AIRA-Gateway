"""OpenTelemetry bootstrap shared by AIRA services (FRD-001).

Sets up tracer/meter/logger providers exporting via OTLP/HTTP to the OpenTelemetry
Collector, plus helpers for W3C trace-context propagation over Kafka message headers.
Everything is gated by an ``enabled`` flag so tests and low-resource setups can run
without a collector.
"""

from __future__ import annotations

import logging

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
