"""Access to Google without a Google key: workload identity federation and the metadata server
(`FRD-142`, `ADR-0037`).

A service-account key is a private key that never expires until somebody deletes it, and an
organisation created since May 2024 is not allowed to create one by default. Two ways need none:

    federation       an identity the installation already has — a token from its own Keycloak, or
                     one a platform writes to a file — is exchanged at Google's Security Token
                     Service for a Google token, which then acts as a service account for an hour.
                     Google holds no secret of ours; we hold none of Google's.
    metadata server  on Google's own infrastructure the machine is the identity, and asking the
                     metadata server is all there is to it.

The configuration is Google's own `external_account` JSON (AIP-4117), as `gcloud iam
workload-identity-pools create-cred-config` writes it. It names no secret, so it may sit in a
config file. Its `credential_source` may be a file or a local URL; or the gateway fetches the
subject token itself with a client-credentials grant against an OIDC provider
(`AIRA_VERTEX_FEDERATION_*`), which is how a Keycloak becomes the identity. AWS and executable
sources are refused by name: neither has a use here, and an executable is a command a credential
file would run.

Holding the resulting token — cache, refresh ahead of expiry, single-flight — is
`aira_common.tokens`, as for every other platform.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from aira_common.tokens import AccessToken
from aira_gateway.upstreams.diagnosis import from_response, from_transport
from aira_gateway.upstreams.vertex.auth import SCOPE, CredentialsInvalid

#: What Google's STS is asked for, per RFC 8693 as AIP-4117 spells it.
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"

#: The subject token type of a JWT — what an OIDC provider's access token or ID token is.
JWT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"

#: How long an impersonated token is asked to live when the configuration says nothing: Google's
#: default, and the longest it grants without an organisation policy.
DEFAULT_LIFETIME_SECONDS = 3600

#: Where a Google VM, a GKE pod with workload identity or a Cloud Run service asks for its token.
#: Plain HTTP by Google's design: the address never leaves the host.
METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
)

#: Hosts a credential configuration may send a token to. The subject token is a credential of the
#: installation's own identity provider, and a configuration that pointed `token_url` elsewhere
#: would hand it over.
_GOOGLE_HOST_SUFFIX = ".googleapis.com"


class SubjectToken(Protocol):
    """Where the identity being exchanged comes from."""

    async def fetch(self) -> str: ...


def _google_url(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CredentialsInvalid(f"The external_account configuration has no {field}.")
    parts = urlsplit(value)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(_GOOGLE_HOST_SUFFIX):
        raise CredentialsInvalid(
            f"The external_account configuration's {field} must be an https address at Google "
            f"(*{_GOOGLE_HOST_SUFFIX}): the token sent there is a credential."
        )
    return value


def _pick(raw: str, fmt: dict[str, Any], where: str) -> str:
    """The token in a file's or a URL's answer: the whole text, or one field of its JSON."""
    if fmt.get("type", "text") == "json":
        field = fmt.get("subject_token_field_name")
        try:
            value = json.loads(raw).get(field) if field else None
        except ValueError, AttributeError:
            value = None
        if not isinstance(value, str) or not value:
            raise CredentialsInvalid(f"{where} holds no field '{field}' with a token in it.")
        return value
    token = raw.strip()
    if not token:
        raise CredentialsInvalid(f"{where} is empty.")
    return token


@dataclass(frozen=True, slots=True)
class FileSubjectToken:
    """A token a platform keeps current in a file — a Kubernetes projected token, an agent's
    output. Read on every exchange, so a renewed token is the one sent."""

    path: str
    fmt: dict[str, Any]

    async def fetch(self) -> str:
        return self.read()

    def read(self) -> str:
        """A small local file, read in place: once an hour, never on the request path."""
        try:
            raw = Path(self.path).read_text(encoding="utf-8")
        except OSError as exc:
            raise CredentialsInvalid(
                f"The subject token file {self.path} cannot be read ({type(exc).__name__})."
            ) from None
        return _pick(raw, self.fmt, f"The subject token file {self.path}")


@dataclass(frozen=True, slots=True)
class UrlSubjectToken:
    """A token a local endpoint hands out on a GET, as Azure's instance metadata does."""

    url: str
    headers: dict[str, str]
    fmt: dict[str, Any]
    client: httpx.AsyncClient

    async def fetch(self) -> str:
        try:
            response = await self.client.get(self.url, headers=self.headers)
        except httpx.HTTPError as exc:
            found = from_transport(exc)
            raise CredentialsInvalid(
                f"The subject token URL did not answer ({type(exc).__name__}).", found
            ) from exc
        if response.status_code != httpx.codes.OK:
            raise CredentialsInvalid(
                f"The subject token URL answered {response.status_code}.", from_response(response)
            )
        return _pick(response.text, self.fmt, "The subject token URL's answer")


