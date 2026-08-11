"""Where a request goes, which is not the same question as what it says (ADR-0011).

The dialect owns the body. The transport owns reaching the cloud. This owns the third axis:
**model identity** — the caller names a model, and the platform's addressing is configuration.

Two platforms speaking one dialect address it in two incompatible ways:

- A plain OpenAI-compatible endpoint takes ``POST /v1/chat/completions`` with ``{"model": …}`` in
  the body. The model *is* the name.
- A platform that addresses by **path** puts its own identifier there and leaves the body's model
  field out. `FRD-120`'s is the first; its implementation lives in that platform's package, not
  here, because this file is the *dialect's* — and a dialect that names a platform is one the next
  platform cannot reuse.

`FRD-120` §5.2 is explicit about why the indirection earns its place, and the reason is not
tidiness. If a deployment name were allowed to be the model name, then every use case's pipeline
config would embed Azure resource naming — and **pricing would break quietly**: `FRD-403` prices by
model, a deployment called `production` has no price, and unpriced traffic is counted apart rather
than as zero. The spend figure would simply stop being complete, with nothing failing.
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

        The third axis again, and the reason it is a question at all. On a plain endpoint the
        listing's ids *are* the model names: read one, catalogue it, call it. On a platform that
        addresses by path, the same listing names models the resource could run — each of which
        needs a deployment created for it before any request can reach it, and the deployment name
        is what the addressing carries. Importing from there produces a catalog entry that looks
        complete and is unreachable, which is `FRD-506`'s "listed is not usable" with the catalog
        vouching for it.

        Declared per platform, never defaulted: undeclared would have to mean *yes* for the
        protocol to stay silent about it, and this file's whole subject is that a platform's
        addressing must be stated rather than assumed.
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
