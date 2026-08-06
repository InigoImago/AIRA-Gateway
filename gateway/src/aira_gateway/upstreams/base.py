"""Upstream provider protocol and a small model registry (FRD-100).

Providers translate canonical requests to a concrete backend. In Phase 1 the only provider
is the deterministic mock; real adapters (Gemini Enterprise, Microsoft Foundry) arrive in
Phase 3 (FRD-304) implementing the same protocol.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
)


class UpstreamError(Exception):
    """A failure talking to an upstream provider.

    ``status_code`` is the upstream HTTP status when the provider answered (used by the
    routes to pass meaningful codes like 429/503 through to the client); it is ``None`` for
    transport-level failures where no response was received.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class UpstreamModel:
    """Metadata describing a model an upstream exposes.

    ``provider``/``publisher``/``region`` are the **provenance** (FRD-115 FR-10): under a residency
    requirement, "the configuration says EU" is a claim and "this request went to `eu`" is
    evidence. They are recorded on every audit row and span, so `FRD-601` can break down by them.
    """

    name: str
    version: str
    supported_methods: tuple[str, ...]
    provider: str = ""
    publisher: str = ""
    region: str = ""


@runtime_checkable
class Upstream(Protocol):
    """A provider AIRA can dispatch canonical requests to."""

    #: Which of `SAMPLING_CONTROLS` this provider's dialect can express (`FRD-124`).
    #:
    #: Declared per adapter and **never defaulted to "all"**. The same rule the catalog follows —
    #: undeclared means unsupported — for the same reason: a control silently dropped changes the
    #: answer and returns a 200. `test_every_adapter_declares_its_sampling_support` makes the
    #: omission a test failure rather than a quiet permission.
    sampling_controls: frozenset[str]

    def models(self) -> list[UpstreamModel]: ...

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse: ...

    def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]: ...

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]: ...

    """One call, one vector per submitted text, in the order submitted (`FRD-113` §5.1).

    A single text is a list of one. Adding a second method for batches instead would mean two code
    paths, and a batch metered on the single-text path is a rate limit with a hole in it — so the
    breaking change to this protocol was taken once, deliberately, rather than avoided.
    """


class AmbiguousModel(Exception):
    """Two providers offer the same model name.

    Raised at startup. With one adapter the old behaviour — last registration wins — was harmless;
    with three (Generative Language, Vertex Gemini, Vertex Anthropic) it becomes a **silent**
    decision about which region and which credential handled a request, and the wrong answer is
    invisible in every log and every report. Failing to boot is the correct response to an
    ambiguous routing table; a running gateway that sometimes leaves the EU is not.
    """


class ProviderRegistry:
    """Resolves model names to providers and lists available models."""

    def __init__(self, providers: list[Upstream]) -> None:
        self._by_model: dict[str, Upstream] = {}
        self._models: dict[str, UpstreamModel] = {}
        for provider in providers:
            for model in provider.models():
                if model.name in self._by_model:
                    raise AmbiguousModel(
                        f"Model '{model.name}' is offered by both "
                        f"{type(self._by_model[model.name]).__name__} and "
                        f"{type(provider).__name__}. Configure it on exactly one."
                    )
                self._by_model[model.name] = provider
                self._models[model.name] = model

    def provider_for(self, model: str) -> Upstream | None:
        return self._by_model.get(model)

    def models(self) -> list[UpstreamModel]:
        return list(self._models.values())

    def get_model(self, name: str) -> UpstreamModel | None:
        return self._models.get(name)