@dataclass(frozen=True, slots=True)
class ClientCredentialsSubjectToken:
    """A token the gateway asks its own OIDC provider for, as a client of it.

    The client-credentials grant: the gateway is a confidential client — in Keycloak, one with
    service accounts enabled — and the access token it receives is the identity Google trusts. Its
    `aud` must be what the workload identity provider allows, which in Keycloak is an audience
    mapper on the client.
    """

    token_url: str
    client_id: str
    client_secret: str
    client: httpx.AsyncClient

    async def fetch(self) -> str:
        try:
            response = await self.client.post(
                self.token_url,
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
            )
        except httpx.HTTPError as exc:
            found = from_transport(exc)
            raise CredentialsInvalid(
                "The identity provider did not answer the client-credentials request "
                f"({type(exc).__name__}).",
                found,
            ) from exc
        if response.status_code != httpx.codes.OK:
            found = from_response(response)
            raise CredentialsInvalid(
                f"The identity provider refused the client-credentials request "
                f"({response.status_code}).",
                found,
            )
        token = _json(response).get("access_token")
        if not isinstance(token, str) or not token:
            raise CredentialsInvalid("The identity provider answered without an access token.")
        return token


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass(frozen=True, slots=True)
class ExternalAccount:
    """Google's `external_account` configuration, the parts this gateway acts on."""

    audience: str
    subject_token_type: str
    token_url: str
    impersonation_url: str
    lifetime_seconds: int
    credential_source: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, has_supplier: bool) -> ExternalAccount:
        """Read the configuration, refusing at start whatever would fail at the first exchange."""
        audience = data.get("audience")
        if not isinstance(audience, str) or not audience.startswith("//iam.googleapis.com/"):
            raise CredentialsInvalid(
                "The external_account configuration's audience must be the workload identity "
                "provider's full name, //iam.googleapis.com/projects/…/providers/…."
            )
        source = data.get("credential_source") or {}
        if not isinstance(source, dict):
            raise CredentialsInvalid("The external_account credential_source is not an object.")
        if "environment_id" in source or "executable" in source:
            raise CredentialsInvalid(
                "The external_account credential_source is an AWS or an executable source, which "
                "this gateway does not run. Use a file, a URL, or AIRA_VERTEX_FEDERATION_* to "
                "fetch the token from your identity provider."
            )
        if not has_supplier and not (source.get("file") or source.get("url")):
            raise CredentialsInvalid(
                "The external_account configuration says nowhere to get the identity from: give "
                "its credential_source a file or a url, or set AIRA_VERTEX_FEDERATION_TOKEN_URL, "
                "_CLIENT_ID and _CLIENT_SECRET."
            )
        impersonation = data.get("service_account_impersonation_url") or ""
        options = data.get("service_account_impersonation") or {}
        lifetime = options.get("token_lifetime_seconds", DEFAULT_LIFETIME_SECONDS)
        if (
            not isinstance(lifetime, int)
            or isinstance(lifetime, bool)
            or not (600 <= lifetime <= 43200)
        ):
            raise CredentialsInvalid(
                "service_account_impersonation.token_lifetime_seconds must lie between 600 and "
                "43200, as Google allows."
            )
        return cls(
            audience=audience,
            subject_token_type=str(data.get("subject_token_type") or JWT_TOKEN_TYPE),
            token_url=_google_url(
                data.get("token_url") or "https://sts.googleapis.com/v1/token", "token_url"
            ),
            impersonation_url=(
                _google_url(impersonation, "service_account_impersonation_url")
                if impersonation
                else ""
            ),
            lifetime_seconds=lifetime,
            credential_source=source,
        )

    def subject(self, client: httpx.AsyncClient) -> SubjectToken:
        """The configuration's own source of the identity: a file first, as AIP-4117 orders it."""
        fmt = self.credential_source.get("format") or {}
        if self.credential_source.get("file"):
            return FileSubjectToken(str(self.credential_source["file"]), fmt)
        headers = self.credential_source.get("headers") or {}
        return UrlSubjectToken(
            str(self.credential_source["url"]),
            {str(key): str(value) for key, value in headers.items()},
            fmt,
            client,
        )


