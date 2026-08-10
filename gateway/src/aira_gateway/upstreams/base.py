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
        #: Which adapter owns a **provider name**, so a model the catalog knows can be served
        #: without having been named in configuration as well (`FRD-507`).
        #:
        #: The configured list was the only way in, and it made the catalog a second place to type
        #: the same name: you declared a model in `AIRA_GEMINI_MODELS` so the adapter would offer
        #: it, then declared it again in the catalog so `FRD-307` would permit it. The catalog is
        #: already the authority on *what may be served* — a model in it names its provider, and
        #: that is enough to know who serves it.
        #:
        #: Configured models keep working unchanged; this is the fallback for one the catalog knows
        #: and the configuration does not.
        self._by_provider: dict[str, Upstream] = {}
        for provider in providers:
            claimed = getattr(provider, "serves_provider", "")
            if claimed:
                if claimed in self._by_provider:
                    raise AmbiguousModel(
                        f"Two adapters both claim provider '{claimed}'. A model catalogued under "
                        "it could be served by either, which decides its region and credential by "
                        "registration order — the same silent choice `ADR-0011` refuses."
                    )
                self._by_provider[claimed] = provider
            for model in provider.models():
                if model.name in self._by_model:
                    raise AmbiguousModel(
                        f"Model '{model.name}' is offered by both "
                        f"{type(self._by_model[model.name]).__name__} and "
                        f"{type(provider).__name__}. Configure it on exactly one."
                    )
                self._by_model[model.name] = provider
                self._models[model.name] = model

    def provenance_for(self, provider: str) -> tuple[str, str, str] | None:
        """Where an adapter that owns a provider name reaches its models (`FRD-507`).

        A model resolved through the **catalog** has no entry in `_models`, so the provenance the
        audit row needs cannot be read from there — and leaving it blank would be worse than the
        second list this feature removes: `FRD-115`'s point is that "the configuration says EU" is
        a claim and "this request went to `eu`" is evidence, and an empty column is neither.

        Read from one of the adapter's own models, because that is where the adapter put it. An
        adapter that owns a namespace and serves no configured model has nothing to answer with,
        and says so rather than guessing.
        """
        upstream = self._by_provider.get(provider)
        if upstream is None:
            return None
        for model in upstream.models():
            if model.provider:
                return (model.provider, model.publisher, model.region)
        declared = getattr(upstream, "provenance", None)
        return declared if isinstance(declared, tuple) else None

    def provider_for(self, model: str, provider: str = "") -> Upstream | None:
        """Which adapter serves this model.

        The configured name first, and a **catalogued** model's provider second. `provider` is what
        the catalog says (`ModelDeclaration.provider`); passing it is what lets a model become
        servable by being catalogued, without a second entry in configuration and without a
        restart. Callers that have no declaration to hand pass nothing and get the old behaviour.
        """
        direct = self._by_model.get(model)
        if direct is not None:
            return direct
        return self._by_provider.get(provider) if provider else None

    def models(self) -> list[UpstreamModel]:
        return list(self._models.values())

    def get_model(self, name: str) -> UpstreamModel | None:
        return self._models.get(name)


class DialectUnsupported(Exception):
    """The request asks for something this wire format cannot express faithfully.

    Raised at mapping time rather than dropped. It should be unreachable in practice — a model that
    cannot do a thing does not declare the capability, and `FRD-114` refuses the request before
    dispatch — so reaching it means a catalog entry claims something its dialect cannot deliver,
    which is exactly the state that must not fail quietly.

    **Lives here rather than in a dialect** (moved 2026-08-08). It was defined in the OpenAI
    mapping, which was fine while only that dialect raised it — and the moment `FRD-131` widened
    the part union, the Gemini and Anthropic adapters needed it too and would have imported it
    *from a sibling dialect*. That is the import the architecture assertion caught once before,
    with `to_json_schema`, and the answer is the same: a thing every dialect needs was never one
    dialect's to own.
    """
