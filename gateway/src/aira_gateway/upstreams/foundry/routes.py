"""How Azure addresses a model, which is not by its name (FRD-120 §5.2).

Lives here rather than beside the dialect on purpose. The dialect is **platform-free** — that is
the property that let Foundry reuse it without a line of change, and `ADR-0011`'s justification
rests on it. A `Routes` implementation that names a cloud belongs to that cloud's package, and the
architecture assertion in ``test_vertex.py`` is what keeps the two apart: it parses every module
outside the platform packages and fails if a vendor appears in code.
"""

from __future__ import annotations

from urllib.parse import quote


class AzureRoutes:
    """Azure OpenAI: the deployment in the path, an API version, no model in the body.

    ``deployments`` maps the **caller-facing model name** to the deployment that serves it — the
    mapping `FRD-114` carries per catalog entry, so that a deployment migration is a catalog edit
    rather than a change to every use case's configuration.

    A model with no deployment raises rather than falling back to its own name. Azure would answer
    404 for a deployment that does not exist, which reads as "the model is gone" instead of "nobody
    told us where it lives", and sends whoever debugs it to the wrong system.
    """

    def __init__(self, deployments: dict[str, str], api_version: str) -> None:
        self._deployments = dict(deployments)
        self._api_version = api_version

    def _deployment(self, model: str) -> str:
        try:
            deployment = self._deployments[model]
        except KeyError:
            raise UnknownDeployment(
                f"No Azure deployment is configured for '{model}'. The catalog's addressing is "
                "where that is declared; without it the request would reach a 404 that reads as a "
                "missing model."
            ) from None
        # Encoded: a deployment name is chosen by whoever created the resource, and Azure permits
        # characters that would otherwise change the path.
        return quote(deployment, safe="")

    def chat(self, model: str) -> str:
        return (
            f"/openai/deployments/{self._deployment(model)}/chat/completions"
            f"?api-version={self._api_version}"
        )

    def embed(self, model: str) -> str:
        return (
            f"/openai/deployments/{self._deployment(model)}/embeddings"
            f"?api-version={self._api_version}"
        )

    def body_model(self, model: str) -> str | None:
        # The path already named the deployment. Azure ignores a body `model`, and sending one
        # would put a *caller-facing* name on the wire where a reader would take it for the
        # deployment — two different strings that look like one field.
        return None


class UnknownDeployment(Exception):
    """A model this platform has no addressing for. A startup or configuration mistake."""
