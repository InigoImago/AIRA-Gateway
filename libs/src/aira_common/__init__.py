"""Shared building blocks for AIRA components."""

from aira_common.config import BaseAiraSettings
from aira_common.errors import ErrorDetail, ErrorResponse
from aira_common.health import CheckResult, tcp_reachable
from aira_common.logging import configure_logging, get_logger
from aira_common.observability import (
    attribute_model_calls_to,
    configure_observability,
    context_from_kafka_headers,
    kafka_headers_from_context,
    model_call,
    model_call_attributes,
    set_span_attributes,
    trace_context_fields,
)

__all__ = [
    "BaseAiraSettings",
    "CheckResult",
    "ErrorDetail",
    "ErrorResponse",
    "attribute_model_calls_to",
    "configure_logging",
    "configure_observability",
    "context_from_kafka_headers",
    "get_logger",
    "kafka_headers_from_context",
    "model_call",
    "model_call_attributes",
    "set_span_attributes",
    "trace_context_fields",
    "tcp_reachable",
]

__version__ = "0.1.0"
