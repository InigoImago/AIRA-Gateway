"""Google AI Studio: the Generative Language API (`FRD-304`).

The HTTP client is injectable, so mapping and error handling are tested hermetically with a
``MockTransport``. The API key is sent as a query parameter and never logged.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from aira_common.models import ThinkingMode
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
)
from aira_gateway.residency import check_region, parse_allowed
from aira_gateway.upstreams.base import OfferedModel, UpstreamError, UpstreamModel
from aira_gateway.upstreams.gemini_mapping import (
    SAMPLING as GEMINI_SAMPLING,
)
from aira_gateway.upstreams.gemini_mapping import (
    batch_embedding_body,
    canonical_to_gemini_embedding,
    canonical_to_gemini_request,
    embedding_values,
    gemini_chunk_to_canonical,
    gemini_response_to_canonical,
)

_METHODS = (
    "generateContent",
    "streamGenerateContent",
    "embedContent",
    "batchEmbedContents",
)

#: The fallback for an empty `AIRA_GEMINI_BASE_URL`. Repeated from the settings default because the
#: adapter must not import the settings class's own default.
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

#: This endpoint in the residency vocabulary. It names no region and guarantees none — unlike
#: Vertex, where the region is in the URL — so it is `global`, which an EU allow-list does not
#: contain: a deployment has to name `global` to use it.
GENERATIVE_LANGUAGE_REGION = "global"

#: Listing page size, and a bound on pages: `nextPageToken` comes from the vendor, and a listing
#: that always returned one would hold the request open forever.
LISTING_PAGE_SIZE = 1000
MAX_LISTING_PAGES = 10

#: Which listed method means which capability. These are **facts**, which is what a catalog import
#: may pre-fill: the API answers 404 for a method a model does not list. What a model is *good* at
#: stays the administrator's to declare (`FRD-131`). Prompt caching appears only as
#: `createCachedContent` (`FRD-133`).
_GENERATE_METHODS = frozenset({"generateContent", "streamGenerateContent"})
_EMBED_METHODS = frozenset({"embedContent", "batchEmbedContents"})
_CACHE_METHODS = frozenset({"createCachedContent"})


def _offered_model(entry: dict[str, Any]) -> OfferedModel:
    """One entry of Google's listing, in the vendor-neutral shape a console can read.

    The ``models/`` prefix is stripped here: every other layer uses the bare name, and a prefixed
    catalog entry is a declaration no request can ever match. ``supportedGenerationMethods``
    present but empty is an answer (nothing); absent is not, so the capabilities stay ``None``.
    """
    methods = entry.get("supportedGenerationMethods")
    listed = frozenset(methods) if isinstance(methods, list) else None
    thinking = entry.get("thinking")
    return OfferedModel(
        name=str(entry.get("name", "")).removeprefix("models/"),
        display_name=str(entry.get("displayName") or ""),
        description=str(entry.get("description") or ""),
        max_output_tokens=_positive(entry.get("outputTokenLimit")),
        can_generate=None if listed is None else bool(listed & _GENERATE_METHODS),
        can_embed=None if listed is None else bool(listed & _EMBED_METHODS),
        can_cache_prompts=None if listed is None else bool(listed & _CACHE_METHODS),
        thinking=thinking if isinstance(thinking, bool) else None,
    )


def _positive(value: object) -> int | None:
    """A token limit, or nothing. A zero or a string is the vendor declining to say."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _unreachable(exc: httpx.HTTPError) -> UpstreamError:
    return UpstreamError(f"Gemini upstream error: {type(exc).__name__}.")


def _refused(response: httpx.Response) -> UpstreamError:
    return UpstreamError(f"Gemini upstream returned {response.status_code}.", response.status_code)


