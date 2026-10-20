"""A model that takes no sampling control but at its neutral value (`FRD-145`).

Anthropic's Claude from Opus 4.7 on answers 400 to `top_k`, to a `temperature` other than 1 and
to a `top_p` below 0.99; the catalogue says which models those are (`fixed_sampling`, filled from
the reference list), and the dispatch chain moves past such a model for a request that sets one —
or refuses it, naming the control, where no other model is left.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.catalog import ModelCatalog, ModelDeclaration
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.apply import apply_event
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.db.models import ModelRead
from aira_gateway.pipeline.dispatch import dispatch_with_fallback
from aira_gateway.requirements import SamplingAccepted, permits, set_sampling
from aira_gateway.upstreams.base import ProviderRegistry
from aira_gateway.upstreams.mock import MockProvider


def _request(**sampling: Any) -> CanonicalRequest:
    return CanonicalRequest(
        model="fixed-1", messages=[CanonicalMessage(role=Role.USER, text="hi")], **sampling
    )


class _Catalog:
    async def declaration(self, model: str) -> ModelDeclaration:
        return ModelDeclaration(name=model, declared=True, fixed_sampling=model.startswith("fixed"))


@pytest.mark.parametrize(
    ("sampling", "named"),
    [
        ({"temperature": 0.2}, ["temperature 0.2"]),
        ({"top_p": 0.9}, ["top_p 0.9"]),
        ({"top_k": 40}, ["top_k 40"]),
        ({"temperature": 1.0, "top_p": 1.0}, []),
        ({}, []),
    ],
)
def test_only_a_value_other_than_the_neutral_one_is_a_setting(
    sampling: dict[str, Any], named: list[str]
) -> None:
    """KIRA sends `temperature: 1` on every request; that asks the model for nothing."""
    assert set_sampling(_request(**sampling)) == named


async def test_a_model_that_samples_at_its_own_settings_refuses_a_request_that_sets_them() -> None:
    refusal = await SamplingAccepted(_Catalog(), _request(temperature=0.2)).refusal("fixed-1")  # type: ignore[arg-type]

    assert refusal is not None and "temperature 0.2" in refusal
    assert await SamplingAccepted(_Catalog(), _request(temperature=0.2)).refusal("free-1") is None  # type: ignore[arg-type]


async def test_the_chain_moves_on_to_a_model_that_takes_them() -> None:
    registry = ProviderRegistry([MockProvider("fixed-1", "free-1")])
    request = _request(temperature=0.2)

    dispatched = await dispatch_with_fallback(
        registry,
        request,
        ("free-1",),
        permits=permits([SamplingAccepted(_Catalog(), request)]),  # type: ignore[arg-type]
    )

    assert dispatched.response.model == "free-1"
    assert [entry.model for entry in dispatched.skipped] == ["fixed-1"]


def _app():  # noqa: ANN202
    return create_app(GatewaySettings(auth_required=False, log_queue_size=0))


async def test_the_flag_travels_from_the_event_to_the_declaration() -> None:
    app = _app()
    with TestClient(app):
        async with app.state.db_sessionmaker() as session:
            await apply_event(
                session,
                "model.upserted",
                {"name": "mock-1", "capabilities": ["generate"], "fixed_sampling": True},
            )
            await session.commit()
        declared = await ModelCatalog(app.state.db_sessionmaker).declaration("mock-1")
        async with app.state.db_sessionmaker() as session:
            # An older Management sends no such field; it leaves the flag as it was.
            await apply_event(session, "model.upserted", {"name": "mock-1"})
            await session.commit()
        kept = await ModelCatalog(app.state.db_sessionmaker).declaration("mock-1")

    assert declared.fixed_sampling is True
    assert kept.fixed_sampling is True


async def test_a_caller_is_told_which_control_the_model_refuses() -> None:
    app = _app()
    with TestClient(app) as client:
        async with app.state.db_sessionmaker() as session:
            session.add(
                ModelRead(
                    model="mock-1",
                    capabilities=["generate"],
                    publisher="google",
                    fixed_sampling=True,
                )
            )
            await session.commit()

        def asking(**config: Any) -> Any:
            return client.post(
                "/v1beta/models/mock-1:generateContent",
                json={
                    "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                    "generationConfig": config,
                },
            )

        refused = asking(temperature=0.2)
        neutral = asking(temperature=1.0)

    assert refused.status_code == 400, refused.text
    assert "temperature 0.2" in refused.json()["error"]["message"]
    assert neutral.status_code == 200, neutral.text
