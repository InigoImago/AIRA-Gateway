"""OpenTelemetry for both planes (`FRD-001`), one module per concern.

providers    the global tracer/meter/logger providers and the health-probe exclusion
export       the watched OTLP exporter: one line per export, partial rejections included
payload      a batch rendered as OTLP/JSON, for debugging
diagnostics  the SDK's own log lines, kept out of the pipeline they describe
redaction    credentials out of URLs, request lines and the access log
spans        span attributes, and who a model call was made for
propagation  trace context over Kafka headers, producer and consumer side
"""

from aira_common.observability.diagnostics import SdkDiagnostics, route_sdk_diagnostics
from aira_common.observability.export import WatchedExport, watched_export
from aira_common.observability.payload import payload_as_json, set_payload_rendering
from aira_common.observability.propagation import (
    KafkaHeaders,
    Processing,
    consuming,
    context_from_kafka_headers,
    kafka_headers_for,
    kafka_headers_from_context,
    traceparent_from_context,
)
from aira_common.observability.providers import (
    EXCLUDED_URLS_ENV,
    HEALTH_PATHS,
    build_resource_attributes,
    configure_observability,
    exclude_health_probes,
)
from aira_common.observability.redaction import (
    ACCESS_LOGGERS,
    REDACTED,
    SENSITIVE_QUERY_PARAMS,
    AccessLogRedaction,
    install_access_log_redaction,
    redact_query_string,
    redact_target,
    redact_url_credentials,
    redact_url_query,
)
from aira_common.observability.spans import (
    attribute_model_calls_to,
    model_call,
    model_call_attributes,
    set_span_attributes,
    trace_context_fields,
)

__all__ = [
    "ACCESS_LOGGERS",
    "EXCLUDED_URLS_ENV",
    "HEALTH_PATHS",
    "REDACTED",
    "SENSITIVE_QUERY_PARAMS",
    "AccessLogRedaction",
    "KafkaHeaders",
    "Processing",
    "SdkDiagnostics",
    "WatchedExport",
    "attribute_model_calls_to",
    "build_resource_attributes",
    "configure_observability",
    "consuming",
    "context_from_kafka_headers",
    "exclude_health_probes",
    "install_access_log_redaction",
    "kafka_headers_for",
    "kafka_headers_from_context",
    "model_call",
    "model_call_attributes",
    "payload_as_json",
    "redact_query_string",
    "redact_target",
    "redact_url_credentials",
    "redact_url_query",
    "route_sdk_diagnostics",
    "set_payload_rendering",
    "set_span_attributes",
    "trace_context_fields",
    "traceparent_from_context",
    "watched_export",
]
