"""Holding a cloud access token (ADR-0011 rule 1, FRD-115 §5.2).

This behaviour is shared by every platform and differs only in how the token is obtained. Writing
it per platform means getting the refresh race right per platform, and the second one is always
the one that is subtly wrong — so it is written once and tested here, hard.

The clock is injected because "refreshes *before* expiry" is otherwise a property testable only by
waiting an hour.
"""

from __future__ import annotations

import asyncio

import pytest

from aira_common.tokens import (
    AccessToken,
    CallableAcquirer,
    StaticTokenSource,
    TokenSource,
    TokenUnavailable,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Counting:
    """An acquirer that counts fetches and can be made to fail."""

    def __init__(self, lifetime: float = 3600.0) -> None:
        self.calls = 0
        self.fail = False
        self.lifetime = lifetime
        self.gate: asyncio.Event | None = None

    async def acquire(self, now: float) -> AccessToken:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("the identity provider is unwell")
        return AccessToken(f"token-{self.calls}", expires_at=now + self.lifetime)


async def test_a_token_is_fetched_once_and_reused() -> None:
    clock, acquirer = _Clock(), _Counting()
    source = TokenSource(acquirer, clock=clock)

    assert await source.token() == "token-1"
    assert await source.token() == "token-1"
    assert acquirer.calls == 1


async def test_it_refreshes_before_expiry_rather_than_on_it() -> None:
    """Fetching lazily when the token has already expired makes one unlucky request pay a round
    trip — and under load makes many requests discover the expiry at once and all fetch."""
    clock, acquirer = _Clock(), _Counting(lifetime=1000.0)
    source = TokenSource(acquirer, clock=clock)
    await source.token()

    clock.advance(700)  # 70% of the lifetime — not yet due
    assert await source.token() == "token-1"

    clock.advance(150)  # past 80%, and still comfortably valid
    assert await source.token() == "token-2"
    assert acquirer.calls == 2


async def test_concurrent_callers_produce_one_fetch() -> None:
    """The thundering herd this exists to prevent: without single-flight, every request in flight
    at the moment a token becomes due fetches its own."""
    clock, acquirer = _Clock(), _Counting()
    acquirer.gate = asyncio.Event()
    source = TokenSource(acquirer, clock=clock)

    waiters = [asyncio.create_task(source.token()) for _ in range(20)]
    await asyncio.sleep(0)  # let them all reach the acquirer
    acquirer.gate.set()
    results = await asyncio.gather(*waiters)

    assert acquirer.calls == 1, "the herd was not collapsed into one fetch"
    assert set(results) == {"token-1"}


async def test_a_failed_refresh_keeps_serving_the_still_valid_token() -> None:
    """A refresh that fails while the current token is still good is not an outage. Treating it as
    one converts a brief identity-provider hiccup into a total outage of what the token protects."""
    clock, acquirer = _Clock(), _Counting(lifetime=1000.0)
    source = TokenSource(acquirer, clock=clock)
    await source.token()

    acquirer.fail = True
    clock.advance(850)  # refresh is due, and it will fail

    assert await source.token() == "token-1"


async def test_a_failed_refresh_backs_off_instead_of_retrying_every_request() -> None:
    """Otherwise a struggling identity provider gets a busy loop from us on top of its problem."""
    clock, acquirer = _Clock(), _Counting(lifetime=1000.0)
    source = TokenSource(acquirer, clock=clock)
    await source.token()

    acquirer.fail = True
    clock.advance(850)
    for _ in range(10):
        await source.token()

    assert acquirer.calls == 2, "every request retried the failing refresh"


async def test_an_expired_token_with_a_failing_refresh_is_an_error() -> None:
    """The one case that must surface: there is nothing usable left to serve."""
    clock, acquirer = _Clock(), _Counting(lifetime=1000.0)
    source = TokenSource(acquirer, clock=clock)
    await source.token()

    acquirer.fail = True
    clock.advance(2000)  # well past expiry

    with pytest.raises(TokenUnavailable):
        await source.token()


async def test_the_first_acquisition_failing_is_an_error() -> None:
    clock, acquirer = _Clock(), _Counting()
    acquirer.fail = True

    with pytest.raises(TokenUnavailable):
        await TokenSource(acquirer, clock=clock).token()


async def test_a_recovered_provider_is_used_again() -> None:
    clock, acquirer = _Clock(), _Counting(lifetime=1000.0)
    source = TokenSource(acquirer, clock=clock)
    await source.token()

    acquirer.fail = True
    clock.advance(850)
    await source.token()

    acquirer.fail = False
    clock.advance(100)  # past the back-off window
    assert await source.token() == "token-3"


async def test_a_static_token_never_expires() -> None:
    source = StaticTokenSource("dev-token")
    assert await source.token() == "dev-token"
    assert await source.token() == "dev-token"


async def test_the_callable_acquirer_adapts_a_plain_coroutine() -> None:
    async def fetch() -> tuple[str, float]:
        return "from-a-coroutine", 60.0

    assert await TokenSource(CallableAcquirer(fetch)).token() == "from-a-coroutine"
