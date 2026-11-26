"""Structured logging on structlog, shared by both services.

Each service calls :func:`configure_logging` once at start-up and gets loggers from
:func:`get_logger`. Lines are rendered as JSON by default, and one rendering goes to **two** sinks:
stdout, and the stdlib logger :data:`EXPORT_LOGGER` that the OTLP handler ships (`FRD-001` FR-6).
Context variables (trace and request ids) are merged in.

Every line passes the same rules on its way, in this order (`FRD-631`): an error is described
without its values (`exceptions`), a field the register does not name is withheld (`fields`), a
person is written as a pseudonym and an address as the installation writes addresses. A library's
line is rendered the same way (`foreign`), and a line written before the export exists is held for
it (`aira_common.observability.early`).
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from typing import Any

import structlog
from opentelemetry import trace

from aira_common.addresses import write_addresses
from aira_common.logging.exceptions import describe_exceptions
from aira_common.logging.fields import keep_known_fields
from aira_common.logging.foreign import take_over_foreign_records
from aira_common.observability import install_access_log_redaction
from aira_common.observability.early import hold_early_records
from aira_common.pseudonym import pseudonymise_event

#: The stdlib logger every rendered line is also handed to, so the OTLP handler on the root logger
#: can ship it (`FRD-001` FR-6). structlog's `PrintLoggerFactory` alone creates no `LogRecord`.
EXPORT_LOGGER = "aira.app"

#: The field that names which process of a plane wrote a line — the API, the consumer, the relay —
#: since the processes of one plane share a service name (`process_named`).
PROCESS_KEY = "process"

#: The event-dict key that keeps a line **out of the OTLP log pipeline** while leaving it on stdout
#: (`FRD-617` §3.2) — for lines *about* the thing that ships lines, which would otherwise feed
#: themselves back into it. `aira_common.integration_debug` marks its `otel` lines with it.
LOCAL_ONLY_KEY = "local_only"

_LEVELS: dict[str, int] = logging.getLevelNamesMapping()

#: structlog method name → stdlib level, for the two `getLevelNamesMapping` lacks. Both would fall
#: to `INFO` otherwise, and a severity that understates is how an alert stops firing.
_METHOD_LEVELS: dict[str, int] = {"exception": logging.ERROR, "msg": logging.INFO}

#: How :data:`LOCAL_ONLY_KEY` reaches :func:`forward_to_stdlib`, which runs after the renderer and
#: sees a string. A context variable rather than a global, so one thread's local-only line cannot
#: suppress another's ordinary one; it is set on every line, so it never outlives its call.
_hold_back: ContextVar[bool] = ContextVar("aira_log_hold_back", default=False)

#: Which process this is, once an entry point has said (:func:`process_named`).
_process: str | None = None


def process_named(name: str | None) -> None:
    """Name the process every line of it carries from now on: ``api``, ``consumer``, ``relay`` …

    One plane's processes share an image, a service name and the resource attributes of their
    telemetry, so without it a line in the log store does not say whether the API or the consumer
    wrote it.
    """
    global _process
    _process = name or None


def process_name() -> str | None:
    """The name :func:`process_named` gave this process, if any."""
    return _process


def add_process(
    _logger: Any, _method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """structlog processor: the process's name, once one is given."""
    if _process is not None:
        event_dict.setdefault(PROCESS_KEY, _process)
    return event_dict


def add_trace_context(
    _logger: Any, _method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """structlog processor: add ``trace_id``/``span_id`` when a span is active."""
    ctx = trace.get_current_span().get_span_context()
    # An id the line was given wins: a request's record is written after its span has ended.
    if ctx.is_valid and "trace_id" not in event_dict:
        event_dict["trace_id"] = trace.format_trace_id(ctx.trace_id)
        event_dict["span_id"] = trace.format_span_id(ctx.span_id)
    return event_dict


def hold_back_from_export(
    _logger: Any, _method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """structlog processor: take :data:`LOCAL_ONLY_KEY` off the event and remember it.

    Immediately before the renderer, so stdout shows an ordinary line with no marker.
    """
    _hold_back.set(bool(event_dict.pop(LOCAL_ONLY_KEY, False)))
    return event_dict


def forward_to_stdlib(_logger: Any, method_name: str, event: Any) -> Any:
    """structlog processor: hand the **rendered** line to stdlib logging, then return it unchanged.

    After the renderer, so one rendering feeds both sinks and stdout is byte-for-byte unchanged.
    Never allowed to fail the log call. The exported record's `code.*` attributes name this
    function rather than the caller — a guessed `stacklevel` would name a confidently wrong file;
    the rendered body and its `trace_id` carry what a reader needs.
    """
    if not isinstance(event, str) or _hold_back.get():
        return event
    level = _METHOD_LEVELS.get(method_name, _LEVELS.get(method_name.upper(), logging.INFO))
    with contextlib.suppress(Exception):
        logging.getLogger(EXPORT_LOGGER).log(level, event)
    return event


def _prepare_export_logger(level: int) -> None:
    """Make :data:`EXPORT_LOGGER` emit at ``level`` and reach the root logger, quietly.

    - its own level, because the root logger's default `WARNING` would filter out `INFO`;
    - `propagate`, because the OTLP handler is on the root logger and this one has none;
    - a `NullHandler`, because otherwise `logging.lastResort` prints every warning a second time,
      bare on stderr, whenever telemetry is off;
    - held until the export exists, because the first lines of a process are written before it
      does (`aira_common.observability.early`).
    """
    logger = logging.getLogger(EXPORT_LOGGER)
    logger.setLevel(level)
    logger.propagate = True
    if not any(isinstance(handler, logging.NullHandler) for handler in logger.handlers):
        logger.addHandler(logging.NullHandler())
    hold_early_records(logger)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure structlog process-wide.

    Args:
        level: Minimum log level name (case-insensitive).
        json_output: Render JSON lines when True, else a colorized console format.
    """
    log_level = _LEVELS.get(level.upper(), logging.INFO)
    # A server's own access log, should an installation switch it on again: there is no code path
    # of ours to put this on.
    install_access_log_redaction()

    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        add_process,
        add_trace_context,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        # An error's type and place on every line, its message and traceback only at `DEBUG`: both
        # end in what the failing code had in hand (`FRD-631` FR-8).
        describe_exceptions(log_level <= logging.DEBUG),
        # Before anything reads a field: one this register does not name is withheld (FR-7).
        keep_known_fields,
        # A person's name leaves as a pseudonym under strict data mode (`FRD-627` FR-14); a no-op
        # while the mode is off.
        pseudonymise_event,
        # An address leaves as `AIRA_TELEMETRY_IP` says (FR-9).
        write_addresses,
        # Last before the renderer: it must see the event dict, and what it removes must not be
        # rendered.
        hold_back_from_export,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )

    _prepare_export_logger(log_level)

    structlog.configure(
        # `forward_to_stdlib` after the renderer hands over the finished line (`FRD-001` §5a).
        processors=[*processors, renderer, forward_to_stdlib],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    # After structlog is configured: a library's record is rendered by the pipeline just built.
    take_over_foreign_records(log_level)


def get_logger(name: str | None = None, **initial_values: Any) -> Any:
    """Return a bound structlog logger, optionally pre-bound with ``initial_values``."""
    return structlog.get_logger(name, **initial_values)
