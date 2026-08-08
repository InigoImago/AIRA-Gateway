"""Structured logging setup based on structlog.

Both AIRA services call :func:`configure_logging` once at startup and obtain loggers via
:func:`get_logger`. Logs are rendered as JSON by default (suitable for shipping via OTLP);
context variables (e.g. trace/request ids) are merged in automatically.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog
from opentelemetry import trace

from aira_common.observability import install_access_log_redaction

_LEVELS: dict[str, int] = logging.getLevelNamesMapping()


def add_trace_context(
    _logger: Any, _method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """structlog processor: add ``trace_id``/``span_id`` when a span is active."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = trace.format_trace_id(ctx.trace_id)
        event_dict["span_id"] = trace.format_span_id(ctx.span_id)
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog process-wide.

    Args:
        level: Minimum log level name (case-insensitive).
        json_output: Render JSON lines when True, else a colorized console format.
    """
    log_level = _LEVELS.get(level.upper(), logging.INFO)
    # Every service that configures logging gets it: the access log is written by the web server,
    # not by us, so there is no code path of ours to put this on instead.
    install_access_log_redaction()

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        add_trace_context,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None, **initial_values: Any) -> Any:
    """Return a bound structlog logger, optionally pre-bound with ``initial_values``."""
    return structlog.get_logger(name, **initial_values)
