"""Which identity the gateway presents to Google, decided once at start (`FRD-142`).

    service_account    a key — `AIRA_VERTEX_CREDENTIALS` or `AIRA_VERTEX_CREDENTIALS_FILE`
    external_account   no key: workload identity federation — the same two settings, holding
                       Google's `external_account` configuration, and optionally
                       `AIRA_VERTEX_FEDERATION_*` to fetch the identity from an OIDC provider
    metadata server    no key: `AIRA_VERTEX_METADATA_SERVER`, on Google Cloud

The JSON's own `type` decides between the first two, as it does for every Google client library; a
key without one is a service account, as keys issued before the field existed are.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from aira_common.tokens import TokenSource
from aira_gateway.config import GatewaySettings
from aira_gateway.upstreams.vertex.auth import (
    UNREADABLE_FILE_HINT,
    CredentialsInvalid,
    GoogleServiceAccountAcquirer,
    KeyFile,
    ServiceAccount,
)
from aira_gateway.upstreams.vertex.federation import (
    METADATA_PROBE_URL,
    ClientCredentialsSubjectToken,
    ExternalAccount,
    FederatedAcquirer,
    MetadataServerAcquirer,
)

#: How long the start waits to learn whether it runs on Google Cloud.
METADATA_PROBE_SECONDS = 1.0


def on_google_cloud(timeout: float = METADATA_PROBE_SECONDS) -> bool:
    """Whether this process runs on Google Cloud: the metadata server answers, as Google's.

    Asked directly, never through a proxy — the address only exists on the host — and briefly,
    because off Google Cloud the name does not resolve and the answer is no.
    """
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(METADATA_PROBE_URL, headers={"Metadata-Flavor": "Google"})
    except httpx.HTTPError:
        return False
    return response.status_code == httpx.codes.OK and (
        response.headers.get("Metadata-Flavor") == "Google"
    )


def configured(settings: GatewaySettings) -> bool:
    """Whether any credential for the platform is set — a key, a configuration or an API key."""
    return bool(
        settings.vertex_credentials or settings.vertex_credentials_file or settings.vertex_api_key
    )


def has_identity(settings: GatewaySettings) -> bool:
    """Whether any Google identity is configured — what registers the platform besides a key."""
    return bool(
        settings.vertex_credentials
        or settings.vertex_credentials_file
        or settings.vertex_metadata_server
    )


def _read(settings: GatewaySettings) -> tuple[str, dict[str, Any] | None]:
    """The configured JSON as text, and as an object where it is one."""
    raw = settings.vertex_credentials
    if settings.vertex_credentials_file:
        try:
            with open(settings.vertex_credentials_file, encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            raise CredentialsInvalid(
                f"The credentials file {settings.vertex_credentials_file} cannot be read "
                f"({type(exc).__name__}). {UNREADABLE_FILE_HINT}"
            ) from None
    try:
        data = json.loads(raw)
    except ValueError:
        return raw, None
    return raw, data if isinstance(data, dict) else None


def build_identity(settings: GatewaySettings, client: httpx.AsyncClient) -> TokenSource:
    """The token source for the configured identity. Raises `CredentialsInvalid` at start for
    anything that would fail at the first exchange."""
    federation = settings.vertex_federation_token_url
    if settings.vertex_metadata_server:
        if federation:
            raise CredentialsInvalid(
                "AIRA_VERTEX_FEDERATION_* has nothing to federate on the metadata server: the "
                "machine is the identity. Unset one of them."
            )
        return TokenSource(MetadataServerAcquirer(client))

    raw, data = _read(settings)
    if data is not None and data.get("type") == "external_account":
        account = ExternalAccount.from_dict(data, has_supplier=bool(federation))
        if federation:
            if not (
                settings.vertex_federation_client_id and settings.vertex_federation_client_secret
            ):
                raise CredentialsInvalid(
                    "AIRA_VERTEX_FEDERATION_TOKEN_URL is set without "
                    "AIRA_VERTEX_FEDERATION_CLIENT_ID and _CLIENT_SECRET: the gateway asks the "
                    "identity provider as a client, and needs to say which."
                )
            subject: Any = ClientCredentialsSubjectToken(
                federation,
                settings.vertex_federation_client_id,
                settings.vertex_federation_client_secret,
                client,
            )
        else:
            subject = account.subject(client)
        return TokenSource(FederatedAcquirer(account, subject, client))

    if federation:
        raise CredentialsInvalid(
            "AIRA_VERTEX_FEDERATION_TOKEN_URL is set, and the credentials are not an "
            "external_account configuration: federation needs one, from `gcloud iam "
            "workload-identity-pools create-cred-config`."
        )
    if data is not None and data.get("type") not in (None, "service_account"):
        raise CredentialsInvalid(
            f"Credentials of type '{data.get('type')}' are not used here: a service_account key "
            "or an external_account configuration."
        )
    source: ServiceAccount | KeyFile = (
        KeyFile(settings.vertex_credentials_file)
        if settings.vertex_credentials_file
        else ServiceAccount.from_json(raw)
    )
    return TokenSource(GoogleServiceAccountAcquirer(source, client))
