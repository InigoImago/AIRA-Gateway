"""An audit row names where a request was served and what it cost, however the model was reached
(`FRD-115` FR-10, `FRD-403`).

Three gaps a live round found: a served row named the configured region although the adapter had
reported the one that answered; a model reached through the catalogue, on a platform with two
publishers, named no provider at all; and an embedding was never priced.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalEmbeddingRequest,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    EmbeddingVectors,
)
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

_PROMPT = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
_EMBED = {"content": {"parts": [{"text": "hi"}]}}


class _Side:
    """One publisher of a platform that hosts two, as Vertex hosts `google` and `anthropic`."""

    #: A test double, like `MockProvider` (`FRD-307`): the models it serves are invented.
    is_test_double = True
    serves_provider = "regional"
    sampling_controls: frozenset[str] = frozenset()

    def __init__(self, publisher: str, configured: list[UpstreamModel] | None = None) -> None:
        self.serves_publisher = publisher
        self._configured = configured or []

    def models(self) -> list[UpstreamModel]:
        return self._configured

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        # Answered by the second region the catalogue lists: the first had failed over.
        return CanonicalResponse(
            model=request.model,
            text="ok",
            usage=CanonicalUsage(prompt_tokens=2, completion_tokens=1),
            served_region="europe-west4",
        )

    async def embed(self, request: CanonicalEmbeddingRequest) -> EmbeddingVectors:
        return EmbeddingVectors(
            [[0.5, 0.5] for _ in request.texts], served_region="europe-west3", input_tokens=5
        )


def _app():  # noqa: ANN202
    app = create_app(
        GatewaySettings(
            auth_required=False,
            log_queue_size=0,
            allowed_regions="europe-west1,europe-west3,europe-west4",
        )
    )
    configured = UpstreamModel(
        "conf-1", "conf-1", ("generateContent",), "regional", "google", "europe-west1"
    )
    # The `anthropic` side last, so a lookup by provider name alone finds it — and it serves none
    # of these models.
    app.state.providers = ProviderRegistry(
        [*app.state.providers.each(), _Side("google", [configured]), _Side("anthropic")]
    )
    return app


async def _rows(app) -> dict[tuple[str, str], RequestLog]:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        rows = (await session.execute(select(RequestLog))).scalars()
        return {(row.model, row.operation): row for row in rows}


async def test_each_row_names_the_publisher_and_the_region_that_answered() -> None:
    app = _app()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(
                    model="cat-1",
                    provider="regional",
                    publisher="google",
                    capabilities=["generate", "embed"],
                    embedding={"default": 2, "dimensions": [2], "supports_batch": True},
                    addressing={"regions": ["europe-west1", "europe-west4"]},
                    approved=True,
                    input_price_per_million_nanos=1_000_000_000,
                    output_price_per_million_nanos=1_000_000_000,
                )
            )
            await session.commit()
        for path, body in (
            ("conf-1:generateContent", _PROMPT),
            ("cat-1:generateContent", _PROMPT),
            ("cat-1:embedContent", _EMBED),
        ):
            response = client.post(f"/v1beta/models/{path}", json=body)
            assert response.status_code == 200, (path, response.text)
        rows = await _rows(app)

    def where(model: str, operation: str) -> tuple[str | None, str | None, str | None]:
        row = rows[(model, operation)]
        return (row.provider, row.publisher, row.region)

    # Configured in europe-west1, answered in europe-west4: the row names where it went.
    assert where("conf-1", "generateContent") == ("regional", "google", "europe-west4")
    # Reached through the catalogue, on a platform with a second publisher.
    assert where("cat-1", "generateContent") == ("regional", "google", "europe-west4")
    assert where("cat-1", "embedContent") == ("regional", "google", "europe-west3")


async def test_an_embedding_is_priced_by_the_tokens_its_adapter_reported() -> None:
    app = _app()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(
                    model="cat-1",
                    provider="regional",
                    publisher="google",
                    capabilities=["embed"],
                    embedding={"default": 2, "dimensions": [2], "supports_batch": True},
                    addressing={"regions": ["europe-west1"]},
                    approved=True,
                    input_price_per_million_nanos=1_000_000_000,
                    output_price_per_million_nanos=1_000_000_000,
                )
            )
            await session.commit()
        response = client.post("/v1beta/models/cat-1:embedContent", json=_EMBED)
        assert response.status_code == 200, response.text
        row = (await _rows(app))[("cat-1", "embedContent")]

    assert row.prompt_tokens == 5
    assert row.cost_nanos, "an embedding with reported tokens and a price on file was unpriced"
