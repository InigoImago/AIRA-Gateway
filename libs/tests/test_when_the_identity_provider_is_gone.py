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
    """Not an outage: the key set was read and holds nothing for this token's `kid`. The
    multi-issuer probe in `OidcValidator.validate` depends on this staying a rejection."""
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
