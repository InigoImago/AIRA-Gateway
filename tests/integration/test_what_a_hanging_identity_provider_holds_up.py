"""An identity provider that hangs holds up only the callers whose tokens it must be asked about.

Down, Keycloak fails fast. Hanging — accepting a connection and never answering — it held up far
more than the requests that needed it. With no key set to hand, every validation fetched the set
itself, and PyJWT holds a lock across the fetch, so the *n*-th caller waited *n* timeouts — on the
event loop's default executor, which the audit writer and readiness's name resolution share.
Readiness answered `503` over a dependency it does not even check.

Now validation runs on threads of its own, one fetch runs at a time and nobody queues behind it
for longer than its timeout, a failed fetch is retried after a backoff rather than by every
request, and readiness resolves names on threads of its own. The hermetic suite proves each with a
validator that blocks. This makes the real case: the stack's Keycloak is paused, which is what
hanging looks like from outside, and the gateway is restarted under it, so it holds no key set at
all — what a deploy during an identity-provider outage meets.
"""

from __future__ import annotations

import asyncio
import subprocess
import time

import httpx
import pytest
import stack_addresses

from aira_common.oidc import DEFAULT_JWKS_TIMEOUT_SECONDS, JWKS_RETRY_MAX_SECONDS

from .conftest import GATEWAY_URL

pytestmark = pytest.mark.integration

KEYCLOAK = stack_addresses.container("keycloak")
GATEWAY = stack_addresses.container("gateway")
#: Requests carrying a token while no key set can be had: more than any runner's default executor
#: has threads, which is what they filled.
TOKENS = 24
#: Long enough for the probes below to meet the validations in flight.
HEAD_START = 0.5
#: What a token's caller may wait while the set cannot be had: the one fetch's timeout, and margin
#: for a loaded runner. Queued behind each other, the twenty-fourth waited twenty-four of them.
TOKEN_ANSWERED_WITHIN = DEFAULT_JWKS_TIMEOUT_SECONDS + 5.0
#: What a request that needs no identity provider may take meanwhile.
UNHELD = 3.0
#: How long the gateway may take to start, and to accept a token again once Keycloak answers: the
#: longest backoff after a failed fetch, and margin.
STARTED_WITHIN = 120.0
RECOVERED_WITHIN = JWKS_RETRY_MAX_SECONDS + 60.0


def _container(action: str, name: str) -> None:
    done = subprocess.run(  # noqa: S603 - fixed argv
        ["docker", action, name], capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, f"docker {action} {name} failed: {done.stderr.strip()}"


async def _until(check, timeout: float, what: str) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    last = ""
    while asyncio.get_running_loop().time() < deadline:
        try:
            if await check():
                return
        # A gateway that is still starting refuses connections, then answers; both mean "not yet".
        except Exception as exc:  # noqa: BLE001
            last = repr(exc)
        await asyncio.sleep(1.0)
    raise AssertionError(f"{what} within {timeout:.0f}s: {last}")


async def _timed(request) -> tuple[int, float]:
    started = time.monotonic()
    response = await request
    return response.status_code, time.monotonic() - started


async def test_while_keycloak_hangs_a_gateway_holding_no_keys_answers_every_caller_at_once(
    fixture, governance_token: str
) -> None:
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=180.0) as client:

        def traces(token: str):
            return client.get(
                "/v1beta/traces",
                params={"use_case": fixture.slug, "limit": 1},
                headers={"Authorization": f"Bearer {token}"},
            )

        async def live() -> bool:
            return (await client.get("/healthz")).status_code == 200

        async def accepted() -> bool:
            return (await traces(governance_token)).status_code == 200

        _container("pause", KEYCLOAK)
        try:
            _container("restart", GATEWAY)
            await _until(live, STARTED_WITHIN, "the gateway did not start")

            validating = [
                asyncio.ensure_future(_timed(traces(governance_token))) for _ in range(TOKENS)
            ]
            await asyncio.sleep(HEAD_START)
            ready, served = await asyncio.gather(
                _timed(client.get("/readyz")),
                _timed(
                    client.post(
                        "/v1beta/models/mock-1:generateContent",
                        headers=fixture.headers(),
                        json={"contents": [{"parts": [{"text": "hi"}]}]},
                    )
                ),
            )
            answered = await asyncio.gather(*validating)
        finally:
            _container("unpause", KEYCLOAK)
        await _until(accepted, RECOVERED_WITHIN, "a token was not accepted again")

    assert ready[0] == 200, f"readiness answered {ready[0]} while the identity provider hung"
    assert ready[1] < UNHELD, f"readiness waited {ready[1]:.1f}s on an identity provider"
    assert served[0] == 200, f"a key's request answered {served[0]}"
    assert served[1] < UNHELD, f"a key's request waited {served[1]:.1f}s on an identity provider"
    slowest = max(took for _, took in answered)
    assert slowest < TOKEN_ANSWERED_WITHIN, (
        f"a token's caller waited {slowest:.1f}s for a key set nobody could fetch"
    )
    assert {status for status, _ in answered} == {401}, answered
