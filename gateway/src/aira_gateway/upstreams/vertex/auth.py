"""Google service-account credentials for Vertex AI (`FRD-115` FR-3).

A service account has an identity, rotation and IAM, which an API key has none of, and is what a
corporate GCP project grants. The exchange is the standard JWT-bearer grant: sign a short-lived
assertion with the account's key, POST it to Google's token endpoint, receive an access token.
Holding that token — cache, refresh-ahead, single-flight — is `aira_common.tokens`, shared with
every platform (`ADR-0011` rule 1).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from aira_common.tokens import AccessToken, TokenSource

#: The OAuth scope Vertex AI accepts. It is broad; what the token can reach is bounded by the
#: service account's IAM roles, not by the scope.
SCOPE = "https://www.googleapis.com/auth/cloud-platform"

#: How long the signed assertion is valid: it is a bearer credential in flight, and Google rejects
#: anything over an hour.
ASSERTION_LIFETIME_SECONDS = 3600


class CredentialsInvalid(Exception):
    """The configured service-account credentials cannot be used.

    Raised at startup, not at dispatch: a deployment with an unusable credential should refuse to
    start rather than fail every request with what looks like an upstream outage.
    """


@dataclass(frozen=True, slots=True)
class ServiceAccount:
    """The fields of a Google service-account key that the exchange needs."""

    client_email: str
    private_key: str
    token_uri: str = "https://oauth2.googleapis.com/token"

    @classmethod
    def from_json(cls, raw: str) -> ServiceAccount:
        try:
            data: dict[str, Any] = json.loads(raw)
        except ValueError as exc:
            raise CredentialsInvalid("Service-account credentials are not valid JSON.") from exc
        missing = [key for key in ("client_email", "private_key") if not data.get(key)]
        if missing:
            # Names the field, never the value: an error message is a log line (FR-8).
            raise CredentialsInvalid(
                f"Service-account credentials are missing: {', '.join(missing)}."
            )
        return cls(
            client_email=str(data["client_email"]),
            private_key=str(data["private_key"]),
            token_uri=str(data.get("token_uri") or "https://oauth2.googleapis.com/token"),
        )


class GoogleServiceAccountAcquirer:
    """Signs an assertion and exchanges it for an access token."""

    def __init__(self, account: ServiceAccount, client: httpx.AsyncClient) -> None:
        self._account = account
        self._client = client

    def _assertion(self, issued_at: int) -> str:
        return jwt.encode(
            {
                "iss": self._account.client_email,
                "scope": SCOPE,
                "aud": self._account.token_uri,
                "iat": issued_at,
                "exp": issued_at + ASSERTION_LIFETIME_SECONDS,
            },
            self._account.private_key,
            algorithm="RS256",
        )

    async def acquire(self, now: float) -> AccessToken:
        # Wall clock for the assertion (Google validates `iat`/`exp` against real time), the
        # caller's monotonic `now` for the expiry: a clock step must not expire a live token.
        response = await self._client.post(
            self._account.token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self._assertion(int(time.time())),
            },
        )
        if response.status_code != httpx.codes.OK:
            # The body can echo the assertion, so only the status is reported. Clock skew is named
            # because it presents as an unexplained 401 and is the usual cause.
            raise CredentialsInvalid(
                f"Token exchange failed with {response.status_code}. "
                "A clock more than a few minutes off is the usual cause."
            )
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise CredentialsInvalid("Token exchange returned no access token.")
        return AccessToken(str(token), expires_at=now + float(payload.get("expires_in", 3600)))


def build_token_source(credentials: str, client: httpx.AsyncClient) -> TokenSource:
    """A shared token source for one service account. Raises if the credentials are unusable."""
    return TokenSource(GoogleServiceAccountAcquirer(ServiceAccount.from_json(credentials), client))
