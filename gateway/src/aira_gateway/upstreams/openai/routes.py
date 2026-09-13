"""How a platform addresses a model in the OpenAI dialect — `ADR-0011`'s third axis.

The dialect owns the body and the transport owns reaching the endpoint; this owns **model
identity**. A plain endpoint takes ``POST /v1/chat/completions`` with the model in the body. A
platform that addresses by **path** puts its own identifier there and leaves the body's model out;
its implementation lives in that platform's package, never here, so the dialect stays reusable.

Why the indirection (`FRD-120` §5.2): were a deployment name allowed to be the model name, pricing
would break quietly — `FRD-403` prices by model, and a deployment called `production` has no price.
"""

from __future__ import annotations

from typing import Protocol


class Routes(Protocol):
    """How one platform addresses chat and embedding, and whether the body names the model."""

    def chat(self, model: str) -> str: ...

    def embed(self, model: str) -> str: ...

    def body_model(self, model: str) -> str | None:
        """The value for the body's ``model`` field, or ``None`` to omit it entirely."""
        ...

    def listing(self) -> str:
        """A path that is cheap to GET and proves the endpoint is answering (`FRD-117` §5.2)."""
        ...

    def names_models(self) -> bool:
        """Whether that listing's ids are names a **caller** could use (`FRD-507` stage C).

        On a plain endpoint they are. On a platform that addresses by path each listed model needs
        a deployment first, and importing one would produce a catalog entry that looks complete and
        is unreachable (`FRD-506`). Declared per platform, never defaulted.
        """
        ...


class StandardRoutes:
    """The plain form: one path, the model in the body. Ollama, direct OpenAI, a NIM endpoint."""

    CHAT = "/v1/chat/completions"
    EMBED = "/v1/embeddings"

    def chat(self, model: str) -> str:
        return self.CHAT

    def embed(self, model: str) -> str:
        return self.EMBED

    def body_model(self, model: str) -> str | None:
        return model

    def listing(self) -> str:
        return "/v1/models"

    def names_models(self) -> bool:
        # `{"data": [{"id": "qwen3:0.6b"}]}` — the id is exactly what a caller puts in a request.
        return True
