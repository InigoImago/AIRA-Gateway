"""Access tokens for a cloud platform: cached, refreshed ahead of expiry, single-flighted.

`ADR-0011` rule 1: every platform needs the *same* behaviour and differs only in how a token is
obtained, so the behaviour lives here once and the acquisition is a hook:

    GoogleServiceAccountTokenSource   Vertex        (signed JWT → OAuth2 exchange)
    EntraTokenSource                  Foundry       (FRD-120)
    StaticTokenSource                 dev and tests

- **Refresh ahead of expiry, not on it**, so no request pays the round trip and load does not make
  many requests discover the expiry at the same moment.
- **Serve through a failed refresh.** A refresh that fails while the held token is still valid is
  not an outage.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

#: Fraction of a token's lifetime after which it is refreshed. Below 1.0 by definition — the point
#: is to replace the token *before* anyone needs it to still be valid.
REFRESH_AT = 0.8

#: How long to wait before retrying a failed refresh while the current token is still usable.
RETRY_AFTER_SECONDS = 30.0


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
        self._lock = asyncio.Lock()
        self._retry_not_before = 0.0

    async def token(self) -> str:
        now = self._clock()
        held = self._token
        if held is not None and held.usable(now) and not held.refresh_due(now, self._issued_at):
            return held.value
        return await self._refresh(held)

    async def _refresh(self, held: AccessToken | None) -> str:
        # Single-flight: whoever wins the lock re-checks, so the waiters return the new token
        # rather than each fetching one of their own.
        async with self._lock:
            now = self._clock()
            current = self._token
            if (
                current is not None
                and current.usable(now)
                and not current.refresh_due(now, self._issued_at)
            ):
                return current.value

            # A failed refresh backs off, but only while the held token still works; retrying on
            # every request would turn a struggling identity provider into a busy loop.
            if current is not None and current.usable(now) and now < self._retry_not_before:
                return current.value

            try:
                fresh = await self._acquirer.acquire(now)
            except Exception as exc:
                self._retry_not_before = now + RETRY_AFTER_SECONDS
                if current is not None and current.usable(now):
                    return current.value
                raise TokenUnavailable(f"Could not obtain an access token: {exc}") from exc

            self._token = fresh
            self._issued_at = now
            self._retry_not_before = 0.0
            return fresh.value


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
