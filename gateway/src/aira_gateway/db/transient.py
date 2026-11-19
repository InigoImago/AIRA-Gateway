"""Whether a database failure says anything about the work that met it (`FRD-204`, `FRD-405`).

Work that fails because the database was restarting or failing over, had closed the connection, ran
out of connections or chose it as a deadlock's victim succeeds a moment later unchanged. Work that
fails on what it carries — a value a column refuses, a constraint — fails the same way every time.
The two call for opposite answers: the configuration consumer holds an event through the first and
skips it on the second, and the audit writer writes a row once more after the first.

Decided by the error's type and SQLSTATE, never by its message: the message is the server's, and
changes with its version and its language.
"""

from __future__ import annotations

import psycopg
from sqlalchemy import exc as sa_exc

#: SQLSTATE classes in which every condition is about the server's state, not the statement:
#: connection exceptions (`08`), a transaction rolled back for a serialization failure or a
#: deadlock (`40`), insufficient resources such as too many connections or a full disk (`53`),
#: operator intervention — a shutdown, a server still starting, a statement cancelled by its
#: `statement_timeout` (`57`) — and system errors (`58`).
TRANSIENT_CLASSES = frozenset({"08", "40", "53", "57", "58"})

#: Single conditions of other classes that pass the same way: a lock not granted within
#: `lock_timeout`. Its class, `55`, also holds conditions that do not pass.
TRANSIENT_STATES = frozenset({"55P03"})


def is_transient(error: BaseException) -> bool:
    """Whether ``error`` came from the database's state rather than from the work, so the same
    work tried again shortly would likely succeed."""
    if isinstance(error, sa_exc.DisconnectionError | sa_exc.TimeoutError):
        # The pool's own: a connection it could not revive, or none free within its timeout.
        return True
    cause: BaseException | None = error
    if isinstance(error, sa_exc.DBAPIError):
        if error.connection_invalidated:
            # SQLAlchemy found the connection dead and discarded it; the next one is a new one.
            return True
        cause = error.orig
    state = getattr(cause, "sqlstate", None)
    if isinstance(state, str) and state:
        return state[:2] in TRANSIENT_CLASSES or state in TRANSIENT_STATES
    # No state, because no server answered: the driver lost the connection or never reached it.
    return isinstance(cause, psycopg.OperationalError | psycopg.InterfaceError)