class GeminiUpstream:
    """Models reached through Google AI Studio with an API key."""

    #: What to call this upstream on a screen; the provider name is an identifier (`FRD-206`).
    platform_label = "Google AI Studio"
    #: The provider name this adapter owns (`FRD-507`): a model catalogued under it is served here
    #: without being named in `AIRA_GEMINI_MODELS`, since the endpoint takes the model in the URL.
    serves_provider = "generative-language"
    #: Where a catalogued model is reached, so its audit row is complete with an empty model list.
    provenance = (serves_provider, "google", GENERATIVE_LANGUAGE_REGION)
    #: The listing's ids are the names a caller uses (`FRD-507` stage C).
    enumerates = True
    sampling_controls = GEMINI_SAMPLING
    #: A token budget (`0` off, `-1` the model's choice), so every mode has a wire value.
    thinking_modes = frozenset(ThinkingMode)
    #: `thinkingLevel`. Whether a model takes it is a fact about the model, in its catalogue entry.
    expresses_thinking_levels = True
    #: A schema parameter and a tools field are separate here.
    tools_with_schema = True
    #: Google's own wire, which carries `responseModalities` and a voice (`FRD-624`).
    speaks = True

    def __init__(self, api_key: str, models: list[str], client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._client = client
        self._models = [
            UpstreamModel(
                name, name, _METHODS, "generative-language", "google", GENERATIVE_LANGUAGE_REGION
            )
            for name in models
        ]

    def models(self) -> list[UpstreamModel]:
        return list(self._models)

    async def available_models(self) -> list[OfferedModel]:
        """What this credential can reach, asked of Google.

        The listing is **paged**: reading only the first page would silently leave models out of
        the console's picker. Bounded by `MAX_LISTING_PAGES` because the vendor drives the loop.
        """
        offered: list[OfferedModel] = []
        page_token = ""
        for _ in range(MAX_LISTING_PAGES):
            params: dict[str, Any] = {"pageSize": LISTING_PAGE_SIZE}
            if page_token:
                params["pageToken"] = page_token
            data = await self._get("/models", params)
            offered.extend(_offered_model(entry) for entry in data.get("models") or [])
            page_token = str(data.get("nextPageToken") or "")
            if not page_token:
                break
        return offered

    async def ping(self, model: str = "", addressing: dict[str, str] | None = None) -> str:
        """The cheapest remote question there is (`FRD-117` §5.2): a GET of the listing."""
        count = len(await self.available_models())
        return f"{count} model(s) listed" if count else "endpoint answered"

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        data = await self._post(
            f"/models/{request.model}:generateContent", canonical_to_gemini_request(request)
        )
        return gemini_response_to_canonical(data, request.model)

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        if request.size > 1:
            data = await self._post(
                f"/models/{request.model}:batchEmbedContents",
                batch_embedding_body(request, request.model),
            )
        else:
            data = await self._post(
                f"/models/{request.model}:embedContent", canonical_to_gemini_embedding(request)
            )
        return embedding_values(data)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        body = canonical_to_gemini_request(request)
        try:
            async with self._client.stream(
                "POST",
                f"/models/{request.model}:streamGenerateContent",
                params={"key": self._api_key, "alt": "sse"},
                json=body,
            ) as response:
                if response.status_code != httpx.codes.OK:
                    raise _refused(response)
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        yield gemini_chunk_to_canonical(json.loads(line[len("data: ") :]))
        except httpx.HTTPError as exc:
            raise _unreachable(exc) from exc

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.get(path, params={**params, "key": self._api_key})
        except httpx.HTTPError as exc:
            raise _unreachable(exc) from exc
        if response.status_code != httpx.codes.OK:
            raise _refused(response)
        result: dict[str, Any] = response.json()
        return result

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, params={"key": self._api_key}, json=body)
        except httpx.HTTPError as exc:
            raise _unreachable(exc) from exc
        if response.status_code != httpx.codes.OK:
            raise _refused(response)
        result: dict[str, Any] = response.json()
        return result

    async def aclose(self) -> None:
        """Close the connection pool this adapter owns (`ProviderRegistry.aclose`)."""
        await self._client.aclose()


def build_gemini_upstream(settings: GatewaySettings) -> GeminiUpstream | None:
    """Build the Gemini provider from settings, or None when no API key is configured.

    Residency is checked at startup like every other adapter family (`FRD-115`): this endpoint is
    `global`, so a deployment must name `global` in `AIRA_ALLOWED_REGIONS` to use it, and one that
    does not refuses to start rather than sending data somewhere it did not permit.
    """
    if not settings.google_api_key:
        return None
    allowed = parse_allowed(settings.allowed_regions)
    check_region(GENERATIVE_LANGUAGE_REGION, allowed)
    models = [name.strip() for name in settings.gemini_models.split(",") if name.strip()]
    # Empty means the default, for this field: Compose passes optional variables as `${VAR:-}`,
    # and an empty string is not an endpoint (httpx would answer `UnsupportedProtocol`).
    base_url = settings.gemini_base_url or DEFAULT_GEMINI_BASE_URL
    client = httpx.AsyncClient(base_url=base_url, timeout=60.0)
    return GeminiUpstream(settings.google_api_key, models, client)
