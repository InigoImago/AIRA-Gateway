"""Reaching Vertex AI: endpoint, region, credential, errors (FRD-115).

Everything here is about *Google the platform* and nothing about a vendor's API shape. The dialects
above it (`FRD-119` for Anthropic, the Gemini mappers for Google) own the bodies. Getting that seam
right is the whole design: put authentication in the adapters and it is written twice; put body
mapping in the transport and adding a third vendor rewrites it.

**Residency is enforced, not intended.** A configuration that can express a non-EU region is a
configuration in which somebody eventually adds one — because that is where a preview model
launched — and nothing objects. So the allowed regions are a list, a model outside it refuses to
start, and every request records where it went.

The list itself is **not** Vertex's: it lives in :mod:`aira_gateway.residency` and every transport
is measured against the same one. "Which regions may we use" is one policy question with a
vendor-specific vocabulary, and a per-cloud list would mean a per-cloud audit (`ADR-0012` §6).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from aira_common.tokens import TokenSource, TokenUnavailable
from aira_gateway.residency import DEFAULT_ALLOWED_REGIONS, check_region
from aira_gateway.upstreams.base import UpstreamError

#: The multi-region endpoint has its own host rather than a `{region}-` prefix.
_MULTI_REGION_HOSTS = {"eu": "aiplatform.eu.rep.googleapis.com"}


def host_for(region: str) -> str:
    return _MULTI_REGION_HOSTS.get(region, f"{region}-aiplatform.googleapis.com")


class VertexTransport:
    """One project, one credential, any publisher."""

    def __init__(
        self,
        *,
        project: str,
        tokens: TokenSource,
        client: httpx.AsyncClient,
        allowed_regions: tuple[str, ...] = DEFAULT_ALLOWED_REGIONS,
    ) -> None:
        self._project = project
        self._tokens = tokens
        self._client = client
        self._allowed = allowed_regions

    def url(self, *, region: str, publisher: str, model: str, method: str) -> str:
        check_region(region, self._allowed)
        # **Percent-encoded, one segment.** This used to read `httpx.URL(path=f"/{model}").path`
        # under a comment claiming the segment was encoded. It was not: that call leaves `/` and
        # `..` untouched and *decodes* `%2f`, so `..%2f..%2fx` came out as `../../x` — worse than
        # the input it was given. Two gates stand in front of it today (`FRD-307`: only a
        # catalogued, approved model dispatches), which is exactly the argument this project
        # refuses elsewhere, and the comment made the next reader trust a protection that was not
        # there.
        #
        # `@` stays literal because an Anthropic model id carries an `@version` suffix
        # (`claude-sonnet-4-5@20250929`) and RFC 3986 allows it in a path. Everything else,
        # including `/`, is encoded — the same thing `AzureRoutes` does with a deployment name,
        # which is where this should have been copied from in the first place.
        segment = quote(model, safe="@")
        return (
            f"https://{host_for(region)}/v1/projects/{self._project}"
            f"/locations/{region}/publishers/{publisher}/models/{segment}:{method}"
        )

    async def _headers(self) -> dict[str, str]:
        try:
            token = await self._tokens.token()
        except TokenUnavailable as exc:
            # An upstream failure, never a client error: the caller did nothing wrong, and a 4xx
            # would send them off to fix their own request (FR-9).
            raise UpstreamError(f"Vertex credentials unavailable: {exc}", 503) from exc
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

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


class _StreamContext:
    """A streamed POST that resolves its Authorization header on entry."""

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
            raise UpstreamError(f"Vertex transport error: {type(exc).__name__}.") from exc
        _raise_for_status(response)
        return response

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(*exc_info)


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code != httpx.codes.OK:
        # The status is preserved so the route's existing 429/503/504 pass-through keeps working
        # across every vendor. The body is not echoed: a Vertex error can quote the request.
        raise UpstreamError(
            f"Vertex upstream returned {response.status_code}.", response.status_code
        )
