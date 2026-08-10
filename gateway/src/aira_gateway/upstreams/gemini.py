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

from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
)
from aira_gateway.residency import check_region, parse_allowed
from aira_gateway.upstreams.base import UpstreamError, UpstreamModel
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


class GeminiUpstream:
    #: The provider name this adapter owns (`FRD-507`). A model catalogued under it is served here
    #: even when nobody named it in `AIRA_GEMINI_MODELS` — the endpoint takes the model name in the
    #: URL and needs no list of its own, so the configured list was only ever a second place to
    #: type what the catalog already says.
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
    #: A schema parameter and a tools field are separate here.
    tools_with_schema = True

    def models(self) -> list[UpstreamModel]:
        return list(self._models)

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
