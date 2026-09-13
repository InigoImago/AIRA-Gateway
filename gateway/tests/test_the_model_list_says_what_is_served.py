"""The model lists say what a request may name, and which verbs it may use (`FRD-507`, `FRD-114`).

Built from configuration alone, both lists omitted a catalogued model that was served, and offered
every verb an adapter has — `embedContent` on a model declared to generate only, which is refused.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel


class _CatalogueOnly:
    """An adapter that, like Vertex, serves models configuration does not name."""

    serves_provider = "regional"

    def models(self) -> list[UpstreamModel]:
        return []


def _app():  # noqa: ANN202
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([*app.state.providers.each(), _CatalogueOnly()])
    return app


async def _declare(app, model: str, **fields: Any) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=model, **fields))
        await session.commit()


async def test_a_catalogued_model_is_listed_with_the_verbs_it_declares() -> None:
    app = _app()
    with TestClient(app) as client:
        await _declare(
            app,
            "emb-1",
            provider="regional",
            numeric_id=7,
            capabilities=["embed"],
            embedding={"default": 2, "dimensions": [2], "supports_batch": True},
            approved=True,
        )
        await _declare(app, "chat-1", provider="regional", numeric_id=8, approved=False)
        await _declare(app, "mock-1", capabilities=["generate"])
        listed = {m["name"]: m for m in client.get("/v1beta/models").json()["models"]}
        one = client.get("/v1beta/models/emb-1")
        kira = client.get("/kira/api/external/models").json()

    assert listed["models/emb-1"]["supportedGenerationMethods"] == [
        "embedContent",
        "batchEmbedContents",
    ]
    assert "models/chat-1" not in listed, "an unapproved model would be refused, so not offered"
    assert listed["models/mock-1"]["supportedGenerationMethods"] == [
        "generateContent",
        "streamGenerateContent",
    ], "a model declared to generate only must not offer the embedding verb it would refuse"
    assert one.status_code == 200, one.text
    assert [model["id"] for model in kira] == [7]
