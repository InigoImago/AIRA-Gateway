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
from aira_gateway.core.canonical import CanonicalChunk, CanonicalRequest, CanonicalResponse
from aira_gateway.upstreams.base import UpstreamError, UpstreamModel
from aira_gateway.upstreams.gemini_mapping import (
    canonical_to_gemini_request,
    gemini_chunk_to_canonical,
    gemini_response_to_canonical,
)

_METHODS = ("generateContent", "streamGenerateContent", "embedContent")


class GeminiUpstream:
    def __init__(self, api_key: str, models: list[str], client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._client = client
        self._models = [UpstreamModel(name, name, _METHODS) for name in models]

    def models(self) -> list[UpstreamModel]:
        return list(self._models)

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        data = await self._post(
            f"/models/{request.model}:generateContent", canonical_to_gemini_request(request)
        )
        return gemini_response_to_canonical(data, request.model)

    async def embed(self, model: str, text: str) -> list[float]:
        data = await self._post(
            f"/models/{model}:embedContent", {"content": {"parts": [{"text": text}]}}
        )
        return [float(value) for value in data.get("embedding", {}).get("values", [])]

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
    """Build the Gemini provider from settings, or None when no API key is configured."""
    if not settings.google_api_key:
        return None
    models = [name.strip() for name in settings.gemini_models.split(",") if name.strip()]
    client = httpx.AsyncClient(base_url=settings.gemini_base_url, timeout=60.0)
    return GeminiUpstream(settings.google_api_key, models, client)
