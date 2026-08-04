"""Upstream provider protocol and a small model registry (FRD-100).

Providers translate canonical requests to a concrete backend. In Phase 1 the only provider
is the deterministic mock; real adapters (Gemini Enterprise, Microsoft Foundry) arrive in
Phase 3 (FRD-304) implementing the same protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aira_gateway.core.canonical import CanonicalChunk, CanonicalRequest, CanonicalResponse


class UpstreamError(Exception):
    """A recoverable failure talking to an upstream provider (maps to a 502)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True, slots=True)
class UpstreamModel:
    """Metadata describing a model an upstream exposes."""

    name: str
    version: str
    supported_methods: tuple[str, ...]


@runtime_checkable
class Upstream(Protocol):
    """A provider AIRA can dispatch canonical requests to."""

    def models(self) -> list[UpstreamModel]: ...

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse: ...

    def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]: ...

    async def embed(self, model: str, text: str) -> list[float]: ...


class ProviderRegistry:
    """Resolves model names to providers and lists available models."""

    def __init__(self, providers: list[Upstream]) -> None:
        self._by_model: dict[str, Upstream] = {}
        self._models: dict[str, UpstreamModel] = {}
        for provider in providers:
            for model in provider.models():
                self._by_model[model.name] = provider
                self._models[model.name] = model

    def provider_for(self, model: str) -> Upstream | None:
        return self._by_model.get(model)

    def models(self) -> list[UpstreamModel]:
        return list(self._models.values())

    def get_model(self, name: str) -> UpstreamModel | None:
        return self._models.get(name)
