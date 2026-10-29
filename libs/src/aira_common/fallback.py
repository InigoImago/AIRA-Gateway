"""Which model answers when the one asked for cannot (`FRD-310`, `ADR-0048`).

The installation keeps one chain **per kind of request** — text generation, embedding, ranking —
and a use case decides what it does with them. Both planes read this vocabulary: Management
validates what is saved against it, and the gateway resolves the chain a request is served by.

**A kind is a capability every candidate must declare.** A generation model has no vector to give,
an embedding model no text and a reranker neither; a candidate of another kind is passed over by
name at every hop, never called.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from aira_common.models import Capability


class FallbackKind(StrEnum):
    """A kind of model access an installation keeps a chain for, named by the capability every
    candidate of that chain must declare."""

    GENERATE = "generate"
    EMBED = "embed"
    RERANK = "rerank"

    @property
    def capability(self) -> Capability:
        return Capability(self.value)


class FallbackMode(StrEnum):
    """What a use case does with the installation's chains.

    Three answers, so a value rather than a flag (`LESSONS.md` §1): *the installation's*, *my own
    for text generation*, and *none at all* — which is what a use case says that must never be
    answered by another model than the one it asked for.
    """

    #: The installation's chain for each kind — empty, and so no fallback, until somebody sets one.
    DEFAULT = "default"
    #: The use case's own chain for text generation; embedding and ranking keep the installation's.
    OWN = "own"
    #: No fallback for any kind: a model that cannot answer is the caller's error.
    NONE = "none"


#: The words a stored mode may hold, for code that reads one before it is parsed.
MODES = frozenset(member.value for member in FallbackMode)

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


def stored_mode(value: object, own_chain: Sequence[object]) -> FallbackMode:
    """The mode a stored or published value means.

    **Absent is not a word.** A pipeline from before modes existed says nothing, and means what it
    meant then: its own chain where it names one, else nothing of its own — the installation's,
    which is empty until set. A word this build does not know is read as `none`, the reading that
    substitutes no model: a newer mode must not widen what an older gateway sends a request to.
    """
    if value is None:
        return FallbackMode.OWN if any(own_chain) else FallbackMode.DEFAULT
    if isinstance(value, str) and value in MODES:
        return FallbackMode(value)
    return FallbackMode.NONE


def chain_for(
    kind: FallbackKind,
    mode: FallbackMode,
    own_chain: Sequence[str],
    installation_chain: Sequence[str],
) -> tuple[str, ...]:
    """The models a request of ``kind`` falls back to, in order, under ``mode``.

    One function for both planes, so the console's account of a use case's chain and the chain its
    requests are served by cannot drift apart.
    """
    if mode is FallbackMode.NONE:
        return ()
    if mode is FallbackMode.OWN and kind is FallbackKind.GENERATE:
        return tuple(own_chain)
    return tuple(installation_chain)
