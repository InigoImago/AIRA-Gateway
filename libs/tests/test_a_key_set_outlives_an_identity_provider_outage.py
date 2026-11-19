"""The key set a token is verified against, while the identity provider is down or hangs.

PyJWT's client holds a lock across the fetch and keeps no set past its five minutes: once that
expired during an outage every validation fetched again, in turn, and the *n*-th waited *n*
timeouts — and every token was refused, the moment the set expired, for as long as the outage
lasted. `BoundedRefreshJwkClient` answers both: **one fetch at a time** that nobody queues behind,
the **last set fetched kept** for a bounded time when a fetch brings no answer, and a **backoff**
between failed fetches rather than one per request.

What must not change is what a key proves: a `kid` the set held does not name is refused, a set
the provider *answers* replaces the kept one at once, and past its limit a kept set verifies
nothing. Each property is driven through the real client against a real socket that hangs, refuses
or answers, with the client's clock moved by hand instead of waited out.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from aira_common.logging import configure_logging
from aira_common.oidc import (
    JWKS_LIFESPAN_SECONDS,
    JWKS_RETRY_FIRST_SECONDS,
    JWKS_STALE_LIMIT_SECONDS,
    BoundedRefreshJwkClient,
    JwtVerifier,
)

ISSUER = "https://keycloak.internal/realms/aira"
AUDIENCE = "aira-gateway"

#: How long a fetch from the hanging endpoint waits: short, so the suite stays quick, and long
#: enough that one caller waiting it out is told apart from several waiting it out in turn.
TIMEOUT = 0.4


class _KeySet:
    """A JWKS endpoint on loopback that answers, hangs or refuses as told, counting each request.

    ``hang`` accepts the request and answers nothing until the test ends: what a Keycloak whose
    database stopped answering does. ``refuse`` answers `503`, which is what a proxy in front of a
    Keycloak that is down does.
    """

    def __init__(self) -> None:
        self.mode = "keys"
        self.keys: list[dict[str, Any]] = []
        self.fetches = 0
        self.released = threading.Event()
        endpoint = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
                endpoint.fetches += 1
                if endpoint.mode == "hang":
                    endpoint.released.wait(30)
                    return
                status, body = (
                    (503, b"Service Unavailable")
                    if endpoint.mode == "refuse"
                    else (200, json.dumps({"keys": endpoint.keys}).encode())
                )
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: Any) -> None:
                return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self.uri = f"http://127.0.0.1:{self._server.server_address[1]}/certs"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.released.set()
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def endpoint() -> Iterator[_KeySet]:
    served = _KeySet()
    yield served
    served.close()


@pytest.fixture(autouse=True)
def _json_logs() -> None:
    configure_logging("INFO", json_output=True)


class _Clock:
    """The client's clock, moved by the test: lifespans and limits are minutes long."""

    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


def _pair(kid: str) -> tuple[Any, dict[str, Any]]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return private, {**public, "kid": kid, "use": "sig", "alg": "RS256"}


def _token(private: Any, kid: str) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "u-1", "iat": now, "exp": now + 300}
    return jwt.encode(claims, private, algorithm="RS256", headers={"kid": kid})


def _verifier(endpoint: _KeySet, clock: _Clock) -> tuple[JwtVerifier, BoundedRefreshJwkClient]:
    client = BoundedRefreshJwkClient(endpoint.uri, timeout=TIMEOUT, clock=clock)
    return JwtVerifier(ISSUER, AUDIENCE, client), client


def _lines(capsys: Any, event: str) -> list[dict[str, Any]]:
    return [
        line
        for line in (
            json.loads(raw) for raw in capsys.readouterr().out.splitlines() if raw.startswith("{")
        )
        if line.get("event") == event
    ]


# == the kept set ================================================================================


def test_tokens_verify_against_the_kept_set_while_the_provider_hangs(
    endpoint: _KeySet, capsys: Any
) -> None:
    """Expired during an outage, the set fetched last still verifies — and says so, once."""
    private, public = _pair("k1")
    endpoint.keys = [public]
    clock = _Clock()
    verifier, _ = _verifier(endpoint, clock)
    assert verifier.verify(_token(private, "k1")) is not None
    capsys.readouterr()

    clock.now += JWKS_LIFESPAN_SECONDS + 1
    endpoint.mode = "hang"
    started = time.monotonic()
    first = verifier.verify(_token(private, "k1"))
    waited = time.monotonic() - started
    again = [verifier.verify(_token(private, "k1")) for _ in range(5)]

    assert first is not None, "the set expired during the outage and every token was refused"
    assert all(claims is not None for claims in again)
    assert waited < 2 * TIMEOUT
    assert endpoint.fetches == 2, "the provider was asked again by requests inside the backoff"
    (said,) = _lines(capsys, "oidc_jwks_unavailable")
    assert said["level"] == "warning"
    assert said["key_set_age_seconds"] == round(JWKS_LIFESPAN_SECONDS + 1)


