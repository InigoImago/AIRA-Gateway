"""What of a database error may be written to a log line (`FRD-406`).

The text of a database error can carry the values its statement was about, in two places.
SQLAlchemy's dump of the bound parameters is switched off where every engine is built
(`base._ENGINE_OPTIONS`). The other is the database's own ``DETAIL``: Postgres names the key a
unique constraint refused and quotes the **whole row** a NOT NULL or CHECK constraint refused — a
request's prompt and answer, a configuration event's fields. A log line has no retention clock and
no role gate, so that part is cut and the rest — the error, the statement — is kept for whoever
debugs it.
"""

from __future__ import annotations

#: Where the driver's message hands over to its detail, and where SQLAlchemy's own part resumes.
_DETAIL = "\nDETAIL:"
_STATEMENT = "\n[SQL:"


def loggable(exc: BaseException) -> str:
    """``str(exc)`` without a database's ``DETAIL``, which may run over several lines."""
    text = str(exc)
    head, found, rest = text.partition(_DETAIL)
    if not found:
        return text
    _, statement, tail = rest.partition(_STATEMENT)
    return head + (statement + tail if statement else "")
