"""The lines a process writes before its log export exists, kept until it does (`FRD-631` FR-1).

A process says the most important things first: that Vault let it in, which secrets it read, that
its pseudonym key is its own. Those lines are written while the settings are still being read —
Vault is a settings source — and the export needs the settings to know where to send. So they were
printed and never exported, and an alert on ``level:warning`` in the log store could never see the
warning that says the installation is misconfigured.

They are held instead, from the moment logging is configured, and handed to the export the moment
it exists, each with the time it was written. A process that exports no logs drops them when it
decides so; one that never decides holds at most :data:`CAPACITY` and counts the rest.
"""

from __future__ import annotations

import logging
import threading

#: How many lines are held. A start writes a dozen; the bound is for a process that never
#: configures an export at all and would otherwise hold every line it writes.
CAPACITY = 1000


class EarlyRecords(logging.Handler):
    """Holds what reaches it, in order, up to a bound."""

    def __init__(self, capacity: int = CAPACITY) -> None:
        super().__init__()
        self._held: list[logging.LogRecord] = []
        self._dropped = 0
        self._capacity = capacity
        self._lock_held = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock_held:
            if len(self._held) < self._capacity:
                self._held.append(record)
            else:
                self._dropped += 1

    def take(self) -> tuple[list[logging.LogRecord], int]:
        """Everything held, and how many were not; held no longer."""
        with self._lock_held:
            held, dropped = self._held, self._dropped
            self._held, self._dropped = [], 0
        return held, dropped


#: The logger the early lines are held on, set by :func:`hold_early_records`.
_holding_on: logging.Logger | None = None
#: Whether this process has decided about exporting logs, which ends the holding.
_decided = False


def hold_early_records(logger: logging.Logger) -> bool:
    """Hold what ``logger`` handles until :func:`release_early_records` or
    :func:`discard_early_records`. Once per process; returns whether it began holding."""
    global _holding_on
    if _decided or _holding_on is not None:
        return False
    logger.addHandler(EarlyRecords())
    _holding_on = logger
    return True


def _stop_holding() -> tuple[list[logging.LogRecord], int]:
    global _holding_on, _decided
    _decided = True
    logger, _holding_on = _holding_on, None
    if logger is None:
        return [], 0
    held: list[logging.LogRecord] = []
    dropped = 0
    for handler in [h for h in logger.handlers if isinstance(h, EarlyRecords)]:
        logger.removeHandler(handler)
        some, lost = handler.take()
        held += some
        dropped += lost
    return held, dropped


def release_early_records(handler: logging.Handler) -> int:
    """Hand every held line to ``handler`` — the export, which is in place by now — and stop.

    The records keep the time they were made, and the export stamps a line with that time, so the
    log store orders them where they belong. Returns how many were handed over.
    """
    held, dropped = _stop_holding()
    for record in held:
        handler.handle(record)
    if dropped:
        # Imported here: `aira_common.logging` imports this package at module scope. Written
        # through the ordinary pipeline, which reaches the export by now.
        from aira_common.logging import get_logger

        get_logger("aira_common.observability").warning("early_lines_dropped", count=dropped)
    return len(held)


def discard_early_records() -> None:
    """This process exports no logs: drop what is held, and hold nothing more."""
    _stop_holding()


def reset_for_tests() -> None:
    """Back to holding nothing and having decided nothing — for a test that configures again."""
    global _decided
    _stop_holding()
    _decided = False
