"""Shared building blocks for AIRA components."""

from aira_common.config import BaseAiraSettings
from aira_common.errors import AiraError, ErrorDetail, ErrorResponse
from aira_common.events import Event, EventPublisher, InMemoryEventPublisher
from aira_common.health import CheckResult, tcp_reachable
from aira_common.logging import configure_logging, get_logger
from aira_common.observability import (
    configure_observability,
    context_from_kafka_headers,
    kafka_headers_from_context,
    set_span_attributes,
    trace_context_fields,
)

__all__ = [
    "AiraError",
    "BaseAiraSettings",
    "CheckResult",
    "ErrorDetail",
    "ErrorResponse",
    "Event",
    "EventPublisher",
    "InMemoryEventPublisher",
    "configure_logging",
    "configure_observability",
    "context_from_kafka_headers",
    "get_logger",
    "kafka_headers_from_context",
    "set_span_attributes",
    "trace_context_fields",
    "tcp_reachable",
]

__version__ = "0.1.0"
