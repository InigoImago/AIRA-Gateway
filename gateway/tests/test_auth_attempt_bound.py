"""A bound on authentication *failures* (2026-08-08).

`FRD-405` gave the gateway rate limits and every one of them is keyed by use case or by member —
which means each needs a *verified* identity and none of them can bound a caller who has none. An
unauthenticated address could probe credentials indefinitely, each attempt a database round trip,
and never meet a limit. The body ceiling bounds one request's size; nothing bounded their number.

The property that makes it safe to set low: **it counts refusals**. A caller with a working
credential never touches this bucket, so a busy legitimate integration cannot be throttled by it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings

BODY = {"contents": [{"parts": [{"text": "hi"}]}]}
PATH = "/v1beta/models/mock-1:generateContent"


def _client(**overrides: object) -> TestClient:
    # No shared counter store: the bound must be a property of *this* app, not of whatever a
    # previous run left in a Redis that happens to be up on this machine.
    values: dict[str, object] = {"auth_required": True, "test_database": True, "redis_url": ""}
    values.update(overrides)
    return TestClient(create_app(GatewaySettings(**values)))  # type: ignore[arg-type]


def test_a_few_failures_are_just_failures() -> None:
    """A typo in a key is a 401, not a punishment."""
    with _client(max_auth_failures_per_minute=5) as client:
        for _ in range(4):
            response = client.post(PATH, json=BODY, headers={"x-goog-api-key": "aira_x_y"})
            assert response.status_code == 401


def test_a_persistent_prober_is_asked_to_wait() -> None:
    with _client(max_auth_failures_per_minute=3) as client:
        statuses = [
            client.post(PATH, json=BODY, headers={"x-goog-api-key": "aira_x_y"}).status_code
            for _ in range(6)
        ]

    assert 429 in statuses
    assert statuses[0] == 401


def test_the_refusal_says_when_to_come_back() -> None:
    """Without `Retry-After` a well-behaved client's only option is the immediate retry the bound
    exists to stop."""
    with _client(max_auth_failures_per_minute=2) as client:
        last = None
        for _ in range(6):
            last = client.post(PATH, json=BODY, headers={"x-goog-api-key": "aira_x_y"})

    assert last is not None
    assert last.status_code == 429
    assert int(last.headers["Retry-After"]) >= 1


def test_a_working_credential_never_touches_the_bucket() -> None:
    """The half that keeps the gateway usable: the bound can be low precisely because success
    does not count against it."""
    with _client(max_auth_failures_per_minute=1, auth_required=False) as client:
        statuses = [client.post(PATH, json=BODY).status_code for _ in range(5)]

    assert statuses == [200] * 5


def test_zero_switches_it_off() -> None:
    """An installation behind a WAF that already does this must be able to say so."""
    with _client(max_auth_failures_per_minute=0) as client:
        statuses = [
            client.post(PATH, json=BODY, headers={"x-goog-api-key": "aira_x_y"}).status_code
            for _ in range(8)
        ]

    assert set(statuses) == {401}


def test_rotating_a_forwarded_header_does_not_buy_a_fresh_bucket() -> None:
    """**The bound is only as strong as the address it counts.**

    It keys on `client_ip`, which honours `X-Forwarded-For` when the deployment says a proxy sits
    in front. A proxy *appends*, so the left of that header is whatever the caller wrote — and
    while this read the leftmost entry, a prober could send a different forged address on every
    attempt and never meet the limit. The database round trip per attempt that the bound exists to
    stop was available without limit to anyone who set one header.

    Written as the attack, through the topology that is actually deployed: the caller forges an
    entry and the proxy **appends** their real address, exactly as `$proxy_add_x_forwarded_for`
    does. Modelling it without the appended address would test a misconfiguration — a deployment
    that claims a proxy while none is in front trusts a header nobody rewrote, and no parsing rule
    can rescue that.
    """
    real = "203.0.113.7"
    with _client(max_auth_failures_per_minute=3, trust_forwarded_for=True) as client:
        statuses = [
            client.post(
                PATH,
                json=BODY,
                headers={
                    "x-goog-api-key": "aira_x_y",
                    "x-forwarded-for": f"10.0.0.{n}, {real}",
                },
            ).status_code
            for n in range(6)
        ]

    assert 429 in statuses, "a prober rotating the header was never bounded"
