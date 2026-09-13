"""A catalogued embedding model is sent where the catalogue says (`FRD-507`), and one with nowhere
to be sent is refused and recorded rather than answered with a 500 (`FRD-115`, `FRD-122`)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalEmbeddingRequest
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.upstreams.base import AmbiguousModel, ProviderRegistry, UpstreamModel

_ADDRESSING = {"regions": ["europe-west1"]}
_EMBED = {"content": {"parts": [{"text": "hallo"}]}}


class _RegionalEmbedder:
    """An adapter that, like Vertex, serves catalogued models and cannot send one without its
    addressing."""

    serves_provider = "regional"

    def __init__(self, *, addressable: bool = True) -> None:
        self.addressable = addressable
        self.seen: list[CanonicalEmbeddingRequest] = []

    def models(self) -> list[UpstreamModel]:
        return []

    async def embed(self, request: CanonicalEmbeddingRequest) -> list[list[float]]:
        self.seen.append(request)
        if not self.addressable or not request.addressing:
            raise AmbiguousModel(f"'{request.model}' is catalogued and says no region.")
        return [[0.5, 0.5] for _ in request.texts]


def _app(provider: _RegionalEmbedder):  # noqa: ANN202
    app = create_app(
        GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="europe-west1")
    )
    app.state.providers = ProviderRegistry([provider])
    return app


async def _catalogue(app) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(
            ModelRead(
                model="emb-1",
                provider="regional",
                numeric_id=7,
                capabilities=["embed"],
                embedding={"default": 2, "dimensions": [2], "supports_batch": True},
                addressing=_ADDRESSING,
                approved=True,
            )
        )
        await session.commit()


async def test_a_catalogued_embedding_model_is_sent_where_the_catalogue_says() -> None:
    provider = _RegionalEmbedder()
    app = _app(provider)
    with TestClient(app) as client:
        await _catalogue(app)
        response = client.post("/v1beta/models/emb-1:embedContent", json=_EMBED)

    assert response.status_code == 200, response.text
    assert provider.seen[0].addressing == _ADDRESSING


async def test_a_model_with_nowhere_to_be_sent_is_refused_and_recorded() -> None:
    """A configuration fault an operator can fix, on both surfaces — and never a 500 without a
    row: the request was attributed, so the audit must say what became of it."""
    app = _app(_RegionalEmbedder(addressable=False))
    with TestClient(app) as client:
        await _catalogue(app)
        gemini = client.post("/v1beta/models/emb-1:embedContent", json=_EMBED)
        kira = client.post("/kira/api/external/embed", json={"text": "hallo", "model_id": 7})
        async with app.state.db_sessionmaker() as session:
            rows = list(
                (await session.execute(select(RequestLog).order_by(RequestLog.api))).scalars()
            )

    assert gemini.status_code == 400, gemini.text
    assert gemini.json()["error"]["status"] == "FAILED_PRECONDITION"
    assert kira.status_code == 400, kira.text
    assert kira.json()["code"] == "MODEL_NOT_FOUND"
    assert [(row.api, row.outcome) for row in rows] == [
        ("gemini", "no_capable_model"),
        ("kira", "no_capable_model"),
    ]
