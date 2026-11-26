"""A line AIRA did not write, written as AIRA writes one (`FRD-631` FR-2).

uvicorn says when a server starts and stops, Django when a request failed, Python when a warning is
raised and when a process dies — each through the standard library's logging, with handlers of its
own: plain text on stderr beside AIRA's JSON on stdout, and none of it through the export, since a
server's loggers do not propagate to the root logger where the export is. So the log store never saw
a service start or stop, and whatever parsed the container output met two formats.

Every such record is handed to AIRA's own pipeline instead: rendered as JSON, its fields checked
against the register, its time and trace attached, and exported. A server's access log is switched
off, because AIRA writes its own access line (`aira_common.logging.access`) with the duration and
the route, without the query string, and with the address as the installation writes addresses.
"""

from __future__ import annotations

import logging
import sys
import threading
from types import TracebackType
from typing import Any

import structlog

from aira_common.logging.exceptions import loggable
from aira_common.observability.redaction import ACCESS_LOGGERS, redact_url_query

#: The loggers a server configures with handlers and formats of its own. Their handlers are removed
#: and their records propagate to the root logger, where :class:`ForeignRecords` renders them.
SERVER_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.asgi", "gunicorn", "gunicorn.error")

#: Loggers whose records never reach this handler: AIRA's own rendered lines (already written) and
#: the OpenTelemetry SDK's diagnostics, which are kept off the export they describe.
_NOT_FOREIGN = ("aira.app", "opentelemetry")

#: Django's request logger warns once for every 4xx answer: "Unauthorized: /api/v1/me". The access
#: line says the same with the status and the route, so only the server errors are kept.
_REQUEST_LOGGER = "django.request"

#: structlog's method for each standard level; a level between two is written as the lower.
_METHODS = (
    (logging.CRITICAL, "critical"),
    (logging.ERROR, "error"),
    (logging.WARNING, "warning"),
    (logging.INFO, "info"),
)


def _method(levelno: int) -> str:
    for threshold, name in _METHODS:
        if levelno >= threshold:
            return name
    return "debug"


class ForeignRecords(logging.Handler):
    """On the root logger: every record a library wrote, rendered and exported as AIRA's are.

    Never fails the call that logged: a line about something is never the reason it failed.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(_NOT_FOREIGN):
            return
        if record.name == _REQUEST_LOGGER and getattr(record, "status_code", 500) < 500:
            return
        try:
            fields: dict[str, Any] = {"logger": record.name}
            if record.exc_info and record.exc_info[1] is not None:
                fields["exc_info"] = record.exc_info
            log = structlog.get_logger(record.name)
            getattr(log, _method(record.levelno))(redact_url_query(record.getMessage()), **fields)
        except Exception:  # noqa: BLE001 — see the docstring
            return


def _take_over_servers(level: int) -> None:
    """A server's loggers propagate to the root at the process's level, and its access logger
    writes nothing.

    uvicorn configures its loggers before it imports the application, so this — called while the
    application is configured — comes after and stands. Its access log is off by having no handler
    anywhere above it: uvicorn asks ``hasHandlers()`` per connection and skips the line.
    """
    for name in SERVER_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.propagate = True
        logger.setLevel(level)
    for name in ACCESS_LOGGERS:
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
        logger.propagate = False


def _crash_hooks() -> None:
    """A process that dies of an exception says so in one line, as every other line is written.

    Python prints an uncaught exception as a bare traceback on stderr. Here it is a ``critical``
    line with the error's type, where it was raised and its message without a database's detail —
    what an operator needs to act on a start that failed — and the traceback only at ``DEBUG``.
    """

    def process(kind: type[BaseException], error: BaseException, tb: TracebackType | None) -> None:
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, error, tb)
            return
        structlog.get_logger("aira").critical(
            "process_crashed", error=loggable(error), exc_info=(kind, error, tb)
        )

    def thread(args: threading.ExceptHookArgs) -> None:
        if args.exc_value is None or issubclass(args.exc_type, SystemExit):
            return
        structlog.get_logger("aira").error(
            "thread_crashed",
            error=loggable(args.exc_value),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    # Only where nobody installed a hook of their own — a debugger, a test runner, an agent.
    if sys.excepthook is sys.__excepthook__:
        sys.excepthook = process
    if threading.excepthook is threading.__excepthook__:
        threading.excepthook = thread


def take_over_foreign_records(level: int = logging.INFO) -> None:
    """Route every library's records through AIRA's pipeline, a server's at ``level``.

    Idempotent."""
    root = logging.getLogger()
    if not any(isinstance(handler, ForeignRecords) for handler in root.handlers):
        root.addHandler(ForeignRecords())
    _take_over_servers(level)
    # A warning is a record too, on `py.warnings`, rather than a line printed on stderr.
    logging.captureWarnings(True)
    _crash_hooks()
