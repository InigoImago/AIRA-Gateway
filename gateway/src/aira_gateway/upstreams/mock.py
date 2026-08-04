"""Deterministic mock upstream provider for demo mode (FRD-002 / FRD-100).

Produces canned but plausible, fully deterministic completions/embeddings so the whole
system works end-to-end without real upstream credentials. Richer fidelity (latency
simulation, tools, multimodal) arrives in FRD-104.
"""

from __future__ import annotations

from collections.abc import Iterator

from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.upstreams.base import UpstreamModel

_STREAM_WORDS_PER_CHUNK = 3


class MockProvider:
    """A deterministic, offline provider exposing a single ``mock-1`` model."""

    def __init__(self, model: str = "mock-1") -> None:
        self._model = UpstreamModel(
            name=model,
            version=model,
            supported_methods=("generateContent", "streamGenerateContent", "embedContent"),
        )

    def models(self) -> list[UpstreamModel]:
        return [self._model]

    def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        prompt = request.last_user_text().strip().replace("\n", " ")[:120]
        text = f"[mock:{request.model}] response to: {prompt}"
        usage = CanonicalUsage(
            prompt_tokens=self._prompt_tokens(request),
            completion_tokens=len(text.split()),
        )
        return CanonicalResponse(model=request.model, text=text, finish_reason="stop", usage=usage)

    def stream_generate(self, request: CanonicalRequest) -> Iterator[CanonicalChunk]:
        full = self.generate(request)
        words = full.text.split()
        for start in range(0, len(words), _STREAM_WORDS_PER_CHUNK):
            delta = " ".join(words[start : start + _STREAM_WORDS_PER_CHUNK])
            yield CanonicalChunk(text_delta=f"{delta} ")
        yield CanonicalChunk(text_delta="", finish_reason="stop", usage=full.usage)

    def embed(self, model: str, text: str, *, dimensions: int = 8) -> list[float]:
        data = text.encode("utf-8")
        return [((sum(data[i::dimensions]) % 1000) / 1000.0) for i in range(dimensions)]

    @staticmethod
    def _prompt_tokens(request: CanonicalRequest) -> int:
        return sum(len(message.text.split()) for message in request.messages)
