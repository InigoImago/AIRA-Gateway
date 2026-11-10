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
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, Sampler, TraceIdRatioBased
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import Attributes

from aira_common.observability.diagnostics import route_sdk_diagnostics
from aira_common.observability.export import watched_export

#: Requests kept out of the traces: the health probes. A probe has no caller, use case or outcome,
#: and at one ask per container every 15 s it crowds real requests out of the trace backend, the
#: OTLP quota and the debug payload. `/readyz` reports its own verdict (`FRD-117`).
#:
#: **One expression over the whole URL, anchored at both ends, matching the path alone.** The
#: instrumentations search it in `scheme://<Host header><path>`, and the host is the caller's to
#: write — so a pattern that matched anywhere would let any request, a refused credential included,
#: leave the traces by naming a host or a slug that contains a probe's name. The optional `?` is
#: Django's: it matches `build_absolute_uri("?")`. No comma, because the variable is a list of them.
HEALTH_PATHS = r"^[a-z]+://[^/]+/(healthz|readyz)\??$"

#: The variable every `opentelemetry.util.http` instrumentation reads, absent a per-library one.
EXCLUDED_URLS_ENV = "OTEL_PYTHON_EXCLUDED_URLS"

#: The variable that names a process's propagators. OpenTelemetry reads it once, at import.
PROPAGATORS_ENV = "OTEL_PROPAGATORS"

#: The three OTLP signals, each with the route the OTLP/HTTP specification appends to a base URL.
SIGNAL_PATHS = {"traces": "/v1/traces", "metrics": "/v1/metrics", "logs": "/v1/logs"}

_configured = False


def propagate_trace_context_only() -> bool:
    """Read a request's W3C trace context, `traceparent` and `tracestate`, and nothing else.

    OpenTelemetry's default also propagates `baggage`, which nothing here reads. Its parser splits
    the header with a pattern that takes quadratic time inside its 8 KB bound, the server
    instrumentation runs it on every request **before authentication**, and the HTTP client
    instrumentation would carry a caller's baggage on to every upstream this process calls. A
    process that propagates only what it uses parses only that.

    An installation that names its propagators in `OTEL_PROPAGATORS` has decided, and keeps its
    decision. Returns whether the restriction was applied.
    """
    if os.environ.get(PROPAGATORS_ENV, "").strip():
        return False
    set_global_textmap(TraceContextTextMapPropagator())
    return True


def exclude_health_probes(paths: str = HEALTH_PATHS) -> str:
    """Keep the health probes out of the traces, for every instrumentation at once.

    Set in the environment rather than per instrumentor: `opentelemetry.util.http` reads it for
    FastAPI, Django and whatever is added next. Never overwrites a value an installation set.
    Returns what is in force.
    """
    return os.environ.setdefault(EXCLUDED_URLS_ENV, paths)


def request_sampler(ratio: float) -> Sampler:
    """Which spans are kept — decided by this installation, never by whoever sent the request.

    The SDK's `ParentBased` follows a **remote** parent's sampled flag, and the remote parent of a
    request is the caller's `traceparent`. A caller who sent `…-00` then left no exported span. For
    a request refused before attribution the span is its only record (`FRD-616`), and the delivery
    channel forwards one record per span to a SIEM (`FRD-618`), so the caller could take its own
    attempts out of the security feed.

    A remote parent is therefore sampled by the same ratio as a root. The caller's trace id is still
    continued, only the decision is ours. `TraceIdRatioBased` decides by trace id and every process
    of an installation is given the same ratio, so a trace that crosses the bus (`FRD-615`) is kept
    or dropped whole. A **local** parent is still followed, so a child of a kept span is kept.
    """
    by_ratio = TraceIdRatioBased(ratio)
    return ParentBased(
        root=by_ratio, remote_parent_sampled=by_ratio, remote_parent_not_sampled=by_ratio
    )


def signal_endpoints(
    endpoint: str | None,
    *,
    traces: str | None = None,
    metrics: str | None = None,
    logs: str | None = None,
) -> dict[str, str]:
    """Where each signal is exported: its own URL when one is given, else the base + its route.

    A signal's own URL is taken **whole**, nothing appended, as the OpenTelemetry SDK does with
    `OTEL_EXPORTER_OTLP_<SIGNAL>_ENDPOINT`: a backend that receives one signal names its own route
    (`/otlp/v1/metrics`, `/loki/api/v1/otlp/v1/logs`). A signal with neither is absent from the
    result and is not exported, so one signal can go somewhere with the base left empty.
    Whitespace counts as empty, because that is what an unset variable comes through Compose as.
    """
    base = (endpoint or "").strip().rstrip("/")
    own = {"traces": traces, "metrics": metrics, "logs": logs}
    resolved: dict[str, str] = {}
    for signal, path in SIGNAL_PATHS.items():
        url = (own[signal] or "").strip()
        if url:
            resolved[signal] = url
        elif base:
            resolved[signal] = f"{base}{path}"
    return resolved


def configure_observability(
    *,
    service_name: str,
    service_version: str = "0.0.0",
    environment: str = "local",
    endpoint: str | None = None,
    traces_endpoint: str | None = None,
    metrics_endpoint: str | None = None,
    logs_endpoint: str | None = None,
    enabled: bool = True,
    sample_ratio: float = 1.0,
) -> bool:
    """Configure global OTel providers exporting OTLP/HTTP, each signal to its own URL.

    ``endpoint`` is the base every signal is sent under; ``traces_endpoint``, ``metrics_endpoint``
    and ``logs_endpoint`` replace it for one signal (:func:`signal_endpoints`). Only a signal with
    a URL gets a provider. Returns True if any signal was configured, False if export is disabled
    or no signal has a URL. Idempotent.
    """
    global _configured
    # **Before the early return**: an instrumentation can be in place with nothing exporting — the
    # control plane instruments Django on `AIRA_OTEL_ENABLED` alone — and it parses the headers
    # either way.
    propagate_trace_context_only()
    if not enabled:
        return False
    targets = signal_endpoints(
        endpoint, traces=traces_endpoint, metrics=metrics_endpoint, logs=logs_endpoint
    )
    if not targets:
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

    # **Before anything is instrumented**: an instrumentor reads the exclusion list when it is
    # constructed, so setting it afterwards excludes nothing.
    exclude_health_probes()

    # **Before the first exporter exists**: the SDK's diagnostics must stop propagating to the
    # root logger, where the OTLP handler below is installed (see `SdkDiagnostics`).
    route_sdk_diagnostics()

    if traces := targets.get("traces"):
        tracer_provider = TracerProvider(resource=resource, sampler=request_sampler(sample_ratio))
        tracer_provider.add_span_processor(
            BatchSpanProcessor(watched_export(OTLPSpanExporter(endpoint=traces), "traces", traces))
        )
        trace.set_tracer_provider(tracer_provider)

    if metrics_url := targets.get("metrics"):
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    watched_export(OTLPMetricExporter(endpoint=metrics_url), "metrics", metrics_url)
                )
            ],
        )
        metrics.set_meter_provider(meter_provider)

    if logs := targets.get("logs"):
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
