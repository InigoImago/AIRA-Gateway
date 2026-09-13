"""Is the upstream reachable, and how do we ask without becoming the problem (FRD-117 §5.2).

Probing every model on every readiness check would be a continuous stream of billable calls and
would make readiness as slow as the slowest upstream — evicting healthy pods. So a background
prober runs on an interval and `/readyz` reads the *last verdict*. The rules that follow:

- **The probe never generates.** An adapter answers cheaply through ``ping``; a generation costs
  money and would wake a scaled-to-zero self-deployed model (`ADR-0012` §5).
- **No ``ping`` is reported as unprobed**, never healthy — "we did not look" is not "it is fine".
- **A stale verdict is reported as stale**, never healthy: a dead prober must be visible.
- **Unreachable is degraded, not down.** It feeds the `DegradationLog` (`FRD-405`), one vocabulary
  for "something is broken and we are still serving".
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

from aira_common.counters import DegradationLog
from aira_common.logging import get_logger
from aira_gateway.upstreams.base import ProviderRegistry

#: How often the background prober runs: far more often than the answer changes, far less often
#: than a readiness probe asks.
DEFAULT_INTERVAL_SECONDS = 60.0

#: How long a verdict is trusted before it is reported **stale**: longer than the interval so one
#: slow round does not flap, short enough that a dead prober shows within a couple of cycles.
DEFAULT_STALE_AFTER_SECONDS = 180.0

#: The probe's own timeout. Short: an upstream that takes ten seconds to say "here" has answered.
DEFAULT_TIMEOUT_SECONDS = 5.0

#: The feature name in the shared degradation log.
FEATURE = "upstream reachability"

_log = get_logger("aira_gateway.diagnostics")


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the last probe found for one provider, and when."""

    provider: str
    ok: bool
    detail: str
    at: float
    #: Whether a remote question was actually asked; an adapter with no cheap call is *unprobed*.
    probed: bool = True
    #: How long the question took, as a number (KIRA's `time_taken`). `None` where nothing was
    #: asked — 0.0 would make "we did not look" read as "it answered instantly".
    took_seconds: float | None = None

    def as_dict(self, now: float, stale_after: float) -> dict[str, object]:
        age = max(0.0, now - self.at)
        return {
            "ok": self.ok,
            "took_seconds": self.took_seconds,
            "probed": self.probed,
            "detail": self.detail,
            "age_seconds": round(age, 1),
            # Reported, not left to a reader who cannot see the interval.
            "stale": age > stale_after,
        }


@dataclass
class UpstreamProbe:
    """A background prober whose verdicts `/readyz` reads.

    ``clock`` and the interval are injectable so staleness — a property about the passage of
    time — can be tested without waiting.
    """

    registry: ProviderRegistry
    degradation: DegradationLog
    interval: float = DEFAULT_INTERVAL_SECONDS
    stale_after: float = DEFAULT_STALE_AFTER_SECONDS
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    clock: object = time.monotonic
    _verdicts: dict[str, Verdict] = field(default_factory=dict)
    _task: asyncio.Task[None] | None = None

    def _now(self) -> float:
        return float(self.clock())  # type: ignore[operator]

    async def probe_once(self) -> dict[str, Verdict]:
        """Ask every provider the cheapest question there is, concurrently.

        Concurrently, so one slow provider cannot delay the verdict for the others.
        """
        providers = {
            getattr(provider, "probe_name", None) or type(provider).__name__: provider
            for provider in self._each_provider()
        }
        results = await asyncio.gather(
            *[self._ask(name, provider) for name, provider in providers.items()],
            return_exceptions=False,
        )
        self._verdicts = {verdict.provider: verdict for verdict in results}
        self._record_degradation()
        return dict(self._verdicts)

    def _each_provider(self) -> list[object]:
        """Every registered adapter — including one that serves no *configured* model.

        Since cataloguing a model is enough to serve it (`FRD-507` stage B), an adapter can have an
        empty configured list and still carry traffic; it must still be probed.
        """
        return list(self.registry.each())

    async def _ask(self, name: str, provider: object) -> Verdict:
        """One provider, one cheap remote question, one verdict."""
        ping = getattr(provider, "ping", None)
        if ping is None:
            # Said, not assumed: nothing cheap to ask cannot be reported green.
            return Verdict(name, True, "no probe available; not checked", self._now(), probed=False)

        start = self._now()
        try:
            detail = await asyncio.wait_for(ping(), timeout=self.timeout)
        except TimeoutError:
            return Verdict(
                name,
                False,
                f"did not answer within {self.timeout:g}s",
                self._now(),
                took_seconds=round(self._now() - start, 3),
            )
        except Exception as exc:  # noqa: BLE001 — any failure here is "not reachable"
            # Broad on purpose: an escaping exception would kill the background task, and every
            # verdict would quietly go stale rather than red.
            return Verdict(
                name,
                False,
                f"{type(exc).__name__}: {exc}"[:120],
                self._now(),
                took_seconds=round(self._now() - start, 3),
            )

        took = self._now() - start
        return Verdict(
            name,
            True,
            f"{detail or 'reachable'}, {took * 1000:.0f}ms",
            self._now(),
            took_seconds=round(took, 3),
        )

    def _record_degradation(self) -> None:
        unreachable = sorted(v.provider for v in self._verdicts.values() if not v.ok)
        if unreachable:
            self.degradation.degraded(
                FEATURE, f"unreachable: {', '.join(unreachable)} — requests to them will fail"
            )
        else:
            self.degradation.working(FEATURE)

    def snapshot(self) -> dict[str, dict[str, object]]:
        """What `/readyz` reports. Never performs I/O — that is the whole point."""
        now = self._now()
        return {
            name: verdict.as_dict(now, self.stale_after)
            for name, verdict in sorted(self._verdicts.items())
        }

    @property
    def degraded(self) -> bool:
        """True when a provider is unreachable **or** its verdict has gone stale.

        Stale counts: a dead prober leaves its last good verdict behind, describing a minute that
        has long passed.
        """
        now = self._now()
        if not self._verdicts:
            return False  # nothing configured to probe; not a degradation
        return any(
            not verdict.ok or (verdict.probed and (now - verdict.at) > self.stale_after)
            for verdict in self._verdicts.values()
        )

    async def run(self) -> None:
        """The background loop. Never raises out — a dead prober is worse than a slow one."""
        while True:
            try:
                await self.probe_once()
            except Exception as exc:  # noqa: BLE001
                _log.warning("upstream_probe_failed", error=str(exc), error_type=type(exc).__name__)
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
