"""Structured logging setup based on structlog.

Both AIRA services call :func:`configure_logging` once at startup and obtain loggers via
:func:`get_logger`. Logs are rendered as JSON by default and go to **two** sinks from one
rendering — stdout, and a stdlib logger the OTLP handler ships (:data:`EXPORT_LOGGER`, `FRD-001`
FR-6). Context variables (e.g. trace/request ids) are merged in automatically.
"""

from __future__ import annotations

import contextlib
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


#: The stdlib logger an application log line is *also* handed to, so that whatever is attached to
#: the root logger can ship it (`FRD-001` FR-6).
#:
#: **The third of the observability baseline that was not wired.** `configure_observability` has
#: attached an OTLP `LoggingHandler` to the root logger since `FRD-001`, and this module configured
#: structlog with `PrintLoggerFactory` — which writes to stdout and creates no `logging.LogRecord`
#: at all. So every line this system writes about itself (`rate_limited`, `oidc_token_rejected`,
#: `config_event_failed`, `unhandled_error`) went to stdout and nowhere else, while the Compose
#: stack collects no container output and uvicorn's access logger is configured with
#: `propagate: False`. Traces and metrics arrived; **logs were the signal nobody had**, under a
#: diagram in `FRD-001` §5 that says `logs (Loki)`.
#:
#: Two correct halves and no wire, the shape `LESSONS.md` §1 lists — and one that had been
#: *written down* rather than fixed: the 2026-08-31 DEVLOG entry names it in passing as something
#: "a reader will look for and not find". A note is not a wire.
EXPORT_LOGGER = "aira.app"

#: structlog's method name → the stdlib level to emit at. `exception` is `error` with a traceback
#: and has no entry in `getLevelNamesMapping`; `msg` is structlog's own generic method. Both would
#: otherwise fall to `INFO`, and a severity that understates is how an alert stops firing.
_METHOD_LEVELS: dict[str, int] = {"exception": logging.ERROR, "msg": logging.INFO}


def forward_to_stdlib(_logger: Any, method_name: str, event: Any) -> Any:
    """structlog processor: hand the **rendered** line to stdlib logging, then return it unchanged.

    Placed after the renderer on purpose. A processor before it would have to render a second time
    to produce something a `LogRecord` can carry, and two renderings of one event are two answers
    to *what did this line say*. One rendering, two sinks: stdout is byte-for-byte what it was.

    Never allowed to fail the log call. A handler that raises — an exporter mid-shutdown, a full
    queue — must not turn a warning into an exception on the path that was trying to report
    something.

    **What the exported record's `code.*` attributes name is this function**, not the line that
    logged — the record is created here, and the depth between here and the application varies
    with structlog's own call chain, so guessing a `stacklevel` would put a *confident* wrong file
    on every line instead of an obvious one. Written down rather than approximated: everything a
    reader needs is in the body, which is the rendered event with all of its keys, and the
    `trace_id` in it is what ties the line to its request.
    """
    if not isinstance(event, str):
        return event
    level = _METHOD_LEVELS.get(method_name, _LEVELS.get(method_name.upper(), logging.INFO))
    with contextlib.suppress(Exception):
        logging.getLogger(EXPORT_LOGGER).log(level, event)
    return event


def _prepare_export_logger(level: int) -> None:
    """Make :data:`EXPORT_LOGGER` emit at ``level`` and reach the root logger, quietly.

    Three properties, each deliberate:

    - **its own level**, because a record is filtered by the level of the logger it is emitted on
      and the root logger's default is `WARNING` — an `INFO` line would never reach a handler;
    - **`propagate`**, because the OTLP handler is on the root logger and this one has none;
    - **a `NullHandler`**, because `logging.lastResort` prints to **stderr** whenever a record
      finds no handler anywhere in the chain. Without it a gateway with telemetry switched off
      would print every warning twice — once as JSON on stdout and once bare on stderr.
    """
    logger = logging.getLogger(EXPORT_LOGGER)
    logger.setLevel(level)
    logger.propagate = True
    if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        logger.addHandler(logging.NullHandler())


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

    _prepare_export_logger(log_level)

    structlog.configure(
        # `forward_to_stdlib` is **after** the renderer, which is what lets it hand over the
        # finished line rather than render a second copy of it (`FRD-001` §5a).
        processors=[*processors, renderer, forward_to_stdlib],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None, **initial_values: Any) -> Any:
    """Return a bound structlog logger, optionally pre-bound with ``initial_values``."""
    return structlog.get_logger(name, **initial_values)
