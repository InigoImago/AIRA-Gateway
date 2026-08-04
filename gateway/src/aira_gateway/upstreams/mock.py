"""Deterministic mock upstream provider for demo mode (FRD-002, basic).

Produces canned but plausible, fully deterministic completions/embeddings so the whole
system works end-to-end without real upstream credentials. Full fidelity (streaming,
richer shapes, latency simulation) is added in FRD-104.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MockCompletion:
    """A deterministic mock chat completion."""

    model: str
    content: str
    prompt_tokens: int
    completion_tokens: int


class MockUpstream:
    """A deterministic, offline mock upstream provider."""

    def __init__(self, model: str = "mock-1") -> None:
        self.model = model

    def complete(self, prompt: str) -> MockCompletion:
        """Return a deterministic completion for ``prompt``."""
        snippet = prompt.strip().replace("\n", " ")[:120]
        content = f"[mock:{self.model}] response to: {snippet}"
        return MockCompletion(
            model=self.model,
            content=content,
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(content.split()),
        )

    def embed(self, text: str, *, dimensions: int = 8) -> list[float]:
        """Return a deterministic pseudo-embedding of length ``dimensions``."""
        data = text.encode("utf-8")
        return [((sum(data[i::dimensions]) % 1000) / 1000.0) for i in range(dimensions)]
