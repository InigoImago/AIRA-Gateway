"""One line per call to a system that is not ours (`FRD-617`).

Everything else the codebase reports is about a request it **received**. Integration work needs the
other direction: what did we send, where, how long did it take, and did it arrive.
`watch(system, operation, **fields)` times one call and emits exactly one structured line with the
system, operation, outcome, duration and the caller's fields. It is not a metrics facility and not a
slow-query log (`postgres` and `redis` are watched per connection, §3.3).

- **Off is off, and on is one switch.** `AIRA_DEBUG_INTEGRATIONS` names systems from
  :data:`SYSTEMS`; empty is the default and costs one set lookup per call site. Lines go out at
  `INFO` (`WARNING` on failure), so no log-level change is needed. An unknown name is a start-up
  refusal: a setting that silently means nothing reads as a broken feature (`LESSONS.md` §3).
- **Never in the way** (FR-6). A watched call behaves exactly as unwatched — exception and return
  value untouched — and an error inside this module is suppressed: it runs on the auth path and
  inside a telemetry exporter.
- **Not carried by what it describes** (§3.2). `otel` lines are local-only and never enter the OTLP
  log pipeline: a line about a failed log export would be queued for export, fail, and produce
  another.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from aira_common.logging import LOCAL_ONLY_KEY, get_logger
from aira_common.observability import redact_target

#: The systems this build can watch, in the order an operator meets them. Closed on purpose.
SYSTEMS: tuple[str, ...] = ("otel", "kafka", "auth", "vault", "redis", "postgres")

#: The one word that means every system, including ones added after the setting was written.
ALL = "all"

#: Systems whose lines must not be exported through OpenTelemetry — for circularity, not
#: sensitivity (see the module docstring).
LOCAL_ONLY_SYSTEMS = frozenset({"otel"})

#: Fields that may hold an address and must pass the shared credential redaction before they are
#: written anywhere (`ADR-0007`): both a query-string key and a `user:password@`.
_URL_FIELDS = frozenset({"target", "endpoint", "url", "uri"})

#: How far down an exception's `__cause__` chain to look for a timeout. Three covers the deepest
#: wrapping seen (`PyJWKClientConnectionError` → `URLError` → `TimeoutError`) and bounds the walk.
_CAUSE_DEPTH = 3

_log = get_logger("aira_common.integration")

_watched: frozenset[str] = frozenset()


class UnknownIntegration(ValueError):
    """``AIRA_DEBUG_INTEGRATIONS`` named something this build cannot watch."""


def parse_systems(spec: str) -> frozenset[str]:
    """Turn the setting's text into the set of systems to watch.

    Empty, whitespace and a lone separator all mean *off* — compose's
    ``${AIRA_DEBUG_INTEGRATIONS:-}`` produces an empty string. An unrecognised name raises.
    """
    names = [part.strip().lower() for part in spec.split(",")]
    wanted = {name for name in names if name}
    if not wanted:
        return frozenset()
    if ALL in wanted:
        return frozenset(SYSTEMS)
    unknown = sorted(wanted - set(SYSTEMS))
    if unknown:
        raise UnknownIntegration(
            f"AIRA_DEBUG_INTEGRATIONS names {', '.join(unknown)}, which this build cannot watch. "
            f"Valid names are {', '.join(SYSTEMS)}, or '{ALL}' for every one of them."
        )
    return frozenset(wanted)


def configure_integration_debug(spec: str) -> frozenset[str]:
    """Set which systems are watched, process-wide. Returns what is now watched.

    Called once per process beside :func:`aira_common.logging.configure_logging`. Process-wide
    rather than passed down: the call sites include a shared library, an exporter's background
    thread and a Django app registry.
    """
    global _watched
    _watched = parse_systems(spec)
    if _watched:
        _log.info("integration_debug_enabled", systems=",".join(sorted(_watched)))
    return _watched


def watched() -> frozenset[str]:
    """The systems currently being watched."""
    return _watched


def is_on(system: str) -> bool:
    """Whether ``system`` is being watched. The whole cost of the channel when it is not."""
    return system in _watched


class Call:
    """The handle a watched call adds detail to. Yielded by :func:`watch`.

    Mutable, because what is worth reporting is often known only *after* the call — a Kafka
    record's partition and offset, whether an exporter answered `SUCCESS`.
    """

    __slots__ = ("_fields", "_live")

    def __init__(self, fields: dict[str, Any], live: bool) -> None:
        self._fields = fields
        self._live = live

    def note(self, **fields: Any) -> None:
        """Add fields to the line this call will emit. A no-op when the channel is off."""
        if self._live:
            self._fields.update(fields)

    def failed(self, detail: str) -> None:
        """Record a failure the callee reported **as a value** rather than by raising.

        An OTLP exporter returns ``FAILURE`` and raises nothing, so a channel watching only
        exceptions would report every failed export as a slow success.
        """
        if self._live:
            self._fields["outcome"] = "failed"
            self._fields["detail"] = detail


#: Yielded when the system is not watched: one object per process, so being ignored allocates
#: nothing.
_IGNORED = Call({}, live=False)


def _is_timeout(exc: BaseException) -> bool:
    """Whether this failure is *"it did not answer"* rather than *"it said no"*.

    A refusal points at configuration (address, port, credential); a timeout at the network. Asked
    three ways, because clients disagree: `TimeoutError` (stdlib and `asyncio`); the type's
    **name**, since httpx, redis-py, aiokafka and urllib3 each define their own; and the **cause
    chain**, since `PyJWKClient` wraps a read timeout in `PyJWKClientConnectionError`.
    """
    seen = exc
    for _ in range(_CAUSE_DEPTH):
        if seen is None:
            return False
        if isinstance(seen, TimeoutError) or "Timeout" in type(seen).__name__:
            return True
        seen = seen.__cause__  # type: ignore[assignment]
    return False


def _clean(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Redact anything that might be a URL. See :data:`_URL_FIELDS`."""
    return {
        key: (redact_target(value) if key in _URL_FIELDS and isinstance(value, str) else value)
        for key, value in fields.items()
    }


