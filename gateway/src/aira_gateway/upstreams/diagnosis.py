"""Why a provider refused, or could not be reached, in words an operator can act on (`FRD-141`).

A refusal names its cause in a structured form on every platform this gateway reaches: Google sends
`error.status` and a `google.rpc.ErrorInfo` whose `reason` is one word from a published list
(`SERVICE_DISABLED`, `IAM_PERMISSION_DENIED`, …); an OAuth token endpoint sends `error`; Azure and
OpenAI-dialect servers send `error.code`. A connection that fails says why in its exception chain —
a certificate, a name, a proxy, a timeout. Reporting the HTTP status alone sends whoever sets up a
model to guess between an API that is not enabled, a missing role, a model not offered in the
region and a proxy that inspects TLS.

**Only the structured fields are read, never the provider's free text.** Words from a closed list,
and metadata values of a bounded identifier shape (a project, a permission, a service), carry no
credential and no request content; a provider's prose can quote the URL it was called with, and on
Google AI Studio that URL carries the key. The explanation is ours, written from those fields.

Two readers, two depths. The **reason word** travels with the caller's error message: it tells a
developer that the fault is the installation's, and which one to report. The **explanation** —
project numbers, permissions, what to change — is for the operator: the model check, `/readyz` and
the log.
"""

from __future__ import annotations

import re
import socket
import ssl
from dataclasses import dataclass
from typing import Any

import httpx

#: The longest reason word read from a provider. Google's are under 40 characters.
MAX_REASON_LENGTH = 64

#: What a reason word may look like: letters, digits and underscores. Anything else is not one of
#: the published words and is not repeated.
_REASON = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

#: What a metadata value may look like to be repeated: an identifier — a project, a service, a
#: permission, a resource path. No spaces, no query string, no `=`, bounded.
_IDENTIFIER = re.compile(r"^[A-Za-z0-9._/:@-]{1,200}$")

#: The `ErrorInfo.metadata` keys an explanation may quote. Each names *where* the refusal applies;
#: none can hold a credential.
_METADATA_KEYS = frozenset(
    {"service", "consumer", "permission", "resource", "quota_metric", "quota_limit", "location"}
)

#: Google's OAuth token endpoint states the cause of a refused grant in `error_description`, from a
#: small fixed vocabulary. Matched, never repeated: the substring decides the reason word.
_GRANT_FAILURES: tuple[tuple[str, str, str], ...] = (
    (
        "invalid jwt signature",
        "KEY_NOT_ACCEPTED",
        "Google does not accept the service account's key: it was deleted or disabled, or the key "
        "belongs to another account. Create a new key for the account, or use keyless access.",
    ),
    (
        "account not found",
        "ACCOUNT_NOT_FOUND",
        "The service account named in the credential does not exist, or is disabled.",
    ),
    (
        "reasonable timeframe",
        "CLOCK_SKEW",
        "Google refused the token request's time window: this machine's clock is off. Synchronise "
        "it (NTP); a few minutes' drift is enough to be refused.",
    ),
    (
        "invalid_scope",
        "SCOPE_REFUSED",
        "Google refused the requested scope for this account.",
    ),
    # Google's Security Token Service, for workload identity federation (`FRD-142`).
    (
        "does not match the expected audience",
        "AUDIENCE_MISMATCH",
        "Google's token exchange refused the identity provider's token: its audience (aud) is not "
        "one the workload identity provider allows. In Keycloak, add an audience mapper to the "
        "gateway's client with the provider's allowed audience, or allow the client's audience "
        "on the provider.",
    ),
    (
        "rejected by the attribute condition",
        "ATTRIBUTE_CONDITION_REJECTED",
        "Google's token exchange refused the token: the workload identity provider's attribute "
        "condition does not admit it. Compare the condition with the token's claims (for a "
        "Keycloak client, azp is its client id).",
    ),
    (
        "error connecting to the given credential's issuer",
        "ISSUER_UNREACHABLE",
        "Google cannot fetch the identity provider's keys from its issuer address. Upload the "
        "realm's JWKS to the workload identity provider instead, and upload it again whenever "
        "the realm's keys rotate.",
    ),
    (
        "signature",
        "SIGNATURE_NOT_VERIFIED",
        "Google could not verify the identity provider's token signature: the keys uploaded to "
        "the workload identity provider are not the realm's current ones. Upload its JWKS again.",
    ),
    (
        "expired",
        "SUBJECT_TOKEN_EXPIRED",
        "The identity provider's token had expired when Google received it; check the clocks.",
    ),
    (
        "org policy violated",
        "ORG_POLICY_VIOLATED",
        "An organisation policy (iam.workloadIdentityPoolProviders) does not allow this identity "
        "provider.",
    ),
    # Keycloak, or any OIDC provider, refusing the gateway as a client.
    (
        "invalid client",
        "CLIENT_REJECTED",
        "The identity provider refused the gateway's client: a wrong client id or secret.",
    ),
    (
        "not enabled to retrieve service account",
        "SERVICE_ACCOUNTS_DISABLED",
        "The identity provider's client may not use the client-credentials grant. In Keycloak, "
        "turn on Service accounts roles for the client.",
    ),
)


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """One cause: a reason word from a closed list, and what an operator does about it."""

    reason: str
    explanation: str

    def __str__(self) -> str:
        return f"{self.reason}: {self.explanation}"


