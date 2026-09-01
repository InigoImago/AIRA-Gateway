"""A hung identity provider stalls one request, not the worker (`FRD-617` §3.4).

`OidcValidator.validate` verifies an RS256 signature and, on a cold start or after a key rotation,
fetches the JWKS — and `PyJWKClient` fetches with `urllib`, which is **synchronous**. Called
directly from `resolve_principal`, which is an `async` dependency, that fetch ran on the event
loop: a Keycloak that accepts connections and does not answer therefore froze *every* concurrent
request on the worker for the length of it, including the ones authenticating with an API key and
the ones asking `/readyz`. With PyJWT's default timeout — nothing was passed — the length of it
was thirty seconds.

Two halves, and this file is about the one a shorter timeout cannot substitute for. It measures
concurrency rather than asserting that `asyncio.to_thread` appears in the source, because the
second would pass against a `to_thread` that is awaited before the work it was meant to move.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

from aira_gateway.auth.dependencies import resolve_principal
from aira_gateway.auth.principal import Principal

#: Long enough that a serialised run is unmistakable, short enough not to slow the suite.
BLOCK_SECONDS = 0.3


class SleepingValidator:
    """Stands in for a JWKS fetch against a provider that accepts and does not answer."""

    def validate(self, token: str) -> Principal:
        time.sleep(BLOCK_SECONDS)
        return Principal(subject="s-1", method="oidc")


def _request() -> Any:
    """The three things `resolve_principal` reads before it reaches the validator."""
    return SimpleNamespace(
        headers={"authorization": "Bearer not.an.aira.key"},
        query_params={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(auth_required=True),
                oidc_validator=SleepingValidator(),
            )
        ),
    )


async def test_a_hung_provider_does_not_stop_the_loop_serving_anything_else() -> None:
    ticks = 0

    async def other_work() -> None:
        nonlocal ticks
        deadline = time.monotonic() + BLOCK_SECONDS
        while time.monotonic() < deadline:
            ticks += 1
            await asyncio.sleep(0.005)

    principal, _ = await asyncio.gather(resolve_principal(_request()), other_work())

    assert principal is not None
    assert ticks > 5, (
        "the event loop made no progress while a token was being validated: the JWKS fetch is "
        "back on the loop (FRD-617 §3.4)"
    )


async def test_two_stalled_validations_overlap_instead_of_queueing() -> None:
    started = time.monotonic()
    results = await asyncio.gather(*(resolve_principal(_request()) for _ in range(3)))
    elapsed = time.monotonic() - started

    assert all(result is not None for result in results)
    assert elapsed < BLOCK_SECONDS * 2, (
        f"three validations took {elapsed:.2f}s; serialised on the loop they would take "
        f"{BLOCK_SECONDS * 3:.2f}s"
    )
