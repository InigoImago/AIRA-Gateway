"""Shared atomic counters, backed by Redis (`ADR-0008`).

Rate-limit buckets and budget reservations must be checked and updated in one indivisible step
across processes, so every operation is a single Lua script — one round trip, no window in which
another instance sees a stale value. This module owns the transport only; the *meaning* of each
counter lives with its feature (``aira_gateway.ratelimit``, ``aira_gateway.budgets``).

Unavailability is a normal state, not an exception to swallow at the call site: callers handle
:class:`CountersUnavailable` by taking their own decided fallback (`FRD-405` §4.3).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from aira_common.integration_debug import watch
from aira_common.logging import get_logger

#: After a failure, stop trying for this long, so a Redis outage does not make every request pay a
#: connection timeout.
RETRY_AFTER_FAILURE_SECONDS = 5.0

#: A script argument. Floats are allowed because a refill rate is one; coercing it to int would
#: round every limit below 60 per minute down to zero.
type ScriptArg = str | int | float

_log = get_logger("aira_common.counters")


class CountersUnavailable(RuntimeError):
    """Redis could not be reached. The caller decides what that means for its feature."""


class DegradationLog:
    """What each feature has most recently experienced from the shared counter store.

    A fallback is invisible unless somebody records it, so every feature reports here, in the same
    words, for the health endpoint to read. The *shape* of each fallback is deliberately not shared
    (`ADR-0008`): rate limiting degrades to a local equivalent, budgets to the store that is already
    authoritative.
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

    Not an error state: an installation may run without Redis, and every caller then takes its
    documented fallback quietly.
    """

    async def run(self, script: str, keys: Sequence[str], args: Sequence[ScriptArg]) -> Any:
        raise CountersUnavailable("No counter store is configured.")

    async def close(self) -> None:
        return None


class RedisRunner:
    """Redis-backed script runner with a small circuit breaker.

    Scripts are cached by SHA on the server and re-uploaded after a Redis restart (redis-py's
    ``Script`` retries on ``NOSCRIPT``), so callers pass source text and pay the upload once.
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
        # Injectable so the breaker's *reopening* can be tested, not only its closing.
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
            # Watched per call, successes included: how slowly Redis answers is the question
            # (`FRD-617` §3.3).
            with watch("redis", "script", target=self._url, keys=len(keys)):
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
