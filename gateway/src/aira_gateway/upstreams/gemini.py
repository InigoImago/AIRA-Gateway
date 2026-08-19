"""Real Google Gemini upstream provider (FRD-304).

Calls the Generative Language API (``generativelanguage.googleapis.com/v1beta``). The HTTP
client is injectable so the mapping/error handling is hermetically tested with a
``MockTransport``; the API key is sent as a query param and never logged.
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


#: Where Google AI Studio lives. Repeated from the settings default on purpose: this is the value
#: an empty configuration falls back to, and the fallback has to exist somewhere the adapter can
#: reach without importing the settings class's own default.
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

#: What the Google AI Studio endpoint is, in the residency vocabulary.
#:
#: `generativelanguage.googleapis.com` names no region and gives no regional guarantee — which is
#: precisely the difference from Vertex, where the region is in the URL and the data stays in it.
#: Calling that `global` is the honest answer, and it is a value an EU allow-list does not contain,
#: so a deployment has to say `global` out loud to use it.
GENERATIVE_LANGUAGE_REGION = "global"

#: How many entries to ask for per listing page, and how many pages to accept.
#:
#: The ceiling is not tuning, it is a bound on a loop somebody else drives: `nextPageToken` comes
#: from the vendor, and a listing that always returns one would hold the request open forever. Ten
#: pages of a thousand is far beyond any credential's catalogue and still finite.
LISTING_PAGE_SIZE = 1000
MAX_LISTING_PAGES = 10

#: Which listed method means which capability.
#:
#: These are **facts rather than claims**, which is the distinction that decides what a catalog
#: import may pre-fill: the API answers 404 for a method a model does not list, so the list is the
#: interface. What a model is *good* at — tools, structured output, attachments — is a measurement
#: and stays the administrator's to declare (`FRD-131` found a model advertising `tools` that
#: returns the JSON as prose).
#:
#: `createCachedContent` is how prompt caching appears. The word "caching" is nowhere in the
#: response, so an implementation reading the obvious field finds nothing and declares no caching
#: for a model that has it (`FRD-133`).
_GENERATE_METHODS = frozenset({"generateContent", "streamGenerateContent"})
_EMBED_METHODS = frozenset({"embedContent", "batchEmbedContents"})
_CACHE_METHODS = frozenset({"createCachedContent"})


def _offered_model(entry: dict[str, Any]) -> OfferedModel:
    """One entry of Google's listing, in the vendor-neutral shape a console can read.

    The ``models/`` prefix is stripped here, at the edge where Google's resource form stops. Every
    other layer — the catalog, the audit row, the caller's own request — uses the bare name, and a
    prefixed entry reaching the catalog is a declaration **no request can ever match** while
    looking perfectly right in the table.

    ``supportedGenerationMethods`` present but empty is still an answer (nothing is supported);
    absent is not, which is why the capabilities stay ``None`` in that case.
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


