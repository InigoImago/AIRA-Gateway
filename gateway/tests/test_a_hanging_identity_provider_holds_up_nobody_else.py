"""A Keycloak that accepts connections and never answers holds up only its own tokens' callers.

Token validation left the event loop for a worker thread, and that was half of it: the thread was
the loop's **default executor**, which the audit writer, mail and (on the standard loop) every name
resolution share, and PyJWT fetched the key set under a lock. Once the cached set expired during
such an outage every bearer request fetched in turn — the *n*-th answered after *n* timeouts — and
the threads waiting filled the executor: an audit row waited for the identity provider, so did an
API-key request once the writer's queue was full, and so did `/readyz`.

Driven through the real `resolve_principal`, the real validator and JWKS client, and the real audit
writer writing inline (as it does once its queue is full), against a loopback endpoint that accepts
the fetch and never answers it. The loop's default executor is cut to four threads, as few as a
small container has, so that work queued behind the validations shows.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import http.server
import json
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from aira_common.oidc import JWKS_LIFESPAN_SECONDS, BoundedRefreshJwkClient
from aira_gateway.auth.dependencies import resolve_principal
from aira_gateway.auth.oidc import OidcValidator
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import Base
from aira_gateway.persistence.redaction import NoOpRedactor
from aira_gateway.persistence.writer import PendingLog, RequestLogWriter

A = "https://kc-a.example/realms/aira"
B = "https://kc-b.example/realms/aira"

#: The fetch timeout here, standing in for the five seconds of production: both effects scale with
#: it, and a quarter of it is still an order of magnitude above an unhindered audit write.
TIMEOUT = 0.5

#: Bearer requests at once — twice the threads validation has, so some wait for a thread too.
CONCURRENT = 8


class _Realm:
    """One realm's signing key and a loopback JWKS endpoint for it that answers or hangs."""

    def __init__(self, issuer: str) -> None:
        self.issuer = issuer
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key()))
        self.jwks = {"keys": [{**public, "kid": "k1", "use": "sig", "alg": "RS256"}]}
        self.hanging = False
        self.fetches = 0
        self.released = threading.Event()
        realm = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
                realm.fetches += 1
                if realm.hanging:
                    realm.released.wait(30)
                    return
                body = json.dumps(realm.jwks).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: Any) -> None:
                return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self.uri = f"http://127.0.0.1:{self._server.server_address[1]}/certs"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def token(self) -> str:
        now = dt.datetime.now(dt.UTC)
        claims = {
            "iss": self.issuer,
            "sub": "u-1",
            "iat": now,
            "exp": now + dt.timedelta(minutes=5),
        }
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": "k1"})

    def close(self) -> None:
        self.released.set()
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def realms() -> Iterator[tuple[_Realm, _Realm]]:
    a, b = _Realm(A), _Realm(B)
    yield a, b
    a.close()
    b.close()


class _Clock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


def _request(validator: OidcValidator, token: str) -> Any:
    """What `resolve_principal` reads before it reaches the validator."""
    return SimpleNamespace(
        headers={"authorization": f"Bearer {token}"},
        query_params={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(auth_required=True), oidc_validator=validator
            )
        ),
    )


@pytest.fixture
async def writer() -> Any:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    # Inline, as `submit` writes once the queue of 512 rows is full: on the request path.
    yield RequestLogWriter(
        async_sessionmaker(engine, expire_on_commit=False),
        GatewaySettings(),
        NoOpRedactor(),
        max_queue=0,
    )
    await engine.dispose()


def _an_api_key_requests_row() -> PendingLog:
    return PendingLog(
        subject="key-owner",
        auth_method="api_key",
        use_case=None,
        source_ip="10.0.0.1",
        operation="generateContent",
        model="mock-1",
        status=200,
        usage=None,
        latency_ms=5,
        trace_id=None,
        request_payload={"contents": []},
        response_payload={"text": "ok"},
        cost_nanos=0,
        api="gemini",
    )


