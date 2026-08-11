"""Is the upstream reachable, and how do we ask without becoming the problem (FRD-117 §5.2).

This is the design point the FRD spends its space on, so it is worth restating where it is
implemented.

The predecessor's `/health` probes the database **and every registered model** on every call. With
a Kubernetes readiness probe every few seconds across every replica, that is a continuous stream of
upstream calls — billable ones, against a provider quota, answering a question whose answer changes
rarely. Worse, it makes the probe **as slow as the slowest upstream**, so one degraded provider
causes readiness timeouts and evicts pods that were serving perfectly well. A health check that can
take down a healthy service is a liability, not a safeguard.

So the probe runs in the background on an interval and `/readyz` reads the *last verdict*. Three
consequences follow, and each is a rule rather than a detail:

- **The probe never generates.** An adapter that can answer cheaply implements ``ping``; a
  generation would cost money to answer "are you there", and against a self-deployed endpoint it
  would **wake a scaled-to-zero model** (`ADR-0012` §5), turning every health check into a cold
  start.
- **An adapter with no ``ping`` is reported as unprobed, not as healthy.** The first draft of this
  called ``models()`` — which is *local configuration*, evaluated once when the registry is built.
  It cannot fail later and says nothing about the network, so every verdict would have been a
  confident green describing nothing at all. That is worse than no probe, because a green board is
  acted upon.
- **A stale verdict is reported as stale**, never as healthy. "The prober has not run" is itself
  information, and the version that rounds it to "fine" is the one that hides an outage.
- **Unreachable is degraded, not down.** A gateway that still refuses over-budget requests, still
  enforces limits and still serves reporting is not down, and evicting it helps nobody. It feeds
  the `DegradationLog` from `FRD-405`, so there is one vocabulary for "something is broken and we
  are still serving" rather than a second one nobody correlates.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

from aira_common.counters import DegradationLog
from aira_common.logging import get_logger
from aira_gateway.upstreams.base import ProviderRegistry

_log = get_logger("aira_gateway.diagnostics")

#: How often the background prober runs. A minute is far more often than the answer changes and far
#: less often than a readiness probe would ask.
DEFAULT_INTERVAL_SECONDS = 60.0

#: How long a verdict is trusted before it is reported as **stale**. Deliberately longer than the
#: interval so a single slow round does not flap, and short enough that a prober which died is
#: visible within a couple of cycles rather than never.
DEFAULT_STALE_AFTER_SECONDS = 180.0

#: The probe's own timeout. Short on purpose: this asks "are you there", and an upstream that takes
#: ten seconds to answer that has already answered it.
DEFAULT_TIMEOUT_SECONDS = 5.0

#: The feature name in the shared degradation log.
FEATURE = "upstream reachability"


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the last probe found for one provider, and when."""

    provider: str
    ok: bool
    detail: str
    at: float
    #: Whether a remote question was actually asked. An adapter with no cheap call is reported as
    #: *unprobed* rather than as healthy — the distinction the first draft of this module missed.
    probed: bool = True

    def as_dict(self, now: float, stale_after: float) -> dict[str, object]:
        age = max(0.0, now - self.at)
        return {
            "ok": self.ok,
            "probed": self.probed,
            "detail": self.detail,
            "age_seconds": round(age, 1),
            # Reported, not inferred by the reader. A caller that had to compare a timestamp
            # against an interval it cannot see would get it wrong in one direction or the other.
            "stale": age > stale_after,
        }


@dataclass
class UpstreamProbe:
    """A background prober whose verdicts `/readyz` reads.

    ``clock`` and the interval are injectable so staleness can be tested without waiting — a
    property that is only ever exercised at one instant is barely tested at all, and this one is
    about the passage of time.
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

        Concurrently because one slow provider must not delay the verdict for the others — the
        serial version reintroduces "as slow as the slowest upstream" inside the prober, where it
        is merely wasteful rather than fatal, but it is the same mistake.
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

        The second half was missing until 2026-08-10, and it stopped being a hypothetical the day
        cataloguing a model became enough to serve it (`FRD-507` stage B). An adapter whose
        configured list is empty — which a Google AI Studio deployment now normally has — appeared
        in no model's provenance, so this walked past it and `/readyz` said **nothing at all**
        about that upstream. Nothing is the wrong answer: `FRD-117`'s rule is that "we did not
        look" and "it is fine" are different verdicts, and an upstream that is silently not probed
        reads as the first while behaving like neither.
        """
        return list(self.registry.each())

    async def _ask(self, name: str, provider: object) -> Verdict:
        """One provider, one cheap remote question, one verdict."""
        ping = getattr(provider, "ping", None)
        if ping is None:
            # Said, not assumed. An adapter with nothing cheap to ask cannot be reported green on
            # that basis — "we did not look" and "it is fine" are different answers, and only one
            # of them is safe to act on.
            return Verdict(name, True, "no probe available; not checked", self._now(), probed=False)

        start = self._now()
        try:
            detail = await asyncio.wait_for(ping(), timeout=self.timeout)
        except TimeoutError:
            return Verdict(name, False, f"did not answer within {self.timeout:g}s", self._now())
        except Exception as exc:  # noqa: BLE001 — any failure here is "not reachable"
            # Deliberately broad: a probe that let an unexpected exception escape would kill the
            # background task, and every verdict would then quietly go stale rather than red.
            return Verdict(name, False, f"{type(exc).__name__}: {exc}"[:120], self._now())

        elapsed = (self._now() - start) * 1000
        return Verdict(name, True, f"{detail or 'reachable'}, {elapsed:.0f}ms", self._now())

    def _record_degradation(self) -> None:
        unreachable = sorted(v.provider for v in self._verdicts.values() if not v.ok)
        if unreachable:
            self.degradation.degraded(
                FEATURE, f"unreachable: {', '.join(unreachable)} — requests to them will fail"
            )
        else:
            self.degradation.working(FEATURE)

    def snapshot(self) -> dict[str, object]:
        """What `/readyz` reports. Never performs I/O — that is the whole point."""
        now = self._now()
        return {
            name: verdict.as_dict(now, self.stale_after)
            for name, verdict in sorted(self._verdicts.items())
        }

    @property
    def degraded(self) -> bool:
        """True when a provider is unreachable **or** its verdict has gone stale.

        Stale counts, and that is the part worth arguing for: a prober that has died leaves the
        last good verdict behind, and a reader that trusted it would see a green board describing
        a minute that has long passed.
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