def _metadata(info: dict[str, Any]) -> dict[str, str]:
    raw = info.get("metadata")
    if not isinstance(raw, dict):
        return {}
    return {
        key: str(value)
        for key, value in raw.items()
        if key in _METADATA_KEYS and isinstance(value, str) and _IDENTIFIER.match(value)
    }


def _google_explanation(reason: str, meta: dict[str, str]) -> str | None:
    """Ours, for the `ErrorInfo` reasons that whoever connects a Google platform meets."""
    service = meta.get("service", "the API")
    project = meta.get("consumer", "the project").removeprefix("projects/")
    permission = meta.get("permission")
    explanations = {
        "SERVICE_DISABLED": (
            f"The API {service} is not enabled in project {project}. Enable it in the Google "
            "Cloud console under APIs & Services, wait a minute, and check again."
        ),
        "IAM_PERMISSION_DENIED": (
            (
                f"The federated identity may not act as the service account ({permission}). "
                "Grant its principal the role Workload Identity User "
                "(roles/iam.workloadIdentityUser) on the service account."
            )
            if permission and permission.startswith("iam.serviceAccounts.")
            else (
                f"The gateway's Google identity may not read the billing account ({permission}). "
                "Grant it Billing Account Viewer (roles/billing.viewer) on that billing account — "
                "a billing account's own IAM, not the project's."
            )
            if permission and permission.startswith("billing.")
            else (
                (f"The credential lacks the permission {permission}. " if permission else "")
                + "Grant the service account the role Vertex AI User (roles/aiplatform.user) on "
                "the project. A model may also be missing in this region, which Google reports "
                "the same way."
            )
        ),
        "BILLING_DISABLED": f"Billing is not enabled for project {project}.",
        "CONSUMER_INVALID": (
            f"Project {project} does not exist, or the credential cannot see it. Check "
            "AIRA_VERTEX_PROJECT: it is the project id, not its name."
        ),
        "USER_PROJECT_DENIED": (
            f"The credential may not use project {project}: it needs serviceusage.services.use "
            "there, which the role Service Usage Consumer grants."
        ),
        "API_KEY_INVALID": (
            "The API key is not valid: deleted, mistyped, or issued for another platform."
        ),
        "API_KEY_SERVICE_BLOCKED": (
            f"The API key is restricted and may not call {service}. Allow that API in the key's "
            "restrictions."
        ),
        "API_KEY_IP_ADDRESS_BLOCKED": (
            "The API key's restrictions refuse this gateway's outbound address."
        ),
        "API_KEY_HTTP_REFERRER_BLOCKED": (
            "The API key is restricted to browser referrers; a server cannot use it."
        ),
        "ACCESS_TOKEN_SCOPE_INSUFFICIENT": (
            "The access token lacks the cloud-platform scope the call needs."
        ),
        "ACCESS_TOKEN_EXPIRED": "The access token had expired when it arrived; check the clock.",
        "RATE_LIMIT_EXCEEDED": (
            f"A quota is exhausted ({meta.get('quota_metric', 'requests')}). Raise it in the "
            "Google Cloud console under IAM & Admin → Quotas, or spread load over regions."
        ),
        "RESOURCE_EXHAUSTED": (
            "Google has no capacity or quota left for this model here right now. Retry later, "
            "raise the quota, or add another region."
        ),
    }
    return explanations.get(reason)


