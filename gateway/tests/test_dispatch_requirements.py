"""A fallback chain may not degrade a request silently (ADR-0012 §3).

The rule is stated for attachments and applies to every property of a candidate that changes what
comes back — a model that cannot read the PDF, one that cannot enforce the schema, one in a region
this request may not use. Falling back to any of those produces no error. It produces a fluent,
confident answer, with a 200, indistinguishable from a correct one.

So: skipped, with the reason kept; and when nothing qualifies, **failed** — as a precondition
problem an operator can fix, not as the 502 it used to be, which read as "the provider is down".
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalMessage,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Role,
)
from aira_gateway.db.models import RequestLog
from aira_gateway.pipeline.dispatch import NoCapableModel, dispatch_with_fallback
from aira_gateway.requirements import RegionAllowed, permits
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


def _request(model: str) -> CanonicalRequest:
    return CanonicalRequest(model=model, messages=[CanonicalMessage(role=Role.USER, text="hi")])


class _Provider:
    """Serves the given models, each optionally in a declared region."""

    def __init__(self, *models: tuple[str, str]) -> None:
        self._models = models

    def models(self) -> list[UpstreamModel]:
        return [
            UpstreamModel(name, name, ("generateContent",), "vertex", "google", region)
            for name, region in self._models
        ]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=request.model,
            text=f"answered by {request.model}",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, model, text):  # noqa: ANN001, ANN201
        raise NotImplementedError


# -- the condition ---------------------------------------------------------------------------


async def test_a_candidate_outside_the_permitted_regions_is_skipped_not_used() -> None:
    """The failure this prevents is the quiet one: the request is answered, correctly in every
    respect except that it left the region it was allowed to be processed in."""
    registry = ProviderRegistry([_Provider(("primary", "us-central1"), ("backup", "eu"))])

    dispatched = await dispatch_with_fallback(
        registry,
        _request("primary"),
        ("backup",),
        permits=permits([RegionAllowed(registry, ("eu",))]),
    )

    assert dispatched.response.model == "backup"
    assert [entry.model for entry in dispatched.skipped] == ["primary"]
    assert "us-central1" in dispatched.skipped[0].reason


async def test_the_reason_names_the_region_and_what_was_permitted() -> None:
    """An operator has to be able to act on it without reading our source."""
    registry = ProviderRegistry([_Provider(("primary", "us-central1"))])

    with pytest.raises(NoCapableModel) as caught:
        await dispatch_with_fallback(
            registry,
            _request("primary"),
            (),
            permits=permits([RegionAllowed(registry, ("eu",))]),
        )

    assert "us-central1" in str(caught.value)
    assert "eu" in str(caught.value)


async def test_a_permitted_region_passes_untouched() -> None:
    registry = ProviderRegistry([_Provider(("primary", "eu"))])

    dispatched = await dispatch_with_fallback(
        registry, _request("primary"), (), permits=permits([RegionAllowed(registry, ("eu",))])
    )

    assert dispatched.response.model == "primary"
    assert dispatched.skipped == []


async def test_a_model_that_declares_no_region_is_not_refused() -> None:
    """The mock and the laptop adapter declare none. Refusing them would break every development
    setup, and the honest reading is that such a deployment has no residency posture to violate —
    the constraint bites only where a region is actually declared."""
    registry = ProviderRegistry([_Provider(("mock", ""))])

    dispatched = await dispatch_with_fallback(
        registry, _request("mock"), (), permits=permits([RegionAllowed(registry, ("eu",))])
    )

    assert dispatched.response.model == "mock"


async def test_no_configured_constraint_permits_everything() -> None:
    registry = ProviderRegistry([_Provider(("anywhere", "us-central1"))])

    dispatched = await dispatch_with_fallback(
        registry, _request("anywhere"), (), permits=permits([RegionAllowed(registry, ())])
    )

    assert dispatched.response.model == "anywhere"


async def test_the_first_refusal_is_the_one_reported() -> None:
    """A candidate excluded for two reasons is excluded; listing both would read as though every
    one had to be fixed."""

    class _Always:
        def __init__(self, reason: str) -> None:
            self._reason = reason

        async def refusal(self, model: str) -> str | None:
            return self._reason

    registry = ProviderRegistry([_Provider(("only", "eu"))])

    with pytest.raises(NoCapableModel) as caught:
        await dispatch_with_fallback(
            registry,
            _request("only"),
            (),
            permits=permits([_Always("first reason"), _Always("second reason")]),
        )

    assert "first reason" in str(caught.value)
    assert "second reason" not in str(caught.value)


# -- an unserved model is visible rather than silently passed over ---------------------------


async def test_a_model_no_provider_serves_appears_in_the_failure() -> None:
    """It used to be a silent `continue`, so a typo in a fallback chain was invisible — the chain
    simply behaved as though the entry were not there."""
    registry = ProviderRegistry([_Provider(("real", "eu"))])

    with pytest.raises(NoCapableModel) as caught:
        await dispatch_with_fallback(registry, _request("typo"), ("also-typo",))

    assert "typo" in str(caught.value)
    assert "also-typo" in str(caught.value)


# -- through the route ---------------------------------------------------------------------------


async def test_the_route_reports_a_precondition_failure_rather_than_an_outage() -> None:
    """502 said "the provider is down" for what is a configuration problem. 400 says which."""
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry([_Provider(("elsewhere-1", "us-central1"))])

    with TestClient(app) as client:
        response = client.post("/v1beta/models/elsewhere-1:generateContent", json=_BODY)

        assert response.status_code == 400
        assert response.json()["error"]["status"] == "FAILED_PRECONDITION"
        assert "us-central1" in response.json()["error"]["message"]

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    assert [row.outcome for row in rows] == ["no_capable_model"]


async def test_the_audit_row_records_which_candidates_were_passed_over_and_why() -> None:
    """A fallback that skipped three models for three reasons is exactly what somebody needs to
    see when they ask why the answer came from the model it did."""
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0, allowed_regions="eu"))
    app.state.providers = ProviderRegistry(
        [_Provider(("primary-1", "us-central1"), ("backup-1", "eu"))]
    )

    from aira_gateway.pipeline.config import Pipeline

    class _Store:
        async def get(self, use_case: Any) -> Pipeline:
            return Pipeline(steps=(), fallback_models=("backup-1",))

    app.state.pipeline_store = _Store()

    with TestClient(app) as client:
        response = client.post("/v1beta/models/primary-1:generateContent", json=_BODY)
        assert response.status_code == 200

        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())

    decisions = rows[0].pipeline_decisions or []
    skipped = [entry for entry in decisions if entry.get("action") == "skipped"]
    assert [entry["to"] for entry in skipped] == ["primary-1"]
    assert "us-central1" in skipped[0]["why"]
    # And the substitution itself is still recorded, so asked-vs-served stays answerable.
    assert rows[0].requested_model == "primary-1"
    assert rows[0].model == "backup-1"


# -- one policy, every cloud (ADR-0012 §6) -----------------------------------------------------


async def test_an_azure_region_passes_the_same_check_as_a_google_one() -> None:
    """The mechanism was always generic — it reads whatever the adapter declares — but the
    *configuration* was not: the allow-list lived behind a `vertex_`-named setting with Google-only
    defaults. The first Azure model would then have failed a check named after Google.

    "Which regions may we use" is one question with a vendor-specific vocabulary.
    """
    registry = ProviderRegistry([_Provider(("gpt-5", "westeurope"), ("gemini", "europe-west1"))])
    allowed = ("westeurope", "europe-west1")

    for model in ("gpt-5", "gemini"):
        dispatched = await dispatch_with_fallback(
            registry, _request(model), (), permits=permits([RegionAllowed(registry, allowed)])
        )
        assert dispatched.response.model == model


async def test_an_azure_region_outside_the_policy_is_refused_like_any_other() -> None:
    registry = ProviderRegistry([_Provider(("gpt-5", "eastus"))])

    with pytest.raises(NoCapableModel) as caught:
        await dispatch_with_fallback(
            registry,
            _request("gpt-5"),
            (),
            permits=permits([RegionAllowed(registry, ("westeurope",))]),
        )

    assert "eastus" in str(caught.value)


def test_the_default_policy_covers_the_eu_regions_of_every_supported_cloud() -> None:
    """Listed before Microsoft Foundry exists, on purpose: the alternative is that the first Azure
    model added is silently refused by a default nobody thought to widen — which is a bad way to
    learn that a policy list was written for one cloud."""
    from aira_gateway.residency import DEFAULT_ALLOWED_REGIONS, parse_allowed

    assert "europe-west1" in DEFAULT_ALLOWED_REGIONS  # Google
    assert "eu" in DEFAULT_ALLOWED_REGIONS  # Google multi-region
    assert "westeurope" in DEFAULT_ALLOWED_REGIONS  # Azure
    assert "germanywestcentral" in DEFAULT_ALLOWED_REGIONS  # Azure

    # And nothing outside the EU sneaks into the default.
    assert not {"us-central1", "eastus", "us-east-1"} & set(DEFAULT_ALLOWED_REGIONS)

    # An empty setting means "the EU defaults", never "no constraint": a residency rule that has
    # to be switched on is one that will be found switched off.
    assert parse_allowed("") == DEFAULT_ALLOWED_REGIONS
    assert parse_allowed("westeurope") == ("westeurope",)


def test_the_residency_setting_is_not_named_after_one_cloud() -> None:
    """A per-cloud setting means a per-cloud audit, and the one added last is the one nobody
    remembers to check."""
    fields = set(GatewaySettings.model_fields)

    assert "allowed_regions" in fields
    assert not any(
        name.endswith("allowed_regions") and name != "allowed_regions" for name in fields
    )