def test_a_kept_set_verifies_nothing_past_its_limit(endpoint: _KeySet, capsys: Any) -> None:
    """The bound on the exposure: a key withdrawn during the outage verifies until the limit and
    not after it, however long the provider stays away."""
    private, public = _pair("k1")
    endpoint.keys = [public]
    clock = _Clock()
    verifier, _ = _verifier(endpoint, clock)
    assert verifier.verify(_token(private, "k1")) is not None

    endpoint.mode = "refuse"
    clock.now += JWKS_STALE_LIMIT_SECONDS - 1
    assert verifier.verify(_token(private, "k1")) is not None, "inside the limit"
    capsys.readouterr()

    clock.now += 2
    assert verifier.verify(_token(private, "k1")) is None
    assert _lines(capsys, "oidc_jwks_unavailable"), "the refusal is the provider's, and says so"


def test_a_key_the_kept_set_does_not_name_is_refused(endpoint: _KeySet) -> None:
    """Keeping a set changes how long its keys are known, never which keys are."""
    known, public = _pair("k1")
    stranger, _ = _pair("k2")
    endpoint.keys = [public]
    clock = _Clock()
    verifier, _ = _verifier(endpoint, clock)
    assert verifier.verify(_token(known, "k1")) is not None

    clock.now += JWKS_LIFESPAN_SECONDS + 1
    endpoint.mode = "refuse"
    assert verifier.verify(_token(stranger, "k2")) is None
    # Nor does naming a kept key help a token another key signed.
    assert verifier.verify(_token(stranger, "k1")) is None
    assert verifier.verify(_token(known, "k1")) is not None


def test_a_set_the_provider_answers_replaces_the_kept_one_at_once(endpoint: _KeySet) -> None:
    """A rotation, or a key withdrawn, takes effect on the first answer — the kept set is for when
    there is none."""
    old, old_public = _pair("k1")
    new, new_public = _pair("k2")
    endpoint.keys = [old_public]
    clock = _Clock()
    verifier, _ = _verifier(endpoint, clock)
    assert verifier.verify(_token(old, "k1")) is not None

    endpoint.keys = [new_public]
    clock.now += JWKS_LIFESPAN_SECONDS + 1
    assert verifier.verify(_token(old, "k1")) is None
    assert verifier.verify(_token(new, "k2")) is not None


def test_an_answer_holding_no_usable_key_is_adopted_too(endpoint: _KeySet) -> None:
    """An empty set is the provider's word, not its absence: nothing verifies until it publishes a
    key again. Treating it as a failed fetch would keep accepting the keys it just withdrew."""
    private, public = _pair("k1")
    endpoint.keys = [public]
    clock = _Clock()
    verifier, _ = _verifier(endpoint, clock)
    assert verifier.verify(_token(private, "k1")) is not None

    endpoint.keys = []
    clock.now += JWKS_LIFESPAN_SECONDS + 1
    assert verifier.verify(_token(private, "k1")) is None
    # Inside the backoff nobody fetches, so this is answered from whatever set is held.
    assert verifier.verify(_token(private, "k1")) is None, "the withdrawn set came back"
    clock.now += JWKS_RETRY_FIRST_SECONDS
    assert verifier.verify(_token(private, "k1")) is None


# == one fetch, and a backoff ====================================================================


def test_callers_with_no_set_share_one_fetch_and_wait_no_longer_than_it(
    endpoint: _KeySet,
) -> None:
    """Eight validations at a cold start against a provider that hangs: one fetch, and every one of
    them answered once that fetch has timed out — not the eighth after eight timeouts."""
    private, public = _pair("k1")
    endpoint.keys = [public]
    endpoint.mode = "hang"
    verifier, _ = _verifier(endpoint, _Clock())
    token = _token(private, "k1")
    took: list[float] = []
    verdicts: list[object] = []

    def _validate() -> None:
        began = time.monotonic()
        verdicts.append(verifier.verify(token))
        took.append(time.monotonic() - began)

    threads = [threading.Thread(target=_validate) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert verdicts == [None] * 8
    assert endpoint.fetches == 1, f"{endpoint.fetches} fetches for one key set"
    assert max(took) < 2 * TIMEOUT, f"answered after {sorted(round(t, 2) for t in took)}s"


def test_a_failed_fetch_is_retried_after_a_backoff_rather_than_by_every_request(
    endpoint: _KeySet,
) -> None:
    private, public = _pair("k1")
    endpoint.keys = [public]
    endpoint.mode = "refuse"
    clock = _Clock()
    verifier, _ = _verifier(endpoint, clock)
    token = _token(private, "k1")

    for _ in range(5):
        assert verifier.verify(token) is None
    assert endpoint.fetches == 1, "every request asked the provider again"

    clock.now += JWKS_RETRY_FIRST_SECONDS
    assert verifier.verify(token) is None
    assert endpoint.fetches == 2

    # Doubled after the second failure: the first interval again is not enough.
    clock.now += JWKS_RETRY_FIRST_SECONDS
    assert verifier.verify(token) is None
    assert endpoint.fetches == 2
    clock.now += JWKS_RETRY_FIRST_SECONDS
    endpoint.mode = "keys"
    assert verifier.verify(token) is not None
    assert endpoint.fetches == 3
