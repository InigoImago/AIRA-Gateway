"""Structured logging setup based on structlog.

Both AIRA services call :func:`configure_logging` once at startup and obtain loggers via
:func:`get_logger`. Logs are rendered as JSON by default (suitable for shipping via OTLP);
context variables (e.g. trace/request ids) are merged in automatically.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

_LEVELS: dict[str, int] = logging.getLevelNamesMapping()


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog process-wide.

    Args:
        level: Minimum log level name (case-insensitive).
        json_output: Render JSON lines when True, else a colorized console format.
    """
    log_level = _LEVELS.get(level.upper(), logging.INFO)

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
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
