"""A model's sampling defaults, applied where a caller sets none (`FRD-146` FR-20).

Shipped empty: no value is sent on anybody's behalf unless an administrator declared it. Where one
is declared, the caller's own value still wins, and a dialect that cannot say the control is sent
nothing rather than refused over a value nobody asked for.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.catalog import ModelCatalog, ModelDeclaration
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.apply import apply_event
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse, CanonicalUsage
from aira_gateway.db.models import ModelRead
from aira_gateway.upstreams.base import ProviderRegistry
from aira_gateway.upstreams.mock import MockProvider


class _Seeing(MockProvider):
    """The mock, remembering what it was sent; ``sampling_controls`` narrowed where a test needs a
    dialect that cannot say one."""

    def __init__(self, *models: str, controls: frozenset[str] | None = None) -> None:
        super().__init__(*models)
        self.seen: list[CanonicalRequest] = []
        if controls is not None:
            self.sampling_controls = controls

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        self.seen.append(request)
        return CanonicalResponse(
            model=request.model,
            text="ok",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )


def _app(provider: _Seeing):  # noqa: ANN202
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([provider])
    return app


async def _catalogue(app, **fields: Any) -> None:  # noqa: ANN001
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model="mock-1", capabilities=["generate"], **fields))
        await session.commit()


def _ask(client: TestClient, **config: Any) -> Any:
    return client.post(
        "/v1beta/models/mock-1:generateContent",
        json={
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "generationConfig": config,
        },
    )


async def test_nothing_is_sent_where_nothing_is_declared() -> None:
    provider = _Seeing("mock-1")
    app = _app(provider)
    with TestClient(app) as client:
        await _catalogue(app)
        assert _ask(client).status_code == 200

    (sent,) = provider.seen
    assert (sent.top_p, sent.top_k) == (None, None)


async def test_a_declared_default_is_sent_where_the_caller_sets_none() -> None:
    provider = _Seeing("mock-1")
    app = _app(provider)
    with TestClient(app) as client:
        await _catalogue(app, default_sampling={"top_p": 0.8, "top_k": 40})
        assert _ask(client).status_code == 200

    (sent,) = provider.seen
    assert (sent.top_p, sent.top_k) == (0.8, 40)


async def test_the_callers_own_value_wins() -> None:
    provider = _Seeing("mock-1")
    app = _app(provider)
    with TestClient(app) as client:
        await _catalogue(app, default_sampling={"top_p": 0.8, "top_k": 40})
        assert _ask(client, topP=0.3).status_code == 200

    (sent,) = provider.seen
    assert (sent.top_p, sent.top_k) == (0.3, 40)


async def test_a_dialect_without_the_control_is_sent_none_and_still_serves() -> None:
    provider = _Seeing("mock-1", controls=frozenset({"top_p"}))
    app = _app(provider)
    with TestClient(app) as client:
        await _catalogue(app, default_sampling={"top_p": 0.8, "top_k": 40})
        response = _ask(client)

    assert response.status_code == 200, response.text
    (sent,) = provider.seen
    assert (sent.top_p, sent.top_k) == (0.8, None)


def test_a_stray_value_in_an_old_row_never_reaches_the_wire() -> None:
    declared = ModelDeclaration(name="m", default_sampling={"top_p": 7, "top_k": True, "seed": 1})

    assert declared.sampling_defaults() == {}


async def test_the_defaults_travel_from_the_event_to_the_declaration() -> None:
    app = _app(_Seeing("mock-1"))
    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            await apply_event(
                session,
                "model.upserted",
                {"name": "mock-1", "capabilities": ["generate"], "default_sampling": {"top_k": 40}},
            )
            await session.commit()
        declared = await ModelCatalog(app.state.db_sessionmaker).declaration("mock-1")

    assert declared.sampling_defaults() == {"top_k": 40}
