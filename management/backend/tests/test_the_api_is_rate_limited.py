"""One caller cannot ask this API as fast as it likes (2026-08-15).

There was no bound at all. The gateway has had one on **failed authentications** since `ADR-0015`,
on the argument that every limit `FRD-405` built is keyed by use case or member and therefore
cannot bound somebody who has neither — and this plane, where every request verifies a token
against a JWKS and then reconciles the caller's groups against the directory, had nothing.

Two bounds, and only one of them is a DRF throttle:

- **`user`** bounds a signed-in caller, generously — a console screen loads five panels at once and
  paging through traces is what the product is for, so it is sized to stop a script rather than to
  shape ordinary use.
- **failed authentications**, keyed by source address, in the authentication class. It is *not* an
  `AnonRateThrottle`, and that was the first attempt: DRF runs `check_permissions` before
  `check_throttles`, and every view here requires authentication — so an anonymous request is
  refused at the permission check and the throttle never runs. Measured: two anonymous requests
  against a rate of one per minute, both `401`, the second never counted. Shipping it would have
  been the badge-wearing absent control this project keeps naming, so the bound moved to where the
  cost is: a presented token is verified against the issuer's JWKS *before* anything decides it is
  invalid.

**Per process, and said so where it is configured**: DRF counts through Django's cache and no
`CACHES` is configured, so N workers admit N × the rate — the same documented degradation
`FallbackTokenBucket` carries on the other plane, and worth having for the same reason.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from aira_management.rbac import sync_user_groups, sync_user_roles
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from .conftest import role_claims

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"


@pytest.fixture(autouse=True)
def _empty_bucket() -> Iterator[None]:
    """DRF counts in Django's cache, which is process-wide — so a test that did not clear it would
    be bounded by whatever ran before, and the order of the suite would decide the result."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def tight(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rate small enough to reach in four requests.

    **Not `override_settings`**, and that is worth stating because it looks like it should work:
    `SimpleRateThrottle.THROTTLE_RATES` is bound to `api_settings.DEFAULT_THROTTLE_RATES` at
    *class definition*, so reloading the setting leaves every throttle holding the original dict.
    A test written the obvious way passes while throttling nothing — the "guard that cannot fail"
    shape, in a fixture.
    """
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "user", "2/minute")
    monkeypatch.setitem(SimpleRateThrottle.THROTTLE_RATES, "anon", "1000/minute")


def _client() -> APIClient:
    user = get_user_model().objects.create(username="ada")
    sync_user_roles(user, role_claims("global-admin"))
    sync_user_groups(user, {"groups": []})
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_a_signed_in_caller_is_bounded(tight: None) -> None:
    """The bound exists and is reached. Driven with a tiny configured rate rather than by making
    six hundred requests: what was missing is that the throttle is *wired to these views* at all —
    the number itself is a setting."""
    del tight
    client = _client()

    statuses = [client.get(BASE).status_code for _ in range(4)]

    assert statuses[:2] == [200, 200], "the allowance is spent before anybody is refused"
    assert statuses[2:] == [429, 429], "and then the caller is asked to wait"


def test_a_throttled_request_answers_in_this_apis_envelope(tight: None) -> None:
    """`429` goes through the same exception handler as everything else, so the console reads it
    with `errorMessage` and tells the reader to wait rather than showing "Request failed."."""
    del tight
    client = _client()
    for _ in range(2):
        client.get(BASE)

    refused = client.get(BASE)

    assert refused.status_code == 429
    body = refused.json()
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["message"], "and it says something a reader can act on"


def test_an_anon_throttle_would_never_have_fired_here() -> None:
    """**Why `AnonRateThrottle` is not on the list**, kept as a test because it looks like the
    obvious answer and is a control that cannot work.

    DRF runs `check_permissions` before `check_throttles`, and every view here requires
    authentication — so an anonymous request is refused at the permission check and the throttle
    is never reached. Whatever the rate, the second request is a `401` like the first, and nothing
    was counted. Asserting this keeps somebody from "fixing" the missing anon scope by adding a
    class that quietly does nothing.
    """
    anonymous = APIClient()

    assert [anonymous.get(BASE).status_code for _ in range(3)] == [401, 401, 401]


def test_a_rejected_token_is_bounded_by_source_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unauthenticated half, bounded where the cost actually is.

    A presented token is verified against the issuer's JWKS **before** anything decides it is
    invalid, so an address probing credentials pays nothing and this service pays per attempt.
    Refusals only, so a working credential never touches the bucket (`ADR-0015`).
    """
    from aira_management.apps.api import authentication

    monkeypatch.setattr(authentication, "build_management_verifier", lambda: _RejectsEverything())
    monkeypatch.setattr(authentication, "build_attempt_bound", lambda: _bound("2/minute"))
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": "Bearer not-a-real-token"}

    statuses = [client.get(BASE, **headers).status_code for _ in range(4)]

    assert statuses[:2] == [401, 401], "two attempts are refused as attempts"
    assert statuses[2:] == [429, 429], "and then the address is asked to wait"


def test_a_working_credential_never_touches_that_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    """The property that makes the bound safe to have: it counts refusals, so no legitimate
    integration can be throttled by it however busy it is."""
    from aira_management.apps.api import authentication

    monkeypatch.setattr(authentication, "build_attempt_bound", lambda: _bound("1/minute"))
    client = _client()  # authenticated, so the authentication class is not reached at all

    assert [client.get(BASE).status_code for _ in range(3)] == [200, 200, 200]


def test_a_rate_of_zero_switches_the_bound_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """For an installation whose WAF already does this. Off means off — not "off and still
    counting", which would be a bucket nobody can see filling."""
    from aira_management.apps.api import authentication

    monkeypatch.setattr(authentication, "build_management_verifier", lambda: _RejectsEverything())
    monkeypatch.setattr(authentication, "build_attempt_bound", lambda: _bound("0/minute"))
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": "Bearer not-a-real-token"}

    assert [client.get(BASE, **headers).status_code for _ in range(3)] == [401, 401, 401]


class _RejectsEverything:
    """A verifier that refuses, so the test drives the refusal path without a Keycloak."""

    def verify(self, token: str) -> None:
        del token
        return None


def _bound(rate: str) -> Any:
    from aira_management.apps.api.attempts import FailedAuthentications

    return FailedAuthentications(rate)


def test_the_shipped_rates_do_not_bound_ordinary_use() -> None:
    """A limit a person meets by working is a limit that gets switched off. The defaults are
    checked against what the product actually does — five panels at once, and paging — rather than
    left to taste."""
    from aira_management.config.runtime import get_settings

    settings = get_settings()
    per_minute = int(settings.throttle_user.split("/")[0])

    assert per_minute >= 300, f"{settings.throttle_user} is tight enough to bite a real session"
    assert int(settings.throttle_auth_failures.split("/")[0]) < per_minute, (
        "an address collecting refusals has no business doing so as fast as a signed-in caller "
        "does real work"
    )