#: Google's `error.status` where it came without an `ErrorInfo`, which is how a model that is not
#: there is reported.
_GOOGLE_STATUS: dict[str, str] = {
    "NOT_FOUND": (
        "The platform has no such model at this address. Check the model id, that it is offered "
        "in this region (not every model is offered in every region), and — for a model of another "
        "publisher than Google — that it is enabled in Model Garden for the project."
    ),
    "PERMISSION_DENIED": (
        "The credential was accepted and may not do this. Grant the service account the role "
        "Vertex AI User (roles/aiplatform.user); for a partner model, enable it in Model Garden."
    ),
    "UNAUTHENTICATED": (
        "The credential was not accepted: a key that was revoked or mistyped, or a token for "
        "another audience."
    ),
    "RESOURCE_EXHAUSTED": (
        "A quota or the platform's capacity is exhausted for this model here. Retry later, raise "
        "the quota, or add another region."
    ),
    "FAILED_PRECONDITION": (
        "The platform refused because something must be set up first — for a partner model, "
        "accepting its terms in Model Garden; otherwise billing or the project's location."
    ),
}

#: Whatever any platform means by a bare status, for an answer that said nothing more.
_BY_STATUS: dict[int, tuple[str, str]] = {
    401: ("UNAUTHENTICATED", "The provider did not accept the credential."),
    403: (
        "PERMISSION_DENIED",
        "The provider accepted the credential and refused it this call: a missing role or a "
        "restricted key.",
    ),
    404: (
        "NOT_FOUND",
        "The provider has no such model or deployment at this address. Check the name and the "
        "region or endpoint it is reached at.",
    ),
    429: (
        "RATE_LIMITED",
        "The provider's quota or rate limit is exhausted for this credential.",
    ),
}


def _error_object(body: Any) -> Any:
    """The `error` member, through Agent Platform's one-element list."""
    if isinstance(body, list) and body:
        body = body[0]
    return body.get("error") if isinstance(body, dict) else None


def from_body(body: Any, status: int) -> Diagnosis | None:
    """Read a refusal's body for its cause. ``None`` when it states none this module knows."""
    error = _error_object(body)
    if isinstance(error, str):
        return _from_grant(error, body.get("error_description") if isinstance(body, dict) else "")
    if not isinstance(error, dict):
        return _from_status(status)

    details = error.get("details")
    for info in details if isinstance(details, list) else []:
        if not isinstance(info, dict) or not str(info.get("@type", "")).endswith("ErrorInfo"):
            continue
        reason = str(info.get("reason", ""))
        if not _REASON.match(reason):
            continue
        meta = _metadata(info)
        explanation = _google_explanation(reason, meta)
        if explanation is None:
            explanation = (
                _GOOGLE_STATUS.get(str(error.get("status", "")), "")
                or (_BY_STATUS.get(status, ("", "The provider refused."))[1])
            )
        return Diagnosis(reason, explanation)

    google_status = str(error.get("status", ""))
    if google_status in _GOOGLE_STATUS:
        return Diagnosis(google_status, _GOOGLE_STATUS[google_status])

    # Azure and the OpenAI dialect: `code` (`DeploymentNotFound`, `model_not_found`) or `type`.
    for key in ("code", "type"):
        word = error.get(key)
        if isinstance(word, str) and _REASON.match(word):
            fallback = _from_status(status)
            # Repeated as the provider spells it: `DeploymentNotFound` is what its documentation
            # and its support find.
            return Diagnosis(
                word[:MAX_REASON_LENGTH],
                fallback.explanation if fallback else "The provider refused.",
            )
    return _from_status(status)


