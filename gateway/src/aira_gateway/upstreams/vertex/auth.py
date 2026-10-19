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
from pathlib import Path
from typing import Any

import httpx
import jwt

from aira_common.tokens import AccessToken
from aira_gateway.upstreams.diagnosis import Diagnosis, from_response, from_transport

#: The OAuth scope Vertex AI accepts. It is broad; what the token can reach is bounded by the
#: service account's IAM roles, not by the scope.
SCOPE = "https://www.googleapis.com/auth/cloud-platform"

#: How long the signed assertion is valid: it is a bearer credential in flight, and Google rejects
#: anything over an hour.
ASSERTION_LIFETIME_SECONDS = 3600

#: What to do about a key file that is configured and not there. Said rather than worked around:
#: falling back to another identity would run the gateway as an account nobody configured.
UNREADABLE_FILE_HINT = (
    "Mount the file at that path, or unset the setting: with no credential set at all, a gateway "
    "on Google Cloud uses the machine's own service account."
)


class CredentialsInvalid(Exception):
    """The configured service-account credentials cannot be used.

    Raised at startup, not at dispatch: a deployment with an unusable credential should refuse to
    start rather than fail every request with what looks like an upstream outage. Raised by a token
    exchange too, carrying the cause Google stated (`FRD-141`).
    """

    def __init__(self, message: str, diagnosis: Diagnosis | None = None) -> None:
        super().__init__(message)
        self.diagnosis = diagnosis


@dataclass(frozen=True, slots=True)
class ServiceAccount:
    """The fields of a Google service-account key that the exchange needs."""

    client_email: str
    private_key: str
    token_uri: str = "https://oauth2.googleapis.com/token"

    @classmethod
    def from_json(cls, raw: str) -> ServiceAccount:
        """Read a service-account key, refusing at startup whatever would fail at the first call.

        The key is **used** here, once, by signing a throwaway claim: a garbled PEM is otherwise
        accepted at startup and turns every request into a 503 that looks like Google being down —
        the case `CredentialsInvalid` exists to rule out.
        """
        try:
            data: Any = json.loads(raw)
        except ValueError as exc:
            raise CredentialsInvalid("Service-account credentials are not valid JSON.") from exc
        if not isinstance(data, dict):
            raise CredentialsInvalid(
                "Service-account credentials must be a JSON object, as Google issues them."
            )
        missing = [key for key in ("client_email", "private_key") if not data.get(key)]
        if missing:
            # Names the field, never the value: an error message is a log line (FR-8).
            raise CredentialsInvalid(
                f"Service-account credentials are missing: {', '.join(missing)}."
            )
        account = cls(
            client_email=str(data["client_email"]),
            private_key=str(data["private_key"]),
            token_uri=str(data.get("token_uri") or "https://oauth2.googleapis.com/token"),
        )
        try:
            jwt.encode({"aud": account.token_uri}, account.private_key, algorithm="RS256")
        except Exception as exc:  # noqa: BLE001 — every way a key fails to sign is this refusal
            # The type, never the message or the key: an error message is a log line (FR-8).
            raise CredentialsInvalid(
                "The service account's private_key cannot sign a token "
                f"({type(exc).__name__}). It must be the PEM key Google issued with the account."
            ) from None
        return account


class KeyFile:
    """A service-account key read from a file, and read **again** when the file changes.

    A file is how a key reaches a container without passing through a single environment value —
    a Docker or Kubernetes secret, a Vault agent template — and how it is rotated without a restart:
    the new key is written over the old one, and the next token is signed with it. A file that is
    changed into something unusable keeps the last key that worked, and says so where a refresh
    failure is said: the held token outlives the mistake (`aira_common.tokens`).
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._seen: tuple[int, int] | None = None
        self._account: ServiceAccount | None = None
        self.load()

    def load(self) -> ServiceAccount:
        try:
            stat = self._path.stat()
        except OSError as exc:
            raise CredentialsInvalid(
                f"The service-account key file {self._path} cannot be read "
                f"({type(exc).__name__}). {UNREADABLE_FILE_HINT}"
            ) from None
        stamp = (stat.st_mtime_ns, stat.st_size)
        if self._account is None or stamp != self._seen:
            try:
                raw = self._path.read_text(encoding="utf-8")
            except OSError as exc:
                raise CredentialsInvalid(
                    f"The service-account key file {self._path} cannot be read "
                    f"({type(exc).__name__})."
                ) from None
            self._account = ServiceAccount.from_json(raw)
            self._seen = stamp
        return self._account


class GoogleServiceAccountAcquirer:
    """Signs an assertion and exchanges it for an access token."""

    def __init__(self, account: ServiceAccount | KeyFile, client: httpx.AsyncClient) -> None:
        self._source = account
        self._client = client

    @property
    def _account(self) -> ServiceAccount:
        """The key to sign with now: a file is looked at again on every exchange, about hourly."""
        source = self._source
        return source.load() if isinstance(source, KeyFile) else source

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
        try:
            response = await self._client.post(
                self._account.token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": self._assertion(int(time.time())),
                },
            )
        except httpx.HTTPError as exc:
            # A proxy that inspects TLS meets the token endpoint first; say so (`FRD-141`).
            found = from_transport(exc)
            raise CredentialsInvalid(
                f"Token exchange did not reach Google ({type(exc).__name__})."
                + (f" {found}" if found else ""),
                found,
            ) from exc
        if response.status_code != httpx.codes.OK:
            # The body can echo the assertion, so its text is never repeated: only the cause it
            # names, matched against Google's own vocabulary (`diagnosis.from_response`).
            found = from_response(response)
            raise CredentialsInvalid(
                f"Token exchange failed with {response.status_code}."
                + (f" {found}" if found else ""),
                found,
            )
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise CredentialsInvalid("Token exchange returned no access token.")
        return AccessToken(str(token), expires_at=now + float(payload.get("expires_in", 3600)))
