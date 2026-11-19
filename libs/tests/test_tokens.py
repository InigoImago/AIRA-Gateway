"""Holding a cloud access token (ADR-0011 rule 1, FRD-115 §5.2).

This behaviour is shared by every platform and differs only in how the token is obtained. Writing
it per platform means getting the refresh race right per platform, and the second one is always
the one that is subtly wrong — so it is written once and tested here, hard.

The clock is injected because "refreshes *before* expiry" is otherwise a property testable only by
waiting an hour. A refresh runs **behind** the requests, which keep the held token meanwhile, so a
test lets it run (`_let_the_refresh_run`) before it asks what it brought.
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


async def _let_the_refresh_run() -> None:
    """Hand the event loop to a refresh started in the background, which finishes at once unless
    a test holds it at its gate."""
    for _ in range(3):
        await asyncio.sleep(0)


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

    clock.advance(150)  # past 80%, and still valid: served while its successor is fetched
    assert await source.token() == "token-1"
    await _let_the_refresh_run()

    assert await source.token() == "token-2"
    assert acquirer.calls == 2


async def test_a_due_refresh_holds_up_no_request() -> None:
    """**No request waits for an identity provider while a valid token is held.** The refresh is
    due at 80 % of the lifetime — with an hour's token, twelve minutes before it expires — and ran
    inline: every request arriving while the provider was slow to answer waited for it, and all
    traffic the token carries stood still. The held token is what they get, and its successor is
    fetched once, behind them."""
    clock, acquirer = _Clock(), _Counting(lifetime=1000.0)
    source = TokenSource(acquirer, clock=clock)
    await source.token()

    acquirer.gate = asyncio.Event()  # the identity provider is slow to answer
    clock.advance(850)
    served = []
    for _ in range(20):
        served.append(await asyncio.wait_for(source.token(), timeout=1.0))
        await _let_the_refresh_run()

    assert served == ["token-1"] * 20
    assert acquirer.calls == 2, "the refresh is one fetch, whoever found it due"
    acquirer.gate.set()
    await _let_the_refresh_run()
    assert await source.token() == "token-2"


async def test_a_caller_with_nothing_valid_waits_for_the_refresh_in_flight() -> None:
    """Only a caller with no usable token waits — and for the fetch already running, not one of its
    own."""
    clock, acquirer = _Clock(), _Counting(lifetime=1000.0)
    source = TokenSource(acquirer, clock=clock)
    await source.token()
    acquirer.gate = asyncio.Event()
    clock.advance(850)
    # The refresh begins, and waits at the gate; the caller who found it due does not.
    assert await asyncio.wait_for(source.token(), timeout=1.0) == "token-1"

    clock.advance(500)  # the held token expires meanwhile
    waiting = asyncio.create_task(source.token())
    await _let_the_refresh_run()
    assert not waiting.done(), "a caller with no valid token was answered without one"
    acquirer.gate.set()

    assert await waiting == "token-2"
    assert acquirer.calls == 2


async def test_a_caller_who_leaves_does_not_cancel_the_fetch_others_wait_for() -> None:
    """A caller that hangs up is cancelled where it waits. Were that the fetch, every other caller
    waiting for the same token would have to fetch again, each paying the provider's time once
    more."""
    clock, acquirer = _Clock(), _Counting()
    acquirer.gate = asyncio.Event()
    source = TokenSource(acquirer, clock=clock)

    leaving = asyncio.create_task(source.token())
    staying = asyncio.create_task(source.token())
    await _let_the_refresh_run()
    leaving.cancel()
    await asyncio.gather(leaving, return_exceptions=True)
    acquirer.gate.set()

    assert await staying == "token-1"
    assert acquirer.calls == 1


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


async def test_concurrent_callers_share_one_failed_fetch() -> None:
    """Single-flight holds for a failure too. With no token to fall back on, every caller queued
    behind a failing fetch made its own, one after another — each paying the identity provider's
    full timeout, so the k-th waited k times as long for the same refusal."""
    clock, acquirer = _Clock(), _Counting()
    acquirer.gate = asyncio.Event()
    acquirer.fail = True
    source = TokenSource(acquirer, clock=clock)

    waiters = [asyncio.create_task(source.token()) for _ in range(20)]
    await asyncio.sleep(0)  # all twenty are waiting: one in the fetch, the rest on the lock
    acquirer.gate.set()
    results = await asyncio.gather(*waiters, return_exceptions=True)

    assert acquirer.calls == 1, "each queued caller fetched again after the first fetch failed"
    assert all(isinstance(result, TokenUnavailable) for result in results)


async def test_a_caller_after_a_shared_failure_tries_again() -> None:
    """Sharing a failure is not a back-off: a caller arriving once it is over asks afresh, so an
    identity provider that has recovered is used at once, even with no token held."""
    clock, acquirer = _Clock(), _Counting()
    acquirer.fail = True
    source = TokenSource(acquirer, clock=clock)
    with pytest.raises(TokenUnavailable):
        await source.token()

    acquirer.fail = False
    assert await source.token() == "token-2"


async def test_only_the_fetch_a_caller_waited_for_is_its_answer() -> None:
    """A failure from before — followed by a fetch that worked — is not handed to a caller whose
    wait ended in that success, even when the token it brought is already due again: that caller is
    answered with the token, still valid, rather than with an outage that is over — and the next
    request renews it behind the requests."""
    clock, acquirer = _Clock(), _Counting(lifetime=1000.0)
    source = TokenSource(acquirer, clock=clock)
    acquirer.fail = True
    with pytest.raises(TokenUnavailable):
        await source.token()

    acquirer.fail = False
    acquirer.gate = asyncio.Event()
    first = asyncio.create_task(source.token())
    await asyncio.sleep(0)  # the first caller is inside the fetch
    second = asyncio.create_task(source.token())
    await asyncio.sleep(0)  # the second waits on the lock
    clock.advance(900)  # a slow fetch: its token is due for refreshing by the time it lands
    acquirer.gate.set()

    assert await first == "token-2"
    assert await second == "token-2"
    assert await source.token() == "token-2"  # due, so its successor is fetched meanwhile
    await _let_the_refresh_run()
    assert await source.token() == "token-3"


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
        await _let_the_refresh_run()

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
    await _let_the_refresh_run()

    acquirer.fail = False
    clock.advance(100)  # past the back-off window
    assert await source.token() == "token-1"  # still valid: served while the provider is asked
    await _let_the_refresh_run()
    assert await source.token() == "token-3"


async def test_a_static_token_never_expires() -> None:
    source = StaticTokenSource("dev-token")
    assert await source.token() == "dev-token"
    assert await source.token() == "dev-token"


async def test_the_callable_acquirer_adapts_a_plain_coroutine() -> None:
    async def fetch() -> tuple[str, float]:
        return "from-a-coroutine", 60.0

    assert await TokenSource(CallableAcquirer(fetch)).token() == "from-a-coroutine"
