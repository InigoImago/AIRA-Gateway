"""Reaching Vertex AI: endpoint, region, credential, errors (`FRD-115`).

Everything here is about Google *the platform* and nothing about a vendor's API shape; the dialects
own the bodies. Authentication in the adapters would be written twice, and body mapping here would
be rewritten for every new vendor (`ADR-0011`). How an answer is judged — status, reason, a body
that is not JSON — is every platform's rule and lives in `upstreams.exchange`.

**Residency is enforced, not intended**: `url()` checks every region, on every call, against the
one allow-list in :mod:`aira_gateway.residency` that every transport shares (`ADR-0012` §6).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx

from aira_common.logging import get_logger
from aira_common.tokens import TokenSource, TokenUnavailable
from aira_gateway.residency import DEFAULT_ALLOWED_REGIONS, check_region
from aira_gateway.upstreams.base import UpstreamError
from aira_gateway.upstreams.diagnosis import of as diagnosis_of
from aira_gateway.upstreams.exchange import (
    json_object,
    opened_stream,
    refused,
    unreachable,
)

#: How this transport names itself in an error: "Google Vertex AI returned 429." The platform's
#: own name, so a caller who reads it knows the answer was the provider's, not the gateway's.
NAME = "Google Vertex AI"

#: What a caller is told when this deployment's own credential fails. The cause — an expired key, a
#: clock off by minutes — is the operator's to fix and goes to the log; an API caller can do
#: nothing with it, and it describes this installation's credentials to whoever sent a request.
CREDENTIALS_UNAVAILABLE = (
    "Vertex credentials are unavailable, so the request could not be sent. The gateway's log has "
    "the cause for its operator."
)

#: The endpoints that are **not** `{region}-aiplatform.googleapis.com`. The regional pattern for
#: `global` resolves and answers `404`, which reads as "the model does not exist there" — and some
#: models are reachable only at `global`.
_MULTI_REGION_HOSTS = {
    "eu": "aiplatform.eu.rep.googleapis.com",
    "global": "aiplatform.googleapis.com",
}

#: Where Google serves the ranking API (`FRD-147`): the Discovery Engine, not Vertex. `global` has
#: the bare host; a multi-region has its own, as Google names them.
RANKING_HOST = "discoveryengine.googleapis.com"
#: The ranking configuration every project has; the model is named in the body.
RANKING_CONFIG = "default_ranking_config"

#: Where Agent Platform serves its managed open models (`FRD-115` FR-2a): one OpenAI-compatible path
#: for every publisher, the model named in the body rather than in the path.
OPEN_MODELS_PATH = "endpoints/openapi/chat/completions"

_log = get_logger("aira_gateway.upstreams")


def host_for(region: str) -> str:
    return _MULTI_REGION_HOSTS.get(region, f"{region}-aiplatform.googleapis.com")


class VertexTransport:
    """One project, one credential, any publisher."""

    def __init__(
        self,
        *,
        project: str,
        tokens: TokenSource | None = None,
        api_key: str = "",
        client: httpx.AsyncClient,
        allowed_regions: tuple[str, ...] = DEFAULT_ALLOWED_REGIONS,
        model_regions: Mapping[str, frozenset[str]] | None = None,
    ) -> None:
        if tokens is None and not api_key:
            # Refused here: a transport with no credential fails every call the same way and looks
            # like Google being down.
            raise ValueError(
                "VertexTransport needs a credential: a TokenSource for a Google identity "
                "(AIRA_VERTEX_CREDENTIALS, _CREDENTIALS_FILE or _METADATA_SERVER) or an API key "
                "(AIRA_VERTEX_API_KEY)."
            )
        self._project = project
        self._tokens = tokens
        self._api_key = api_key
        self._client = client
        self._allowed = allowed_regions
        #: Regions single models are permitted by name (`AIRA_ALLOWED_MODEL_REGIONS`).
        self._model_regions = dict(model_regions or {})

    def url(
        self, *, region: str, publisher: str, model: str, method: str, wire_model: str = ""
    ) -> str:
        """``model``'s URL for ``method`` in ``region``.

        ``model`` is the catalogue's name, and residency is checked for it — a region permitted by
        name is permitted to that entry (`ADR-0034`). ``wire_model`` is the name Google knows, where
        the entry is an alias of another model (`FRD-146` FR-10); the URL carries that one.
        """
        self._check(region, model)
        # **Percent-encoded as one segment**, so `/`, `..` or `%2f` in a model name cannot change
        # the path. `@` stays literal for Anthropic's `@version` suffix; RFC 3986 allows it.
        segment = quote(wire_model or model, safe="@")
        return (
            f"https://{host_for(region)}/v1/projects/{self._project}"
            f"/locations/{region}/publishers/{publisher}/models/{segment}:{method}"
        )

    def open_models_url(self, *, region: str, model: str) -> str:
        """The open-model endpoint in ``region``. Residency is checked for ``model`` exactly as
        `url` checks it: the model is named in the body, but it is still the model that goes."""
        self._check(region, model)
        return (
            f"https://{host_for(region)}/v1/projects/{self._project}"
            f"/locations/{region}/{OPEN_MODELS_PATH}"
        )

    def ranking_url(self, *, location: str, model: str) -> str:
        """The ranking API in ``location``. Residency is checked for ``model`` exactly as `url`
        checks it: the model is named in the body, but the records still go there."""
        self._check(location, model)
        host = RANKING_HOST if location == "global" else f"{location}-{RANKING_HOST}"
        return (
            f"https://{host}/v1/projects/{self._project}/locations/{location}"
            f"/rankingConfigs/{RANKING_CONFIG}:rank"
        )

    @property
    def project(self) -> str:
        """The project every call is made in and billed to."""
        return self._project

    def _check(self, region: str, model: str) -> None:
        check_region(region, self._allowed, model=model, for_models=self._model_regions)

    async def post(
        self, url: str, body: dict[str, Any], *, headers: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        """POST ``body``, with the credential and any ``headers`` a service asks for besides."""
        headers = {**await self._headers(), **(headers or {})}
        try:
            response = await self._client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise unreachable(exc, NAME) from exc
        if response.status_code != httpx.codes.OK:
            raise refused(response, NAME)
        return json_object(response, NAME)

    @asynccontextmanager
    async def stream(self, url: str, body: dict[str, Any]) -> AsyncIterator[httpx.Response]:
        """A streamed POST, its credential resolved and its status judged on entry."""
        headers = await self._headers()
        async with opened_stream(self._client, url, NAME, json=body, headers=headers) as response:
            yield response

    @property
    def tokens(self) -> TokenSource | None:
        """The Google identity's token source, or ``None`` on an API key — shared with the billing
        lookup (`FRD-509`), which only an OAuth identity may make."""
        return self._tokens

    async def aclose(self) -> None:
        """Close the connection pool. Called once, from the application lifespan."""
        await self._client.aclose()

    async def _headers(self) -> dict[str, str]:
        """The credential, in whichever form this deployment configured (`FRD-115` FR-3a).

        A service-account token that cannot be acquired is an **upstream** failure (503): the
        caller did nothing wrong, and a 4xx would send them to fix their own request (FR-9).
        """
        if self._api_key or self._tokens is None:
            # The API-key branch; the constructor refuses no key without tokens, and this spelling
            # lets the type checker follow.
            return {"x-goog-api-key": self._api_key, "Content-Type": "application/json"}
        try:
            token = await self._tokens.token()
        except TokenUnavailable as exc:
            _log.error("vertex_credentials_unavailable", error=str(exc))
            found = diagnosis_of(exc)
            said = f" Reason: {found.reason}." if found else ""
            raise UpstreamError(f"{CREDENTIALS_UNAVAILABLE}{said}", 503, found) from exc
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
