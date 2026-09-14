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
from dataclasses import dataclass
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


class SentinelConfigError(ValueError):
    """A Sentinel setting that cannot be honoured. Raised at start-up, never at request time."""


@dataclass(frozen=True, slots=True)
class SentinelConfig:
    """Where to find the leader through Redis Sentinel: the sentinels, the name they know the
    leader by, and the credentials. The passwords are secrets and come from Vault."""

    sentinels: tuple[tuple[str, int], ...]
    service: str
    username: str = ""
    password: str = ""
    sentinel_password: str = ""
    db: int = 0

    def target(self) -> str:
        """For a log line: the sentinels and the service, never a password."""
        hosts = ",".join(f"{host}:{port}" for host, port in self.sentinels)
        return f"sentinel://{hosts}/{self.service}"


def parse_sentinels(raw: str) -> tuple[tuple[str, int], ...]:
    """``host:port,host:port`` as addresses; an entry that is not one is refused, not skipped."""
    found: list[tuple[str, int]] = []
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        host, sep, port = entry.rpartition(":")
        if not sep or not host or not port.isdigit() or not 0 < int(port) < 65536:
            raise SentinelConfigError(
                f"'{entry}' is not a sentinel address: host:port, such as 'redis-a:26379'."
            )
        found.append((host, int(port)))
    return tuple(found)


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
        url: str = "",
        *,
        sentinel: SentinelConfig | None = None,
        connect_timeout: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._sentinel = sentinel
        #: What a log line names: the URL, or the sentinels and the service — never a password.
        self._target = sentinel.target() if sentinel is not None else url
        self._connect_timeout = connect_timeout
        # Injectable so the breaker's *reopening* can be tested, not only its closing.
        self._clock = clock
        self._client: Any | None = None
        self._scripts: dict[str, Any] = {}
        self._unavailable_until = 0.0

    def _connect(self) -> Any:
        if self._client is None:
            timeout = self._connect_timeout
            if self._sentinel is not None:
                from redis.asyncio.sentinel import Sentinel  # lazily, like `Redis` below

                config = self._sentinel
                sentinels = Sentinel(  # type: ignore[no-untyped-call]  # redis-py leaves it untyped
                    list(config.sentinels),
                    sentinel_kwargs={
                        "socket_connect_timeout": timeout,
                        "socket_timeout": timeout,
                        "password": config.sentinel_password or None,
                    },
                    socket_connect_timeout=timeout,
                    socket_timeout=timeout,
                )
                # The leader as the sentinels name it now. After a failover the pool's next
                # connection asks them again, so the client follows the leader to its new server.
                self._client = sentinels.master_for(
                    config.service,
                    username=config.username or None,
                    password=config.password or None,
                    db=config.db,
                    decode_responses=True,
                    socket_connect_timeout=timeout,
                    socket_timeout=timeout,
                )
            else:
                from redis.asyncio import Redis  # imported lazily so the dep stays optional

                self._client = Redis.from_url(
                    self._url,
                    socket_connect_timeout=timeout,
                    socket_timeout=timeout,
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
            with watch("redis", "script", target=self._target, keys=len(keys)):
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


def build_runner(url: str, sentinel: SentinelConfig | None = None) -> ScriptRunner:
    """A runner through ``sentinel`` when one is configured, else ``url``, else a disabled one."""
    if sentinel is not None:
        return RedisRunner(sentinel=sentinel)
    if not url.strip():
        return DisabledRunner()
    return RedisRunner(url)
