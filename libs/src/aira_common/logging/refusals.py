"""A refused access attempt, as one line — at most so many per source (`FRD-631` FR-6).

A request refused before it is attributed — no credential, a wrong one, a use case the credential
may not act on — writes no audit row, so its span was its only record. Where traces are not
exported, and in a SIEM fed with logs, a failed sign-in was therefore nowhere. Each one is a line
now, ``auth_refused``, with what a SIEM correlates on: the outcome, the status, who it was where the
credential named somebody, the key's prefix, the use case asked for, and where it came from —
written as the installation writes people and addresses.

**Bounded per source.** A credential-stuffing run is thousands of refusals a minute from a handful
of addresses, and a line each would flood the log store with the attack it is meant to show. So one
source writes at most :data:`LINES_PER_SOURCE` lines per :data:`WINDOW_SECONDS`; the rest are
counted, and the next line from that source says how many under ``suppressed``.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from aira_common.logging.pipeline import get_logger

_log = get_logger("aira.security")

#: The event every such line carries.
EVENT = "auth_refused"

#: How many lines one source may cause per window, and how long a window is.
LINES_PER_SOURCE = 10
WINDOW_SECONDS = 60.0

#: How many sources are remembered at once; past it the one heard from longest ago is forgotten,
#: so an attack from a million addresses costs bounded memory and loses only its counts.
SOURCES = 10_000

#: The outcome of a refusal that never established who the caller is.
UNAUTHENTICATED = "unauthenticated"


@dataclass(slots=True)
class _Window:
    started: float
    written: int
    suppressed: int


class PerSource:
    """How many lines each source has written in its current window, and how many it was denied."""

    def __init__(
        self,
        limit: int = LINES_PER_SOURCE,
        window: float = WINDOW_SECONDS,
        sources: int = SOURCES,
    ) -> None:
        self._limit = limit
        self._window = window
        self._sources = sources
        self._seen: OrderedDict[str, _Window] = OrderedDict()
        self._lock = threading.Lock()

    def admit(self, source: str, now: float) -> tuple[bool, int]:
        """``(whether to write, how many were held back before it)`` for one more line."""
        with self._lock:
            seen = self._seen.pop(source, None)
            if seen is None or now - seen.started >= self._window:
                carried = seen.suppressed if seen is not None else 0
                self._remember(source, _Window(now, 1, 0))
                return True, carried
            self._remember(source, seen)
            if seen.written < self._limit:
                seen.written += 1
                return True, 0
            seen.suppressed += 1
            return False, 0

    def _remember(self, source: str, window: _Window) -> None:
        self._seen[source] = window
        while len(self._seen) > self._sources:
            self._seen.popitem(last=False)


_bound = PerSource()


def say_refused(
    *,
    outcome: str,
    status: int,
    source_ip: str | None,
    subject: str | None = None,
    auth_method: str | None = None,
    credential: str | None = None,
    use_case_requested: str | None = None,
    reason: str | None = None,
    request_id: str | None = None,
    api: str | None = None,
    route: str | None = None,
    now: float | None = None,
) -> bool:
    """Write one ``auth_refused`` line, unless its source has written its share. Returns whether
    it wrote one.

    ``reason`` is AIRA's own sentence, never what the caller sent; the person and the address are
    written by the pipeline's rules, like every line's.
    """
    write, suppressed = _bound.admit(
        source_ip or "unknown", time.monotonic() if now is None else now
    )
    if not write:
        return False
    fields = {
        "category": "authentication" if outcome == UNAUTHENTICATED else "authorization",
        "outcome": outcome,
        "status": status,
        "subject": subject,
        "auth_method": auth_method,
        "credential": credential,
        "use_case_requested": use_case_requested,
        "reason": reason,
        "request_id": request_id,
        "api": api,
        "route": route,
        "source_ip": source_ip,
        "suppressed": suppressed or None,
    }
    _log.warning(EVENT, **{key: value for key, value in fields.items() if value is not None})
    return True


def forget_sources_for_tests() -> None:
    """Every source's window, gone — for a test that counts lines from a fresh start."""
    global _bound
    _bound = PerSource()