class FederatedAcquirer:
    """Exchanges the subject token at Google's STS, then — where configured — impersonates the
    service account with the federated token."""

    def __init__(
        self, account: ExternalAccount, subject: SubjectToken, client: httpx.AsyncClient
    ) -> None:
        self._account = account
        self._subject = subject
        self._client = client

    async def _post(self, url: str, what: str, **request: Any) -> dict[str, Any]:
        try:
            response = await self._client.post(url, **request)
        except httpx.HTTPError as exc:
            found = from_transport(exc)
            raise CredentialsInvalid(
                f"{what} did not answer ({type(exc).__name__}).", found
            ) from exc
        if response.status_code != httpx.codes.OK:
            # Google's text is matched for its cause, never repeated (`diagnosis`).
            raise CredentialsInvalid(
                f"{what} refused with {response.status_code}.", from_response(response)
            )
        return _json(response)

    async def acquire(self, now: float) -> AccessToken:
        account = self._account
        exchanged = await self._post(
            account.token_url,
            "Google's token exchange (STS)",
            data={
                "grant_type": TOKEN_EXCHANGE_GRANT,
                "audience": account.audience,
                "scope": SCOPE,
                "requested_token_type": ACCESS_TOKEN_TYPE,
                "subject_token_type": account.subject_token_type,
                "subject_token": await self._subject.fetch(),
            },
        )
        federated = exchanged.get("access_token")
        if not isinstance(federated, str) or not federated:
            raise CredentialsInvalid("Google's token exchange returned no access token.")
        if not account.impersonation_url:
            # Direct resource access: the federated principal itself holds the role.
            return AccessToken(federated, expires_at=now + float(exchanged.get("expires_in", 3600)))

        impersonated = await self._post(
            account.impersonation_url,
            "The service account impersonation",
            json={"scope": [SCOPE], "lifetime": f"{account.lifetime_seconds}s"},
            headers={"Authorization": f"Bearer {federated}"},
        )
        token = impersonated.get("accessToken")
        if not isinstance(token, str) or not token:
            raise CredentialsInvalid("The service account impersonation returned no token.")
        return AccessToken(token, expires_at=now + _seconds_left(impersonated.get("expireTime")))


def _seconds_left(expire_time: Any) -> float:
    """How long an impersonated token lives, from its RFC 3339 `expireTime`; an hour when it says
    nothing readable, which is what Google issues by default."""
    try:
        moment = datetime.fromisoformat(str(expire_time).replace("Z", "+00:00"))
    except ValueError:
        return float(DEFAULT_LIFETIME_SECONDS)
    left = (moment - datetime.now(moment.tzinfo)).total_seconds()
    return left if left > 0 else float(DEFAULT_LIFETIME_SECONDS)


class MetadataServerAcquirer:
    """The token of the service account the machine runs as, from Google's metadata server."""

    def __init__(self, client: httpx.AsyncClient, url: str = METADATA_TOKEN_URL) -> None:
        self._client = client
        self._url = url

    async def acquire(self, now: float) -> AccessToken:
        try:
            response = await self._client.get(
                self._url, headers={"Metadata-Flavor": "Google"}, params={"scopes": SCOPE}
            )
        except httpx.HTTPError as exc:
            found = from_transport(exc)
            raise CredentialsInvalid(
                "The metadata server did not answer: this is not a machine on Google Cloud, or "
                f"it runs without a service account ({type(exc).__name__}).",
                found,
            ) from exc
        if response.status_code != httpx.codes.OK:
            raise CredentialsInvalid(
                f"The metadata server refused with {response.status_code}.",
                from_response(response),
            )
        payload = _json(response)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise CredentialsInvalid("The metadata server returned no access token.")
        return AccessToken(token, expires_at=now + float(payload.get("expires_in", 3600)))