def _from_grant(error: str, description: Any) -> Diagnosis | None:
    """An OAuth token endpoint's refusal: `error`, and a description matched, never repeated."""
    text = str(description or "").lower()
    for marker, reason, explanation in _GRANT_FAILURES:
        if marker in text or marker == error.lower():
            return Diagnosis(reason, explanation)
    if not _REASON.match(error):
        return None
    return Diagnosis(
        error.upper(),
        "The identity provider refused to issue an access token for this credential.",
    )


def _from_status(status: int) -> Diagnosis | None:
    known = _BY_STATUS.get(status)
    return Diagnosis(*known) if known else None


def from_response(response: httpx.Response) -> Diagnosis | None:
    """`from_body` for an answer whose body may not be JSON, or may not have been read."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 — an unreadable body states no cause beyond its status
        return _from_status(response.status_code)
    return from_body(body, response.status_code)


def _chain(exc: BaseException) -> list[BaseException]:
    """The exception and everything it was raised from, the way a traceback reads them."""
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _host(exc: BaseException) -> str:
    """The host a failed exchange was addressed to — never the path or the query, which can carry a
    key."""
    try:
        request = getattr(exc, "request", None)
    except RuntimeError:  # httpx raises when an exception carries no request
        return "the provider"
    host = getattr(getattr(request, "url", None), "host", "") if request is not None else ""
    return str(host) if host and _IDENTIFIER.match(str(host)) else "the provider"


def from_transport(exc: BaseException) -> Diagnosis | None:
    """Why no answer came: a certificate, a name, a proxy, a timeout. ``None`` for anything else."""
    host = _host(exc)
    chain = _chain(exc)
    if any(isinstance(link, ssl.SSLCertVerificationError) for link in chain):
        return Diagnosis(
            "CERTIFICATE_NOT_TRUSTED",
            f"The certificate {host} presented is not trusted here. Behind a proxy that inspects "
            "TLS, point SSL_CERT_FILE at a bundle that holds your organisation's root and the "
            "public roots, and restart.",
        )
    if any(isinstance(link, httpx.ProxyError) for link in chain):
        return Diagnosis(
            "PROXY_REFUSED",
            f"The proxy refused the connection to {host}. Check HTTPS_PROXY and the proxy's "
            "allow list.",
        )
    if any(isinstance(link, socket.gaierror) for link in chain):
        return Diagnosis(
            "NAME_NOT_RESOLVED",
            f"{host} does not resolve from here. Check DNS, or whether outbound traffic must go "
            "through a proxy (HTTPS_PROXY).",
        )
    if any(isinstance(link, httpx.ConnectTimeout) for link in chain):
        return Diagnosis(
            "CONNECT_TIMEOUT",
            f"No connection to {host} could be opened in time: a firewall that drops the "
            "traffic, or a proxy that must be named in HTTPS_PROXY.",
        )
    if any(isinstance(link, httpx.ConnectError) for link in chain):
        return Diagnosis(
            "CONNECTION_FAILED",
            f"The connection to {host} failed: refused, reset, or the TLS handshake broke off.",
        )
    if any(isinstance(link, httpx.ReadTimeout) for link in chain):
        return Diagnosis(
            "READ_TIMEOUT",
            f"{host} accepted the connection and did not answer in time.",
        )
    return None


def of(exc: BaseException) -> Diagnosis | None:
    """The diagnosis an exception carries anywhere in its chain, if any."""
    for link in _chain(exc):
        found = getattr(link, "diagnosis", None)
        if isinstance(found, Diagnosis):
            return found
    return None