class GeminiUpstream:
    #: The provider name this adapter owns (`FRD-507`). A model catalogued under it is served here
    #: even when nobody named it in `AIRA_GEMINI_MODELS` — the endpoint takes the model name in the
    #: URL and needs no list of its own, so the configured list was only ever a second place to
    #: type what the catalog already says.
    #: What to call this upstream on a screen. The provider *name* is an identifier — it goes in
    #: the catalog, on the audit row and into routing — and `generative-language` tells a reader
    #: nothing about which vendor they are choosing. Declared per adapter rather than mapped in the
    #: console, because a second vocabulary in TypeScript is one more thing to forget (`FRD-206`).
    platform_label = "Google AI Studio"

    serves_provider = "generative-language"

    #: Where this adapter reaches its models, for a catalogued model that names no configured one
    #: (`FRD-507`). The same three values every `UpstreamModel` here carries — stated once so the
    #: audit row is complete even when the configured list is empty, which is the whole point of
    #: cataloguing being enough.
    provenance = (serves_provider, "google", GENERATIVE_LANGUAGE_REGION)

    def __init__(self, api_key: str, models: list[str], client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._client = client
        self._models = [
            UpstreamModel(
                name, name, _METHODS, "generative-language", "google", GENERATIVE_LANGUAGE_REGION
            )
            for name in models
        ]

    sampling_controls = GEMINI_SAMPLING
    #: A token budget: `0` off, `-1` the model's choice, otherwise a count — so every mode in the
    #: vocabulary has a wire value, including `limited`.
    thinking_modes = frozenset(ThinkingMode)
    #: A schema parameter and a tools field are separate here.
    tools_with_schema = True

    def models(self) -> list[UpstreamModel]:
        return list(self._models)

    #: This endpoint publishes a listing whose ids are the names a caller uses (`FRD-507` stage C).
    enumerates = True

    async def available_models(self) -> list[OfferedModel]:
        """What this credential can actually reach, asked of Google rather than typed by hand.

        The listing is **paged**, and one key here answered with 50 entries. A loop that read only
        the first page would leave models out of the console's picker with nothing on screen saying
        anything had been cut off — the same silence as a truncated fallback chain, and an
        administrator would conclude their key does not include the model they are looking for.

        Bounded anyway, because the loop is driven by a token the *vendor* controls: an unbounded
        remote loop is not a slow response, it is one that never arrives.
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
        """The cheapest remote question there is (`FRD-117` §5.2).

        A **GET of the listing**, never a generation. This adapter had none at all until the
        listing existed, so `/readyz` and `FRD-506`'s reachability check both reported it as
        *unprobed* — honestly, but with nothing behind the honesty.
        """
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
                    raise UpstreamError(
                        f"Gemini upstream returned {response.status_code}.",
                        response.status_code,
                    )
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        yield gemini_chunk_to_canonical(json.loads(line[len("data: ") :]))
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Gemini upstream error: {type(exc).__name__}.") from exc

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.get(path, params={**params, "key": self._api_key})
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Gemini upstream error: {type(exc).__name__}.") from exc
        if response.status_code != httpx.codes.OK:
            raise UpstreamError(
                f"Gemini upstream returned {response.status_code}.", response.status_code
            )
        result: dict[str, Any] = response.json()
        return result

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, params={"key": self._api_key}, json=body)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"Gemini upstream error: {type(exc).__name__}.") from exc
        if response.status_code != httpx.codes.OK:
            raise UpstreamError(
                f"Gemini upstream returned {response.status_code}.", response.status_code
            )
        result: dict[str, Any] = response.json()
        return result

    async def aclose(self) -> None:
        """Close the connection pool this adapter owns (`ProviderRegistry.aclose`)."""
        await self._client.aclose()


def build_gemini_upstream(settings: GatewaySettings) -> GeminiUpstream | None:
    """Build the Gemini provider from settings, or None when no API key is configured.

    **Residency is checked here too, as of 2026-08-10.** It was not, and this was the one adapter
    family of four that was not: Vertex, the OpenAI servers and Foundry all measure their region
    against `AIRA_ALLOWED_REGIONS` at startup, and this one declared `global` on every model —
    honestly, so it reached the audit row — while nothing compared it to the policy.

    That is the shape this project keeps naming: an **enforced control that one path bypasses**,
    the same as `:embedContent` skipping the pre-dispatch gate. The record was right and the
    control was absent, which is worse than a control that is missing everywhere, because the
    evidence says the deployment is compliant.

    `FRD-115`'s rule applies unchanged: a model in a region this deployment does not permit is a
    **startup** failure. A gateway that sometimes leaves the EU is not a smaller problem than one
    that will not start — it is the same problem, discovered later and by somebody else.

    AI Studio remains entirely usable, and deliberately: name `global` in `AIRA_ALLOWED_REGIONS`.
    That turns "may we send data there" from something a person remembers into a line in the
    configuration and a region on every audit row.
    """
    if not settings.google_api_key:
        return None
    allowed = parse_allowed(settings.allowed_regions)
    check_region(GENERATIVE_LANGUAGE_REGION, allowed)
    models = [name.strip() for name in settings.gemini_models.split(",") if name.strip()]
    # **Empty means "use the default", for this field only.** Compose passes optional variables as
    # `${VAR:-}`, which expands to an empty string, and `_empty_means_unset` deliberately leaves
    # `str` fields alone — there an empty value is often a real answer (`AIRA_CORS_ORIGINS=` means
    # none). A base URL is not one of those: the empty string is not an endpoint, it is the absence
    # of one, and passing it produced `UnsupportedProtocol` from httpx — an upstream error message
    # about our own configuration. Same rule as the Vault provisioning found earlier the same day:
    # absent and empty are different answers, and here only one of them can be meant.
    base_url = settings.gemini_base_url or DEFAULT_GEMINI_BASE_URL
    client = httpx.AsyncClient(base_url=base_url, timeout=60.0)
    return GeminiUpstream(settings.google_api_key, models, client)