async def _while_bearer_requests_wait(
    validator: OidcValidator, token: str, writer: RequestLogWriter
) -> tuple[list[tuple[float, object]], float]:
    """``CONCURRENT`` bearer requests through `resolve_principal`, and an API-key request's audit
    row written while they wait: when each bearer request was answered and with what, and how long
    the row took."""
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=4))
    started = time.monotonic()

    async def bearer() -> tuple[float, object]:
        principal = await resolve_principal(_request(validator, token))
        return time.monotonic() - started, principal

    logins = [asyncio.create_task(bearer()) for _ in range(CONCURRENT)]
    await asyncio.sleep(0.05)  # the row arrives while the fetch hangs
    began = time.monotonic()
    await writer.submit(_an_api_key_requests_row())
    row_took = time.monotonic() - began
    return sorted(await asyncio.gather(*logins), key=lambda done: done[0]), row_took


async def test_a_cold_start_against_a_hanging_provider_costs_one_timeout_and_no_audit_row(
    realms: tuple[_Realm, _Realm], writer: RequestLogWriter
) -> None:
    """No key set held yet, and the provider hangs: every bearer request is refused after one
    fetch's timeout — not the eighth after eight — and the audit row of an API-key request, written
    meanwhile, does not wait for any of them."""
    realm, _ = realms
    realm.hanging = True
    client = BoundedRefreshJwkClient(realm.uri, timeout=TIMEOUT)
    validator = OidcValidator(realm.issuer, None, client)

    answered, row_took = await _while_bearer_requests_wait(validator, realm.token(), writer)

    assert [principal for _, principal in answered] == [None] * CONCURRENT
    assert realm.fetches == 1, f"{realm.fetches} fetches of one key set"
    assert answered[-1][0] < 2 * TIMEOUT, f"answered after {[round(t, 2) for t, _ in answered]}s"
    assert row_took < TIMEOUT / 4, f"an API-key request's audit row waited {row_took:.2f}s"


async def test_tokens_keep_verifying_against_the_kept_key_set_while_the_provider_hangs(
    realms: tuple[_Realm, _Realm], writer: RequestLogWriter
) -> None:
    """The set expired during the outage. One request asks the provider again and waits out the
    timeout; every other one is verified against the set already held, at once."""
    realm, _ = realms
    clock = _Clock()
    client = BoundedRefreshJwkClient(realm.uri, timeout=TIMEOUT, clock=clock)
    validator = OidcValidator(realm.issuer, None, client)
    assert validator.validate(realm.token()) is not None
    clock.now += JWKS_LIFESPAN_SECONDS + 1
    realm.hanging = True

    answered, row_took = await _while_bearer_requests_wait(validator, realm.token(), writer)

    assert all(principal is not None for _, principal in answered), "the outage refused tokens"
    assert realm.fetches == 2, f"the expired set was fetched {realm.fetches - 1} times"
    assert sum(1 for took, _ in answered if took >= TIMEOUT / 2) <= 1, (
        f"more than one request waited for the provider: {[round(t, 2) for t, _ in answered]}s"
    )
    assert row_took < TIMEOUT / 4


async def test_each_realm_keeps_its_own_key_set(realms: tuple[_Realm, _Realm]) -> None:
    """Several issuers: one realm's provider hanging delays only that realm's tokens, and a token
    is verified against its own realm's keys, never the other's."""
    a, b = realms
    validator = OidcValidator(
        A,
        None,
        BoundedRefreshJwkClient(a.uri, timeout=TIMEOUT),
        others=((B, "", BoundedRefreshJwkClient(b.uri, timeout=TIMEOUT)),),
    )
    a.hanging = True
    started = time.monotonic()
    waiting = asyncio.create_task(resolve_principal(_request(validator, a.token())))
    await asyncio.sleep(0.05)

    other = await resolve_principal(_request(validator, b.token()))
    other_took = time.monotonic() - started

    assert other is not None and other.issuer == B
    assert other_took < TIMEOUT, "realm B's token waited for realm A's provider"
    assert await waiting is None
    # A token naming realm B and signed with realm A's key is not B's, however B's set is held.
    forged = jwt.encode(
        {"iss": B, "sub": "u-1", "iat": int(time.time()), "exp": int(time.time()) + 300},
        a.private,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    assert validator.validate(forged) is None
