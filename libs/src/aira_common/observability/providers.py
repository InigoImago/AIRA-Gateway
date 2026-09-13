"""The global OpenTelemetry providers, exporting OTLP/HTTP to the collector (`FRD-001`).

Gated by ``enabled`` so tests and low-resource setups run without a collector.
"""

from __future__ import annotations

import logging
import os

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.util.types import Attributes

from aira_common.observability.diagnostics import route_sdk_diagnostics
from aira_common.observability.export import watched_export

#: Paths kept out of the traces: the health probes. A probe has no caller, use case or outcome, and
#: at one ask per container every 15 s it crowds real requests out of the trace backend, the OTLP
#: quota and the debug payload. `/readyz` reports its own verdict (`FRD-117`).
HEALTH_PATHS = "healthz,readyz"

#: The variable every `opentelemetry.util.http` instrumentation reads, absent a per-library one.
EXCLUDED_URLS_ENV = "OTEL_PYTHON_EXCLUDED_URLS"

_configured = False


def exclude_health_probes(paths: str = HEALTH_PATHS) -> str:
    """Keep the health probes out of the traces, for every instrumentation at once.

    Set in the environment rather than per instrumentor: `opentelemetry.util.http` reads it for
    FastAPI, Django and whatever is added next. Never overwrites a value an installation set.
    Returns what is in force.
    """
    return os.environ.setdefault(EXCLUDED_URLS_ENV, paths)


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

    Returns True if observability was configured, False if it was disabled or no endpoint was
    given. Idempotent.
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

    # **Before anything is instrumented**: an instrumentor reads the exclusion list when it is
    # constructed, so setting it afterwards excludes nothing.
    exclude_health_probes()

    # **Before the first exporter exists**: the SDK's diagnostics must stop propagating to the
    # root logger, where the OTLP handler below is installed (see `SdkDiagnostics`).
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
    # Bridge stdlib logging to OTLP; structlog lines reach it through `aira_common.logging`.
    logging.getLogger().addHandler(LoggingHandler(logger_provider=logger_provider))

    _configured = True
    return True


def build_resource_attributes(service_name: str, environment: str) -> Attributes:
    """The standard resource attributes (used in tests and tools)."""
    return {"service.name": service_name, "deployment.environment": environment}
