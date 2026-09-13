"""How Azure addresses a model, which is not by its name (`FRD-120` §5.2).

Here rather than beside the dialect: the dialect is **platform-free**, which is what let Foundry
reuse it unchanged (`ADR-0011`). The architecture assertion in ``test_vertex.py`` keeps vendor
names inside the platform packages.
"""

from __future__ import annotations

from urllib.parse import quote


class UnknownDeployment(Exception):
    """A model this platform has no addressing for. A startup or configuration mistake."""


class AzureRoutes:
    """Azure OpenAI: the deployment in the path, an API version, no model in the body.

    ``deployments`` maps the **caller-facing model name** to the deployment that serves it, so a
    deployment migration is a catalog edit, not a change to every use case (`FRD-114`). A model
    with no deployment raises rather than falling back to its own name: Azure's 404 for a missing
    deployment reads as "the model is gone" instead of "nobody told us where it lives".
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

    def listing(self) -> str:
        # The *resource's* listing, not a deployment's: a readiness verdict must not depend on
        # which deployment was asked, and one cold deployment is not an unreachable resource.
        return f"/openai/models?api-version={self._api_version}"

    def names_models(self) -> bool:
        """No: this listing answers which models the resource *could* run (`FRD-507` stage C).

        Each needs a deployment before any request can reach it, so an imported entry would be
        catalogued, approved, priced — and answer 404 on its first request.
        """
        return False

    def body_model(self, model: str) -> str | None:
        # The path already names the deployment. A body `model` would put a caller-facing name on
        # the wire where a reader would take it for the deployment.
        return None
