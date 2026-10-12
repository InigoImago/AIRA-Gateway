"""A spend limit cannot bound what it cannot count (`FRD-610` §3.2).

The hole, stated in the concept and open since: a request against a model with **no price on file**
contributes nothing to a cost figure. It is counted apart as `unpriced_requests` — right for
reporting, where unknown is not zero — and for *enforcement* unknown becomes **unbounded**. A use
case whose models are all unpriced spends without limit against a budget that looks configured, and
nothing anywhere said so.

The answer is a dispatch condition rather than a check at the reservation, and that is what makes
it better than a refusal: a chain **skips** an unpriced candidate and a priced one serves
(`FRD-302`). What is refused is a request whose only candidate cannot be counted.

Every case below that *allows* the request matters as much as the one that refuses it: a rule about
money that fires where no money is capped would make an unpriced model unusable for reasons nobody
asked for.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalRequest, CanonicalResponse, CanonicalUsage
from aira_gateway.db.models import BudgetRead, ModelRead, UseCaseRead
from aira_gateway.pipeline.config import Pipeline
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel
from aira_gateway.upstreams.mock import MockProvider

#: One euro, in nano-units — the unit every cost limit is stored in (`aira_common.money`).
ONE_EURO = 1_000_000_000


class _Echo:
    """A real-looking model. **Not** a test double: those are exempt by name, and the exemption
    would make every case here pass without the rule existing at all."""

    def __init__(self, name: str) -> None:
        self._name = name

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(self._name, self._name, ("generateContent",), "acme", "acme", "")]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(
            model=self._name,
            text=f"[{self._name}]",
            usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def stream_generate(self, request: object):  # noqa: ANN201
        raise NotImplementedError
        yield  # pragma: no cover

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0, 1.0]]


class _FixedStore:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: str | None) -> Pipeline:
        return self._pipeline


def _app(pipeline: Pipeline | None = None, *providers: object, **settings: Any) -> Any:
    app = create_app(
        GatewaySettings(
            auth_required=False, require_use_case=False, log_queue_size=0, redis_url="", **settings
        )
    )
    registry = ProviderRegistry(list(providers))
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _FixedStore(pipeline or Pipeline())
    return app


async def _catalogue(app: Any, model: str, *, priced: bool) -> None:
    """Catalogue and approve, with or without a price.

    Approved either way: `ModelApproved` is a different gate with a different owner, and a test of
    this rule has to pass that one to reach it.
    """
    async with app.state.db_sessionmaker() as session:
        session.add(
            ModelRead(
                model=model,
                approved=True,
                capabilities=["generate", "embedding"],
                input_price_per_million_nanos=100_000_000 if priced else None,
                output_price_per_million_nanos=400_000_000 if priced else None,
            )
        )
        await session.commit()


async def _use_case(app: Any, slug: str = "uc", models: list[str] | None = None) -> None:
    async with app.state.db_sessionmaker() as session:
        session.add(UseCaseRead(slug=slug, name=slug, allowed_models=models))
        await session.commit()


async def _budget(app: Any, **limits: Any) -> None:
    """One enabled budget on the use case, with whichever limits the case is about."""
    async with app.state.db_sessionmaker() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case=limits.pop("use_case", "uc"),
                scope=limits.pop("scope", "use_case"),
                subject=limits.pop("subject", ""),
                period="month",
                enabled=True,
                **limits,
            )
        )
        await session.commit()


def _post(client: TestClient, model: str, slug: str | None = "uc") -> Any:
    headers = {"X-AIRA-Use-Case": slug} if slug else {}
    return client.post(
        f"/v1beta/models/{model}:generateContent",
        json={"contents": [{"role": "user", "parts": [{"text": "hello"}]}]},
        headers=headers,
    )


# == the hole ====================================================================================


async def test_an_unpriced_model_is_refused_where_a_cost_limit_binds() -> None:
    app = _app(None, _Echo("free-lunch"))
    with TestClient(app) as client:
        await _catalogue(app, "free-lunch", priced=False)
        await _use_case(app)
        await _budget(app, limit_cost_nanos=5 * ONE_EURO)
        response = _post(client, "free-lunch")

    assert response.status_code == 400, response.text
    message = response.json()["error"]["message"]
    # The model by name, the reason, and what to do: a refusal an operator cannot act on is an
    # outage with a sentence attached.
    assert "free-lunch" in message
    assert "no price" in message
    assert "spend limit" in message or "cost budget" in message
    # The escape hatch is named, because the alternative is a reader concluding the gateway is
    # broken (the switch exists precisely for an installation that has not finished pricing).
    assert "AIRA_ENFORCE_PRICED_MODELS" in message


async def test_the_chain_prefers_a_priced_model_over_refusing() -> None:
    """The reason this is a dispatch condition and not a check at the reservation.

    A fallback is exactly where a request ends up when something is already wrong, and *"this
    candidate cannot be counted"* is a reason to try the next one — not a reason to fail.
    """
    app = _app(Pipeline(fallback_models=("priced",)), _Echo("free-lunch"), _Echo("priced"))
    with TestClient(app) as client:
        await _catalogue(app, "free-lunch", priced=False)
        await _catalogue(app, "priced", priced=True)
        await _use_case(app)
        await _budget(app, limit_cost_nanos=5 * ONE_EURO)
        response = _post(client, "free-lunch")

    assert response.status_code == 200, response.text
    assert response.json()["modelVersion"] == "priced"


# == where it must not fire ======================================================================


async def test_a_priced_model_serves_under_the_same_limit() -> None:
    app = _app(None, _Echo("priced"))
    with TestClient(app) as client:
        await _catalogue(app, "priced", priced=True)
        await _use_case(app)
        await _budget(app, limit_cost_nanos=5 * ONE_EURO)
        response = _post(client, "priced")

    assert response.status_code == 200, response.text


async def test_a_token_or_request_limit_counts_an_unpriced_request_exactly_as_any_other() -> None:
    """Only a *cost* limit is blind. Tokens and requests are counted whatever the price is, so
    refusing here would take away a model for a limit that bounds it perfectly well."""
    for limits in ({"limit_tokens": 1_000_000}, {"limit_requests": 1000}):
        app = _app(None, _Echo("free-lunch"))
        with TestClient(app) as client:
            await _catalogue(app, "free-lunch", priced=False)
            await _use_case(app)
            await _budget(app, **limits)
            response = _post(client, "free-lunch")

        assert response.status_code == 200, f"{limits}: {response.text}"


async def test_no_budget_at_all_leaves_an_unpriced_model_alone() -> None:
    app = _app(None, _Echo("free-lunch"))
    with TestClient(app) as client:
        await _catalogue(app, "free-lunch", priced=False)
        await _use_case(app)
        response = _post(client, "free-lunch")

    assert response.status_code == 200, response.text


async def test_with_enforcement_off_nothing_is_capped_and_nothing_is_refused() -> None:
    """`AIRA_ENFORCE_BUDGETS=false` means no limit refuses anything.

    Refusing a model to protect a limit that stops nothing would be the strictest reading of the
    least meaningful configuration — and the console now says out loud that the limit is inert
    (`FRD-610` §3.3), which is the honest answer in that installation.
    """
    app = _app(None, _Echo("free-lunch"), enforce_budgets=False)
    with TestClient(app) as client:
        await _catalogue(app, "free-lunch", priced=False)
        await _use_case(app)
        await _budget(app, limit_cost_nanos=5 * ONE_EURO)
        response = _post(client, "free-lunch")

    assert response.status_code == 200, response.text


async def test_the_switch_lets_an_installation_accept_the_gap_knowingly() -> None:
    app = _app(None, _Echo("free-lunch"), enforce_priced_models=False)
    with TestClient(app) as client:
        await _catalogue(app, "free-lunch", priced=False)
        await _use_case(app)
        await _budget(app, limit_cost_nanos=5 * ONE_EURO)
        response = _post(client, "free-lunch")

    assert response.status_code == 200, response.text


async def test_a_disabled_cost_budget_does_not_bind() -> None:
    """A lifted budget keeps its figures and binds nothing (`FRD-401`), so it cannot make a model
    unusable. Asserted because `applicable` is what decides, and this is the one of its rules that
    a reader of this file would not guess."""
    app = _app(None, _Echo("free-lunch"))
    with TestClient(app) as client:
        await _catalogue(app, "free-lunch", priced=False)
        await _use_case(app)
        async with app.state.db_sessionmaker() as session:
            session.add(
                BudgetRead(
                    id=1,
                    use_case="uc",
                    scope="use_case",
                    subject="",
                    period="month",
                    enabled=False,
                    limit_cost_nanos=5 * ONE_EURO,
                )
            )
            await session.commit()
        response = _post(client, "free-lunch")

    assert response.status_code == 200, response.text


# == whose budget binds ==========================================================================


async def test_a_per_member_cost_budget_binds_too() -> None:
    """`each_member` is a cost cap per person, and the request path resolves it from the caller.
    A rule that only looked at the use case's shared pot would leave the per-head case unbounded —
    which is the same hole one scope along."""
    app = _app(None, _Echo("free-lunch"))
    with TestClient(app) as client:
        await _catalogue(app, "free-lunch", priced=False)
        await _use_case(app)
        await _budget(app, scope="each_member", limit_cost_nanos=ONE_EURO)
        response = _post(client, "free-lunch")

    assert response.status_code == 400, response.text
    assert "free-lunch" in response.json()["error"]["message"]


async def test_the_installations_own_cost_budget_binds_a_caller_with_no_use_case() -> None:
    """`FRD-610` §3.1's residual bucket. A caller naming no use case is not exempt from spend
    control, so it is not exempt from being countable either."""
    app = _app(None, _Echo("free-lunch"))
    with TestClient(app) as client:
        await _catalogue(app, "free-lunch", priced=False)
        await _budget(app, use_case="", scope="use_case", limit_cost_nanos=ONE_EURO)
        response = _post(client, "free-lunch", slug=None)

    assert response.status_code == 400, response.text
    assert "free-lunch" in response.json()["error"]["message"]


async def test_the_mock_is_exempt_because_it_is_not_a_model() -> None:
    """Deterministic fiction has no price and never will.

    The exemption every requirement in `requirements.py` grants the test double, and this one needs
    it most: the demo ships a cost budget *and* the mock, so without this the one-command
    demonstration would refuse its own traffic. Written after the mutation that removes the
    exemption survived — `test_cost_budgets.py` prices its mock models, so nothing noticed.

    `_is_test_double` asks the **registry**, not the name, so a provider called `mock` that is a
    real adapter is still governed.
    """
    app = _app(None, MockProvider("mock-1"))
    with TestClient(app) as client:
        # Catalogued and approved with **no price** — exactly the shape that is refused above.
        await _catalogue(app, "mock-1", priced=False)
        await _use_case(app)
        await _budget(app, limit_cost_nanos=5 * ONE_EURO)
        response = _post(client, "mock-1")

    assert response.status_code == 200, response.text
