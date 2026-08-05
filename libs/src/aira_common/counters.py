"""Shared atomic counters, backed by Redis (ADR-0008).

The gateway needs counters that several processes agree on and that can be *checked and updated
in one indivisible step*: rate-limit buckets and budget reservations. Doing that with separate
reads and writes is what the counters exist to fix, so every operation here is a single Lua
script — one round trip, no window in which another instance sees a stale value.

This module owns the transport only: connecting, running a script, and being honest about when
Redis is not reachable. The *meaning* of each counter lives with the feature that uses it
(``aira_gateway.ratelimit``, ``aira_gateway.budgets``), the same way ``kafka.py`` carries records
without knowing what they mean.

Unavailability is a normal state, not an exception to be swallowed at the call site: callers ask
for a runner and handle :class:`CountersUnavailable` by taking their own decided fallback. See
``FRD-405 §4.3`` for what each caller does.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from aira_common.logging import get_logger

_log = get_logger("aira_common.counters")

# After a failure, stop trying for this long. Without it every request pays a connection
# timeout while Redis is down — turning a degraded dependency into a slow gateway, which is
# the failure mode the fallbacks exist to avoid.
RETRY_AFTER_FAILURE_SECONDS = 5.0

# Script arguments are numbers or strings; Redis serialises both. Floats are explicit here
# because a refill rate is one, and coercing it to int would silently round every limit below
# 60 per minute down to zero.
type ScriptArg = str | int | float


class CountersUnavailable(RuntimeError):
    """Redis could not be reached. The caller decides what that means for its feature."""


class DegradationLog:
    """What each feature has most recently experienced from the shared counter store.

    Every feature built on these counters needs a decided answer to "and what if it is gone" —
    and every one of those answers is invisible unless somebody records it. Before this, one
    feature set a flag nobody read and another logged a warning, so a gateway could run for a
    week on its fallbacks with the health endpoint reporting nothing but a successful ping.

    The *shape* of the fallback is deliberately not shared. Rate limiting degrades to a local
    equivalent, budgets degrade to the store that is already authoritative, and forcing those
    into one abstraction would hide the reason they differ (see ADR-0008). What is shared is
    that both say so, in one place, in the same words.
    """

    def __init__(self) -> None:
        self._degraded: dict[str, str] = {}

    def working(self, feature: str) -> None:
        """Record that ``feature`` reached the counter store."""
        self._degraded.pop(feature, None)

    def degraded(self, feature: str, detail: str) -> None:
        """Record that ``feature`` fell back, and what that costs while it lasts."""
        self._degraded[feature] = detail

    @property
    def features(self) -> dict[str, str]:
        """Features currently on their fallback, mapped to what that means."""
        return dict(self._degraded)

    def __bool__(self) -> bool:
        return bool(self._degraded)


class ScriptRunner(Protocol):
    """Runs an atomic script against the shared counter store."""

    async def run(self, script: str, keys: Sequence[str], args: Sequence[ScriptArg]) -> Any:
        """Execute ``script`` atomically. Raises :class:`CountersUnavailable` if the store is not
        reachable."""
        ...

    async def close(self) -> None: ...


class DisabledRunner:
    """Used when no Redis is configured. Every call reports unavailability immediately.

    This is not an error state: an installation may deliberately run without Redis, in which case
    every caller takes its documented fallback and nothing here should be noisy about it.
    """

    async def run(self, script: str, keys: Sequence[str], args: Sequence[ScriptArg]) -> Any:
        raise CountersUnavailable("No counter store is configured.")

    async def close(self) -> None:
        return None


class RedisRunner:
    """Redis-backed script runner with a small circuit breaker.

    Scripts are cached by SHA on the server and re-uploaded automatically if Redis was restarted
    (redis-py's ``Script`` handles the ``NOSCRIPT`` retry), so callers pass source text and pay
    the upload once.
    """

    def __init__(
        self,
        url: str,
        *,
        connect_timeout: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._connect_timeout = connect_timeout
        # Injectable so the breaker's *reopening* can be tested and not only its closing. A
        # breaker that never lets go is indistinguishable, in a green suite, from one that does.
        self._clock = clock
        self._client: Any | None = None
        self._scripts: dict[str, Any] = {}
        self._unavailable_until = 0.0

    def _connect(self) -> Any:
        if self._client is None:
            from redis.asyncio import Redis  # imported lazily so the dep stays optional at import

            self._client = Redis.from_url(
                self._url,
                socket_connect_timeout=self._connect_timeout,
                socket_timeout=self._connect_timeout,
                decode_responses=True,
            )
        return self._client

    async def run(self, script: str, keys: Sequence[str], args: Sequence[ScriptArg]) -> Any:
        now = self._clock()
        if now < self._unavailable_until:
            raise CountersUnavailable("Counter store is in a failed state; not retrying yet.")
        try:
            client = self._connect()
            registered = self._scripts.get(script)
            if registered is None:
                registered = client.register_script(script)
                self._scripts[script] = registered
            result = await registered(keys=list(keys), args=list(args))
        except CountersUnavailable:
            raise
        except Exception as exc:  # redis errors, DNS, timeouts — all mean "not usable right now"
            self._unavailable_until = self._clock() + RETRY_AFTER_FAILURE_SECONDS
            _log.warning("counters_unavailable", error=str(exc), error_type=type(exc).__name__)
            raise CountersUnavailable(str(exc)) from exc
        self._unavailable_until = 0.0
        return result

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self._scripts.clear()


def build_runner(url: str) -> ScriptRunner:
    """Return a runner for ``url``, or a disabled one when no URL is configured."""
    if not url.strip():
        return DisabledRunner()
    return RedisRunner(url)
