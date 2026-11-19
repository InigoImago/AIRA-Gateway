"""Access tokens for a cloud platform: cached, refreshed ahead of expiry, single-flighted.

`ADR-0011` rule 1: every platform needs the *same* behaviour and differs only in how a token is
obtained, so the behaviour lives here once and the acquisition is a hook:

    GoogleServiceAccountTokenSource   Vertex        (signed JWT → OAuth2 exchange)
    EntraTokenSource                  Foundry       (FRD-120)
    StaticTokenSource                 dev and tests

- **Refresh ahead of expiry, not on it**, so no request pays the round trip and load does not make
  many requests discover the expiry at the same moment.
- **Refresh behind the requests.** While the held token is valid it is what every request gets;
  its successor is fetched in the background. An identity provider that is slow to answer — or
  does not answer within its whole time limit — holds up no request while there is a token to
  send, and only a request that has none waits for one.
- **Serve through a failed refresh.** A refresh that fails while the held token is still valid is
  not an outage.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from aira_common.logging import get_logger

#: Fraction of a token's lifetime after which it is refreshed. Below 1.0 by definition — the point
#: is to replace the token *before* anyone needs it to still be valid.
REFRESH_AT = 0.8

#: How long to wait before retrying a failed refresh while the current token is still usable.
RETRY_AFTER_SECONDS = 30.0

_log = get_logger("aira_common.tokens")


class TokenUnavailable(Exception):
    """No usable token: none was ever obtained, or the held one expired and refresh failed."""


@dataclass(frozen=True, slots=True)
class AccessToken:
    value: str
    #: Monotonic deadline, not a wall-clock time — a clock step must not expire a live token.
    expires_at: float

    def refresh_due(self, now: float, issued_at: float) -> bool:
        lifetime = self.expires_at - issued_at
        return lifetime <= 0 or now >= issued_at + lifetime * REFRESH_AT

    def usable(self, now: float) -> bool:
        return now < self.expires_at


class TokenAcquirer(Protocol):
    """Obtains a fresh token. The only part that differs per platform."""

    async def acquire(self, now: float) -> AccessToken: ...


class TokenSource:
    """Holds one token for one credential, shared by every adapter using it."""

    def __init__(
        self,
        acquirer: TokenAcquirer,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._acquirer = acquirer
        # Injectable, so "refreshes before expiry" is testable without waiting an hour.
        self._clock = clock
        self._token: AccessToken | None = None
        self._issued_at = 0.0
        self._retry_not_before = 0.0
        #: The fetch in flight, whoever started it. **Single-flight**: every caller that needs a
        #: fetch shares this one and its outcome — a failure included, so callers queued behind a
        #: failing fetch do not each make their own, one after another, each paying the identity
        #: provider's whole time limit.
        self._fetch: asyncio.Task[AccessToken | Exception] | None = None

    async def token(self) -> str:
        now = self._clock()
        held = self._token
        if held is not None and held.usable(now):
            # A failed refresh backs off, but only while the held token still works; retrying on
            # every request would turn a struggling identity provider into a busy loop.
            if held.refresh_due(now, self._issued_at) and now >= self._retry_not_before:
                self._fetching()
            return held.value
        # Nothing usable is held: this caller needs the fetch — the one in flight, if any. Shielded,
        # so a caller that leaves does not cancel the fetch every other caller is waiting for.
        fetched = await asyncio.shield(self._fetching())
        if isinstance(fetched, Exception):
            raise TokenUnavailable(f"Could not obtain an access token: {fetched}") from fetched
        return fetched.value

    def _fetching(self) -> asyncio.Task[AccessToken | Exception]:
        """The fetch in flight, or a new one."""
        loop = asyncio.get_running_loop()
        if self._fetch is None or self._fetch.done() or self._fetch.get_loop() is not loop:
            self._fetch = loop.create_task(self._fetched())
        return self._fetch

    async def _fetched(self) -> AccessToken | Exception:
        """One fetch, its outcome kept: the new token, or why there is none. Returned rather than
        raised, so a refresh nobody waits for leaves no exception unread."""
        now = self._clock()
        try:
            fresh = await self._acquirer.acquire(now)
        except Exception as exc:  # noqa: BLE001 — every failure is the outcome its waiters share
            self._retry_not_before = self._clock() + RETRY_AFTER_SECONDS
            held = self._token
            if held is not None and held.usable(self._clock()):
                # Said here, because no request sees it: the held token carries them meanwhile.
                _log.warning(
                    "access_token_refresh_failed", error_type=type(exc).__name__, error=str(exc)
                )
            return exc
        self._token = fresh
        self._issued_at = now
        self._retry_not_before = 0.0
        return fresh


class StaticTokenSource(TokenSource):
    """A fixed token that never expires. For development and for tests of everything else."""

    def __init__(self, value: str) -> None:
        super().__init__(_Static(value))


@dataclass(frozen=True, slots=True)
class _Static:
    value: str

    async def acquire(self, now: float) -> AccessToken:
        return AccessToken(self.value, expires_at=float("inf"))


@dataclass(frozen=True, slots=True)
class CallableAcquirer:
    """Adapts a coroutine returning ``(token, lifetime_seconds)`` to the protocol."""

    fetch: Callable[[], Awaitable[tuple[str, float]]]

    async def acquire(self, now: float) -> AccessToken:
        value, lifetime = await self.fetch()
        return AccessToken(value, expires_at=now + lifetime)
