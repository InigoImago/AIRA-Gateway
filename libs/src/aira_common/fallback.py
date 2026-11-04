"""Which model answers when the one asked for cannot (`FRD-310`, `ADR-0052`).

A use case keeps one chain **per kind of request** — text generation, embedding, ranking — in its
pipeline, each empty unless somebody fills it: a model that cannot answer is then the caller's
error. Both planes read this vocabulary: Management validates what is saved against it, and the
gateway resolves the chain a request is served by.

**A kind is a capability every candidate must declare.** A generation model has no vector to give,
an embedding model no text and a reranker neither; a candidate of another kind is passed over by
name at every hop, never called.
"""

from __future__ import annotations

from enum import StrEnum

from aira_common.models import Capability


class FallbackKind(StrEnum):
    """A kind of model access a use case keeps a chain for, named by the capability every
    candidate of that chain must declare."""

    GENERATE = "generate"
    EMBED = "embed"
    RERANK = "rerank"

    @property
    def capability(self) -> Capability:
        return Capability(self.value)


#: The pipeline field that holds a use case's chain for each kind. Text generation keeps the name
#: its chain has always had (`FRD-302`); the other two are named for what they stand in for.
CHAIN_FIELDS: dict[FallbackKind, str] = {
    FallbackKind.GENERATE: "fallback_models",
    FallbackKind.EMBED: "embedding_fallback_models",
    FallbackKind.RERANK: "ranking_fallback_models",
}

#: What a model must declare to stand in for a request of each kind. Speech counts as generation:
#: a spoken answer is a `generateContent` request, and the gateway asks a speech model of the
#: requests that want one (`SpeechSupported`) and passes it over for every other.
SERVES: dict[FallbackKind, frozenset[Capability]] = {
    FallbackKind.GENERATE: frozenset({Capability.GENERATE, Capability.SPEECH}),
    FallbackKind.EMBED: frozenset({Capability.EMBED}),
    FallbackKind.RERANK: frozenset({Capability.RERANK}),
}


def can_serve(capabilities: frozenset[Capability], kind: FallbackKind) -> bool:
    """Whether a model declaring ``capabilities`` may stand in for a request of ``kind``.

    The caller passes what the gateway would read — a model nobody declared has the baseline,
    generation and embedding, and nothing more (`FRD-114` FR-7).
    """
    return bool(capabilities & SERVES[kind])
