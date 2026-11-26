"""What of an error a log line says (`FRD-631` FR-8).

An error's message is written by whoever raised it, and it carries what that code had in hand: a
database quotes the key a unique constraint refused — ``Key (username)=(max.mustermann) already
exists`` — and the whole row a NOT NULL constraint refused, which may be a request's prompt. A
traceback ends in that message. A log line has no retention clock and no role check, so:

- **every line**: the error's type, where it was raised in AIRA's code, and for a database error the
  constraint, the table and the SQLSTATE — enough to find the defect, nothing it was about;
- **only at ``AIRA_LOG_LEVEL=DEBUG``**: the message and the traceback, for whoever reproduces it.

One processor for every line that hands over an exception (``exc_info``), so no call site decides
on its own, and :func:`loggable` for the call sites that write a database error's message on
purpose — what is expected and said in words: the message without the detail.
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import MutableMapping
from types import TracebackType
from typing import Any

#: Where the driver's message hands over to its detail, and where SQLAlchemy's own part resumes.
_DETAIL = "\nDETAIL:"
_STATEMENT = "\n[SQL:"

#: How far a cause chain is followed for a database's diagnostics: SQLAlchemy and Django each wrap
#: the driver's error once, and a handler may wrap that again.
_CAUSE_DEPTH = 5

#: What marks a frame as AIRA's own, for :func:`location`.
_OWN = ("aira_gateway", "aira_management", "aira_common")

ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def loggable(exc: BaseException) -> str:
    """``str(exc)`` without a database's ``DETAIL``, which may run over several lines.

    SQLAlchemy's dump of the bound parameters is switched off where every engine is built; the
    other place a value hides is the database's own ``DETAIL``, which is cut here, and the rest —
    the error, the statement — is kept for whoever debugs it.
    """
    text = str(exc)
    head, found, rest = text.partition(_DETAIL)
    if not found:
        return text
    _, statement, tail = rest.partition(_STATEMENT)
    return head + (statement + tail if statement else "")


def exc_info_of(value: Any) -> ExcInfo | None:
    """What ``exc_info=`` was given, as a triple; ``None`` for nothing."""
    if isinstance(value, BaseException):
        return (type(value), value, value.__traceback__)
    if isinstance(value, tuple) and len(value) == 3 and isinstance(value[1], BaseException):
        return (type(value[1]), value[1], value[1].__traceback__)
    if value is True:
        kind, error, tb = sys.exc_info()
        return (kind, error, tb) if kind is not None and error is not None else None
    return None


def location(tb: TracebackType | None) -> str | None:
    """``module/path.py:line in function`` of the innermost frame in AIRA's code, else the
    innermost of all; the path from the package down, never the machine's directories."""
    frames = traceback.extract_tb(tb) if tb is not None else []
    if not frames:
        return None
    own = [frame for frame in frames if any(name in frame.filename for name in _OWN)]
    frame = (own or frames)[-1]
    path = frame.filename.replace("\\", "/")
    for name in _OWN:
        at = path.rfind(f"/{name}/")
        if at >= 0:
            path = path[at + 1 :]
            break
    else:
        path = path.rsplit("/", 2)[-1] if "/" in path else path
    return f"{path}:{frame.lineno} in {frame.name}"


def database_diagnostics(exc: BaseException) -> dict[str, str]:
    """The constraint, the table and the SQLSTATE a database error names — never its values.

    Read off the driver's error wherever it is in the chain: SQLAlchemy keeps it as ``orig``,
    Django as ``__cause__``.
    """
    seen: BaseException | None = exc
    for _ in range(_CAUSE_DEPTH):
        if seen is None:
            break
        diag = getattr(seen, "diag", None)
        if diag is not None:
            found = {
                "constraint": getattr(diag, "constraint_name", None),
                "table": getattr(diag, "table_name", None),
                "sqlstate": getattr(seen, "sqlstate", None) or getattr(diag, "sqlstate", None),
            }
            return {key: str(value) for key, value in found.items() if value}
        seen = getattr(seen, "orig", None) or seen.__cause__ or seen.__context__
    return {}


def describe_exceptions(debugging: bool) -> Any:
    """The structlog processor in place of ``format_exc_info``: what of an error a line says.

    ``debugging`` is whether the process logs at ``DEBUG``; only then are the message and the
    traceback written.
    """

    def processor(
        _logger: Any, _method: str, event: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        info = exc_info_of(event.pop("exc_info", None))
        if info is None:
            return event
        kind, error, tb = info
        event.setdefault("error_type", kind.__name__)
        at = location(tb)
        if at:
            event.setdefault("error_at", at)
        for key, value in database_diagnostics(error).items():
            event.setdefault(key, value)
        if debugging:
            event["exception"] = "".join(traceback.format_exception(kind, error, tb))
        return event

    return processor
