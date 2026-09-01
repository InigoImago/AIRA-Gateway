"""One line per call to a system that is not ours (`FRD-617`).

Everything else this codebase says about itself is about a request it **received**: a server span,
an audit row, a degradation entry. Integration work needs the other direction — *what did we send,
where, how long did it take, and did it arrive* — and that half existed nowhere. A collector on the
wrong port, a broker that refuses our SASL mechanism and an identity provider that has gone away
all presented as ordinary silence, or as somebody else's fault.

## What this is

`watch(system, operation, **fields)` wraps a call, times it, and emits exactly **one** structured
line carrying the system, the operation, the outcome, the duration and whatever the caller thought
was worth naming. That is the whole feature. It is not a metrics facility (the trace and the
collector's own counters are that), and it is not a slow query log — see `FRD-617` §3.3 for why
`postgres` and `redis` are watched at connection granularity and not per statement.

## Off is off, and on is one switch

`AIRA_DEBUG_INTEGRATIONS` selects systems by name from :data:`SYSTEMS`. Empty is the default.
Disabled, a call site costs one frozenset membership test and yields a shared no-op — nothing is
formatted, timed, or allocated per field.

There is deliberately **no second knob**. The lines go out at `INFO` (`WARNING` for a failure)
rather than `DEBUG`, because a debug channel that also requires `AIRA_LOG_LEVEL=DEBUG` is one whose
first use is spent discovering that it needs `AIRA_LOG_LEVEL=DEBUG`, and lowering the root level to
see six lines buys every library's opinion as well.

The vocabulary is closed and an unknown name is a **startup refusal**. `LESSONS.md` §3: a setting
that silently means nothing is worse than one that is missing, because the operator concludes the
feature does not work rather than that they misspelled it.

## Never in the way

`FR-6`: a watched call behaves exactly as it would unwatched. The exception propagates untouched,
the return value is the callee's, and an error *inside* this module is suppressed — a diagnostic
channel that can fail the thing it observes is a liability, and this one runs on the request path
(`auth`) and inside a telemetry exporter, which are the two places a raised exception is hardest to
attribute.

## And it must not travel by the thing it describes

Lines about `otel` are marked local-only and never enter the OTLP log pipeline. `FRD-617` §3.2 has
the mechanism and the reason: a line reporting a failed log export becomes a log record, which is
queued for export, which fails, which produces another line. The same trap ate the SDK's *own*
explanation of every export failure this project has ever had — with `AIRA_OTEL_ENABLED=true` the
only handler on the root logger is the OTLP one, so the sentence saying why the exporter could not
post was posted to the exporter that could not post.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from aira_common.logging import LOCAL_ONLY_KEY, get_logger
from aira_common.observability import redact_target

_log = get_logger("aira_common.integration")

#: The systems this build knows how to watch. Closed on purpose — see the module docstring.
#:
#: Ordered as an operator meets them: the two that carry telemetry and configuration, the one that
#: decides who a caller is, and the three that hold state.
SYSTEMS: tuple[str, ...] = ("otel", "kafka", "auth", "vault", "redis", "postgres")

#: The one word that means every system, including ones added after the setting was written.
ALL = "all"

#: Systems whose lines must not be exported through OpenTelemetry. `otel` is the only one that can
#: be, and the reason is circularity rather than sensitivity — see the module docstring.
LOCAL_ONLY_SYSTEMS = frozenset({"otel"})

#: Fields whose value may be an address and must therefore pass the shared credential redaction
#: before it is written anywhere (`ADR-0007`). `redact_target` is both halves of that: the query
#: parameter a Gemini key rides in, and the `user:password@` a Redis or Postgres URL carries.
_URL_FIELDS = frozenset({"target", "endpoint", "url", "uri"})

_watched: frozenset[str] = frozenset()


class UnknownIntegration(ValueError):
    """``AIRA_DEBUG_INTEGRATIONS`` named something this build cannot watch."""


def parse_systems(spec: str) -> frozenset[str]:
    """Turn the setting's text into the set of systems to watch.

    Empty, whitespace and a lone separator all mean *off*, because a compose file writing
    ``${AIRA_DEBUG_INTEGRATIONS:-}`` produces an empty string and that is the ordinary way this
    setting is absent (the same idiom `BaseAiraSettings._empty_means_unset` exists for).

    An unrecognised name raises. The alternative — ignore it — produces an installation where the
    operator has switched the feature on, sees nothing, and concludes the feature is broken.
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

    Called once per process next to :func:`aira_common.logging.configure_logging`. Process-wide
    rather than passed down, for the same reason logging is: the call sites are in a shared
    library, an exporter's background thread and a Django app registry, and threading a settings
    object to all three would mean the ones that could not reach it stay unwatched.
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

    Mutable on purpose: what is worth reporting is often only known *after* the call — the
    partition and offset a Kafka record landed on, how many spans a batch held, whether an
    exporter answered `SUCCESS` or `FAILURE`. A caller that had to know its own outcome in advance
    would report the ones that go well and nothing else.
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

        The case this exists for is an OTLP exporter: it returns ``SpanExportResult.FAILURE`` and
        raises nothing at all, so a channel that only watched exceptions would report every
        unsuccessful export as a success that took a while.
        """
        if self._live:
            self._fields["outcome"] = "failed"
            self._fields["detail"] = detail


#: Yielded when the system is not watched. One object for the whole process: a call site must not
#: allocate to be ignored.
_IGNORED = Call({}, live=False)


#: How far down an exception's `__cause__` chain to look. Three is deep enough for every wrapping
#: seen here — `PyJWKClientConnectionError` → `URLError` → `TimeoutError` is the longest — and
#: bounded so a client library with a cyclic or very deep chain cannot turn a log line into a walk.
_CAUSE_DEPTH = 3


def _is_timeout(exc: BaseException) -> bool:
    """Whether this failure is *"it did not answer"* rather than *"it said no"*.

    The distinction is not cosmetic. A refusal means a wrong address, a wrong port or a wrong
    credential and is answered by changing configuration; a timeout means a firewall, a hung
    process or a route that goes nowhere and is answered by looking at the network. Collapsing the
    two sends whoever reads the line to the wrong one of those about half the time.

    Three ways of asking, because clients disagree about all three:

    - `TimeoutError`, which covers the stdlib and `asyncio` (one class since 3.11);
    - **the name**, because httpx, redis-py, aiokafka and urllib3 each define their own type, and
      importing four client libraries into a shared module so a classification can be exact is a
      worse trade than matching a name they all agree on;
    - **the cause chain**, because a library may wrap it in a type that says neither. Measured:
      `PyJWKClient` raises `PyJWKClientConnectionError` for a refused connection *and* for a read
      timeout, with the real answer one `__cause__` down — so without this every identity-provider
      failure read as `failed`, and the field that is supposed to separate "the port is wrong" from
      "something is swallowing the packets" separated nothing on the one system where that question
      is asked per request.
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

    Named `report` and not `emit`: `emit` is this codebase's word for *publishing a configuration
    event to the outbox*, and two guards read the source for `emit("…")` to check that every event
    Management publishes has a topic and a branch in the gateway's consumer. A second `emit` with
    an unrelated first argument made both of them fail on `postgres` — which is the guards working,
    and a name worth changing rather than an exclusion worth adding.

    Suppresses everything it could raise, for the reason in the module docstring: this runs inside
    an exporter's background thread and on the authentication path, and a diagnostic that can take
    down what it observes is worse than no diagnostic.
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

    Emits on the way out whether the call returned or raised, which is the property that makes the
    channel usable: *"there is no line"* then means *"the call was never made"*, and that is a
    different problem from one that failed. A version that only reported failures would leave the
    two indistinguishable, which is the state `tools/lab_status.py` was written to escape one layer
    further out.

    The exception is re-raised untouched (`FR-6`).
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
        # Bounded: a driver that embeds a whole response body in its message would otherwise put it
        # in the log, and the first two hundred characters of any of these carries the reason.
        collected["error"] = str(exc)[:200]
        report(system, operation, duration_ms=_elapsed_ms(started), **collected)
        raise
    report(system, operation, duration_ms=_elapsed_ms(started), **collected)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
