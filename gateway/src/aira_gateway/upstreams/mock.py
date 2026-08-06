"""Deterministic mock upstream provider for demo mode (FRD-002 / FRD-100).

Produces canned but plausible, fully deterministic completions/embeddings so the whole
system works end-to-end without real upstream credentials. Richer fidelity (latency
simulation, tools, multimodal) arrives in FRD-104.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

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

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        prompt = request.last_user_text().strip().replace("\n", " ")[:120]
        words = f"[mock:{request.model}] response to: {prompt}{_attachments(request)}".split()

        finish_reason = "stop"
        limit = request.max_output_tokens
        if limit is not None and limit < len(words):
            words = words[:limit]
            finish_reason = "max_tokens"

        usage = CanonicalUsage(
            prompt_tokens=self._prompt_tokens(request),
            completion_tokens=len(words),
        )
        return CanonicalResponse(
            model=request.model, text=" ".join(words), finish_reason=finish_reason, usage=usage
        )

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        full = await self.generate(request)
        words = full.text.split()
        for start in range(0, len(words), _STREAM_WORDS_PER_CHUNK):
            delta = " ".join(words[start : start + _STREAM_WORDS_PER_CHUNK])
            yield CanonicalChunk(text_delta=f"{delta} ")
        yield CanonicalChunk(text_delta="", finish_reason=full.finish_reason, usage=full.usage)

    async def embed(self, model: str, text: str, *, dimensions: int = 8) -> list[float]:
        data = text.encode("utf-8")
        return [((sum(data[i::dimensions]) % 1000) / 1000.0) for i in range(dimensions)]

    @staticmethod
    def _prompt_tokens(request: CanonicalRequest) -> int:
        # Attachments cost input tokens that no character count predicts, and the mock has to say
        # so or every budget test against a document would measure a request that looked free.
        # 250 per 1 KiB is a coarse stand-in for what a provider actually charges — the point is
        # that it is not zero.
        attachment_tokens = sum(part.size // 4 for part in request.attachments)
        words = sum(len(message.text.split()) for message in request.messages)
        return words + attachment_tokens


def _attachments(request: CanonicalRequest) -> str:
    """Describe what was attached, deterministically.

    A mock that ignored attachments would let every hermetic test pass while the real path was
    broken — and the whole document feature would be exercised only against a cloud nobody has in
    CI. So the mock *sees* them, and says what it saw.
    """
    parts = request.attachments
    if not parts:
        return ""
    described = ", ".join(f"{part.media_type} ({part.size} bytes)" for part in parts)
    return f" [with {len(parts)} attachment(s): {described}]"