def report(system: str, operation: str, **fields: Any) -> None:
    """Write one line for ``system``, if it is watched. The channel's only output.

    Not named `emit`: that is this codebase's word for publishing a configuration event, and two
    guards read the source for `emit("…")`. Suppresses everything it could raise (FR-6).
    """
    if system not in _watched:
        return
    try:
        outcome = fields.pop("outcome", "ok")
        line = {
            "system": system,
            "operation": operation,
            "outcome": outcome,
            **_clean(fields),
        }
        if system in LOCAL_ONLY_SYSTEMS:
            line[LOCAL_ONLY_KEY] = True
        if outcome == "ok":
            _log.info("integration_call", **line)
        else:
            _log.warning("integration_call", **line)
    except Exception:  # noqa: BLE001 — never the reason a watched call fails
        return


@contextmanager
def watch(system: str, operation: str, **fields: Any) -> Iterator[Call]:
    """Time one call to ``system`` and emit a line saying how it went.

    Emits whether the call returned or raised, so *"there is no line"* means *"the call was never
    made"* — a different problem from one that failed. The exception is re-raised untouched (FR-6).
    """
    if system not in _watched:
        yield _IGNORED
        return
    collected = dict(fields)
    call = Call(collected, live=True)
    started = time.perf_counter()
    try:
        yield call
    except BaseException as exc:
        collected["outcome"] = "timeout" if _is_timeout(exc) else "failed"
        collected["error_type"] = type(exc).__name__
        # Bounded: a driver may embed a whole response body in its message.
        collected["error"] = str(exc)[:200]
        report(system, operation, duration_ms=_elapsed_ms(started), **collected)
        raise
    report(system, operation, duration_ms=_elapsed_ms(started), **collected)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
