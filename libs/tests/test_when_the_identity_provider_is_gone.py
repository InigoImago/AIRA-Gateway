"""An unreachable Keycloak is not a bad token, and it must not be reported as one (`FRD-617`).

`PyJWKClientError` is a subclass of `PyJWTError`, so the JWKS fetch sat inside the same `try` as
the decode: a refused connection, a DNS failure and a read timeout against the identity provider
all came out as `oidc_token_rejected` at `INFO` and as a `401`. The moment Keycloak goes away was
therefore reported to every operator as *every user's credential is suddenly invalid* — which
sends whoever reads it to the wrong system entirely, on the day the right one is down.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import jwt
import pytest

from aira_common.integration_debug import configure_integration_debug
from aira_common.logging import configure_logging
from aira_common.oidc import DEFAULT_JWKS_TIMEOUT_SECONDS, JwtVerifier, build_jwks_client


@pytest.fixture(autouse=True)
def _channel() -> Iterator[None]:
    configure_logging("INFO", json_output=True)
    configure_integration_debug("auth")
    yield
    configure_integration_debug("")


def lines(capsys: Any) -> list[dict]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip().startswith("{")
    ]


class Resolver:
    """A JWKS client that fails the way a real one does. `uri` is what `PyJWKClient` exposes."""

    uri = "https://keycloak.internal/realms/aira/protocol/openid-connect/certs"

    def __init__(self, error: Exception | None = None, key: object = "the-key") -> None:
        self._error = error
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> Any:
        if self._error is not None:
            raise self._error
        return type("K", (), {"key": self._key})()


def _verifier(resolver: Resolver) -> JwtVerifier:
    return JwtVerifier("https://keycloak.internal/realms/aira", "aira-gateway", resolver)


def test_a_provider_that_cannot_be_reached_is_reported_as_the_provider(capsys: Any) -> None:
    error = jwt.PyJWKClientConnectionError("Fail to fetch data from the url, err: [Errno 111]")
    assert _verifier(Resolver(error)).verify("a.token.here") is None

    emitted = lines(capsys)
    outage = [line for line in emitted if line["event"] == "oidc_jwks_unavailable"]
    assert outage, "an unreachable identity provider must not be logged as a rejected token"
    assert outage[0]["level"] == "warning"
    assert outage[0]["jwks_uri"] == Resolver.uri
    assert "Errno 111" in outage[0]["error"]
    # And it is *not* also reported as the caller's credential being wrong.
    assert not [line for line in emitted if line["event"] == "oidc_token_rejected"]


def test_the_outage_is_one_line_on_the_integration_channel_too(capsys: Any) -> None:
    error = jwt.PyJWKClientConnectionError("timed out")
    _verifier(Resolver(error)).verify("a.token.here")

    (call,) = [line for line in lines(capsys) if line["event"] == "integration_call"]
    assert call["system"] == "auth"
    assert call["operation"] == "jwks.fetch"
    assert call["outcome"] == "failed"
    assert call["target"] == Resolver.uri
    assert call["issuer"] == "https://keycloak.internal/realms/aira"


def test_a_key_set_that_holds_no_matching_key_is_still_a_rejected_token(capsys: Any) -> None:
    """Not an outage: the key set was read and holds nothing for this token's `kid` — which is
    what a forged `kid` is, so it must not warn of an identity provider that is working."""
    error = jwt.PyJWKClientError('Unable to find a signing key that matches: "abc"')
    assert _verifier(Resolver(error)).verify("a.token.here") is None

    emitted = lines(capsys)
    assert [line for line in emitted if line["event"] == "oidc_token_rejected"]
    assert not [line for line in emitted if line["event"] == "oidc_jwks_unavailable"]


def test_a_successful_fetch_is_reported_with_a_duration(capsys: Any) -> None:
    """The key set is cached, so this line appears on a cold start and on a rotation — which is
    exactly when somebody wants to know whether the provider answered, and how quickly."""
    _verifier(Resolver()).verify("not.a.real.token")

    (call,) = [line for line in lines(capsys) if line["event"] == "integration_call"]
    assert call["outcome"] == "ok"
    assert call["duration_ms"] >= 0


def test_the_jwks_client_is_built_with_a_bounded_timeout() -> None:
    """PyJWT's default is thirty seconds, and `PyJWKClient` fetches with `urllib` — synchronously,
    from an `async` dependency. Nothing was passed, so thirty seconds is what applied."""
    client = build_jwks_client("https://keycloak.internal/certs")
    assert client.timeout == DEFAULT_JWKS_TIMEOUT_SECONDS
    assert DEFAULT_JWKS_TIMEOUT_SECONDS < 30
    assert build_jwks_client("https://x/certs", timeout=1.5).timeout == 1.5


def test_a_malformed_token_is_a_401_and_not_a_500(capsys: Any) -> None:
    """A caller's own value must never become a server error (`LESSONS.md` §1).

    `PyJWKClient` parses the token to find its `kid` **before** it fetches anything, so a truncated
    or corrupted bearer raises `DecodeError` out of the fetch step. Splitting that step out of
    `verify` narrowed what its `except` covered, and this went from a `401` to an unhandled
    exception — found by pointing the FRD-617 demonstration at a hand-written token, which is
    precisely the value a client sends.
    """
    error = jwt.DecodeError("Invalid crypto padding")
    assert _verifier(Resolver(error)).verify("eyJhbGciOiJSUzI1NiJ9.e30.x") is None

    rejected = [line for line in lines(capsys) if line["event"] == "oidc_token_rejected"]
    assert rejected and rejected[0]["reason"] == "DecodeError"


def test_a_real_pyjwkclient_refuses_a_malformed_token_without_raising() -> None:
    """The same property against the **real** client rather than a stand-in: a fake that raises
    where the real one raises is still a test of the fake (`LESSONS.md` §7)."""
    verifier = JwtVerifier(
        "https://keycloak.internal/realms/aira",
        "aira-gateway",
        build_jwks_client("http://127.0.0.1:9/certs", timeout=0.5),
    )
    assert verifier.verify("not.a.token") is None


# == a real client against a key set that answers badly ==========================================


class _KeySetServer:
    """A JWKS endpoint on loopback that answers as it is told and counts what it was asked.

    A real `PyJWKClient` against a real socket: the failures below are what `urllib` raises out of a
    read, and a stand-in that raised them by hand would only test that it can raise them.
    """

    def __init__(self) -> None:
        import http.server
        import threading

        self.mode = "jwks"
        self.keys: list[dict[str, Any]] = []
        self.fetches = 0
        server = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the stdlib's spelling
                server.fetches += 1
                if server.mode == "html":
                    body = b"<html><body>Please sign in</body></html>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif server.mode == "truncated":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "1000")
                    self.end_headers()
                    self.wfile.write(b'{"keys": [')
                    self.close_connection = True
                else:
                    body = json.dumps({"keys": server.keys}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *_args: Any) -> None:
                return

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.uri = f"http://127.0.0.1:{self._httpd.server_address[1]}/certs"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def key_set() -> Iterator[_KeySetServer]:
    server = _KeySetServer()
    yield server
    server.close()


def _signing_pair(kid: str) -> tuple[Any, dict[str, Any]]:
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return private, {**public, "kid": kid, "use": "sig", "alg": "RS256"}


def _signed(private: Any, kid: str) -> str:
    import time as _time

    now = int(_time.time())
    claims = {"iss": "https://keycloak.internal/realms/aira", "sub": "u-1", "iat": now}
    return jwt.encode(
        {**claims, "exp": now + 300, "aud": "aira-gateway"},
        private,
        algorithm="RS256",
        headers={"kid": kid},
    )


@pytest.mark.parametrize("mode", ["html", "truncated"])
def test_a_key_set_that_cannot_be_read_is_the_provider_not_the_token(
    key_set: _KeySetServer, mode: str, capsys: Any
) -> None:
    """`PyJWKClient` converts a refused connection and nothing else. A sign-in page answered with
    200, or a body cut off mid-read, left `verify` as `JSONDecodeError` or `IncompleteRead` — a
    500 for every bearer request, and none of them logged as what it was."""
    key_set.mode = mode
    private, _public = _signing_pair("k1")
    verifier = JwtVerifier(
        "https://keycloak.internal/realms/aira",
        "aira-gateway",
        build_jwks_client(key_set.uri, timeout=2.0),
    )

    assert verifier.verify(_signed(private, "k1")) is None

    emitted = lines(capsys)
    assert [line for line in emitted if line["event"] == "oidc_jwks_unavailable"]
    assert not [line for line in emitted if line["event"] == "oidc_token_rejected"]


def test_a_token_nested_past_what_the_parser_follows_is_a_rejected_token(capsys: Any) -> None:
    """The caller's own header, as deeply nested JSON: `json` raises `RecursionError`, which is not
    a `ValueError`, so PyJWT does not wrap it. A worker thread's stack is what decides how deep is
    too deep — a small one, as here, reaches it with a token of a few kilobytes."""
    import base64
    import threading

    header = base64.urlsafe_b64encode(b"[" * 5000).rstrip(b"=").decode()
    token = f"{header}.e30.c2ln"
    verifier = JwtVerifier(
        "https://keycloak.internal/realms/aira",
        "aira-gateway",
        build_jwks_client("http://127.0.0.1:9/certs", timeout=0.5),
    )
    outcome: list[object] = []

    def _verify() -> None:
        try:
            outcome.append(verifier.verify(token))
        except BaseException as exc:  # noqa: BLE001 - what escapes is the finding
            outcome.append(exc)

    previous = threading.stack_size(256 * 1024)
    try:
        worker = threading.Thread(target=_verify)
        worker.start()
        worker.join()
    finally:
        threading.stack_size(previous)

    assert outcome == [None]
    assert [line for line in lines(capsys) if line["event"] == "oidc_token_rejected"]


def test_an_oversized_token_is_refused_before_anything_parses_it() -> None:
    from aira_common.oidc import MAX_TOKEN_LENGTH

    asked: list[str] = []

    class _Counting(Resolver):
        def get_signing_key_from_jwt(self, token: str) -> Any:
            asked.append(token)
            return super().get_signing_key_from_jwt(token)

    oversized = "e" * (MAX_TOKEN_LENGTH + 1)
    assert _verifier(_Counting()).verify(oversized) is None
    assert asked == []


# == an unknown key id costs a fetch at most once per interval ===================================


def test_unknown_key_ids_refetch_the_key_set_at_most_once_per_interval(
    key_set: _KeySetServer,
) -> None:
    """PyJWT refetches the whole set for every `kid` it does not hold — right for a rotation, and
    one request to Keycloak per forged token otherwise, made synchronously on a worker thread."""
    private, public = _signing_pair("k1")
    key_set.keys = [public]
    verifier = JwtVerifier(
        "https://keycloak.internal/realms/aira",
        "aira-gateway",
        build_jwks_client(key_set.uri, timeout=2.0),
    )
    assert verifier.verify(_signed(private, "k1")) is not None
    before = key_set.fetches

    for index in range(50):
        assert verifier.verify(_signed(private, f"forged-{index}")) is None

    assert key_set.fetches - before <= 1


def test_a_rotated_key_verifies_once_the_interval_has_passed(key_set: _KeySetServer) -> None:
    from aira_common.oidc import JWKS_REFRESH_INTERVAL_SECONDS, BoundedRefreshJwkClient

    now = [1000.0]
    old, old_public = _signing_pair("k1")
    key_set.keys = [old_public]
    client = BoundedRefreshJwkClient(key_set.uri, timeout=2.0, clock=lambda: now[0])
    verifier = JwtVerifier("https://keycloak.internal/realms/aira", "aira-gateway", client)
    assert verifier.verify(_signed(old, "k1")) is not None
    assert verifier.verify(_signed(old, "forged")) is None  # spends this interval's refetch

    new, new_public = _signing_pair("k2")
    key_set.keys = [old_public, new_public]
    assert verifier.verify(_signed(new, "k2")) is None, "refetched inside the interval"

    now[0] += JWKS_REFRESH_INTERVAL_SECONDS + 1
    assert verifier.verify(_signed(new, "k2")) is not None
