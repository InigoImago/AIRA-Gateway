"""Reaching Vertex AI: endpoint, region, credential, errors (`FRD-115`).

Everything here is about Google *the platform* and nothing about a vendor's API shape; the dialects
own the bodies. Authentication in the adapters would be written twice, and body mapping here would
be rewritten for every new vendor (`ADR-0011`).

**Residency is enforced, not intended**: `url()` checks every region, on every call, against the
one allow-list in :mod:`aira_gateway.residency` that every transport shares (`ADR-0012` §6).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from aira_common.tokens import TokenSource, TokenUnavailable
from aira_gateway.residency import DEFAULT_ALLOWED_REGIONS, check_region
from aira_gateway.upstreams.base import UpstreamError, upstream_reason

#: The endpoints that are **not** `{region}-aiplatform.googleapis.com`. The regional pattern for
#: `global` resolves and answers `404`, which reads as "the model does not exist there" — and some
#: models are reachable only at `global`.
_MULTI_REGION_HOSTS = {
    "eu": "aiplatform.eu.rep.googleapis.com",
    "global": "aiplatform.googleapis.com",
}


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
    ) -> None:
        if tokens is None and not api_key:
            # Refused here: a transport with no credential fails every call the same way and looks
            # like Google being down.
            raise ValueError(
                "VertexTransport needs a credential: a service-account TokenSource "
                "(AIRA_VERTEX_CREDENTIALS) or an API key (AIRA_VERTEX_API_KEY)."
            )
        self._project = project
        self._tokens = tokens
        self._api_key = api_key
        self._client = client
        self._allowed = allowed_regions

    def url(self, *, region: str, publisher: str, model: str, method: str) -> str:
        check_region(region, self._allowed)
        # **Percent-encoded as one segment**, so `/`, `..` or `%2f` in a model name cannot change
        # the path. `@` stays literal for Anthropic's `@version` suffix; RFC 3986 allows it.
        segment = quote(model, safe="@")
        return (
            f"https://{host_for(region)}/v1/projects/{self._project}"
            f"/locations/{region}/publishers/{publisher}/models/{segment}:{method}"
        )

    async def post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        headers = await self._headers()
        try:
            response = await self._client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Vertex transport error: {type(exc).__name__}.") from exc
        _raise_for_status(response)
        result: dict[str, Any] = response.json()
        return result

    def stream(self, url: str, body: dict[str, Any]) -> _StreamContext:
        return _StreamContext(self, url, body)

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
            raise UpstreamError(f"Vertex credentials unavailable: {exc}", 503) from exc
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


class _StreamContext:
    """A streamed POST that resolves its credential and judges the status on entry."""

    def __init__(self, transport: VertexTransport, url: str, body: dict[str, Any]) -> None:
        self._transport = transport
        self._url = url
        self._body = body
        self._cm: Any = None

    async def __aenter__(self) -> httpx.Response:
        headers = await self._transport._headers()
        self._cm = self._transport._client.stream(
            "POST", self._url, json=self._body, headers=headers
        )
        try:
            response: httpx.Response = await self._cm.__aenter__()
        except httpx.HTTPError as exc:
            self._cm = None
            raise UpstreamError(f"Vertex transport error: {type(exc).__name__}.") from exc
        try:
            _raise_for_status(response)
        except UpstreamError:
            # Close what was opened: Python does not call `__aexit__` when `__aenter__` raises, and
            # under regional failover a refused stream is an ordinary step, not a rare end.
            await self._cm.__aexit__(None, None, None)
            self._cm = None
            raise
        return response

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(*exc_info)


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code != httpx.codes.OK:
        # The status is kept so 429/503/504 pass through for every vendor. The provider's reason
        # is carried for a 400 only, and never the body: a Vertex error can quote the request.
        detail = upstream_reason(response) if response.status_code == 400 else ""
        raise UpstreamError(
            f"Vertex upstream returned {response.status_code}.{detail}", response.status_code
        )
