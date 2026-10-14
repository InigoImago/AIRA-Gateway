"""What every model a request reaches must meet, whichever path reaches it (`ADR-0012` §3).

Residency, the installation's approval (`FRD-307`), the use case's release (`FRD-308`) and — where
spend is capped — a price (`FRD-610` §3.2) are properties of the installation, not of a feature the
request uses. The dispatch chain asks them of every candidate (`prepare.requirements_for`), and a
pipeline step asks them of every model it is about to call (`pipeline_run`): a step's models are
checked when the pipeline is saved, and a release withdrawn or an approval revoked after that must
still stop the call — or a redactor goes on sending prompts to a model the installation withdrew.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request

from aira_gateway.api.serving.context import (
    attribution_of,
    catalog_of,
    released_models,
    use_case_of,
)
from aira_gateway.requirements import (
    ModelApproved,
    ModelReleasedForUseCase,
    PricedWhereCostIsLimited,
    RegionAllowed,
    Requirement,
)
from aira_gateway.residency import parse_allowed, parse_model_regions
from aira_gateway.state import budgets_of, pricing_of, providers_of, settings_of


async def installation_requirements(request: Request) -> list[Requirement]:
    """What any model this request reaches must satisfy, asked per model.

    The release is read once here and asked per model, so a long chain or a pipeline of three steps
    costs one query.
    """
    registry = providers_of(request)
    settings = settings_of(request)
    checks: list[Requirement] = [
        RegionAllowed(
            registry,
            parse_allowed(settings.allowed_regions),
            parse_model_regions(settings.allowed_model_regions),
        ),
        ModelApproved(catalog_of(request), registry),
        ModelReleasedForUseCase(await released_models(request), use_case_of(request), registry),
    ]
    if settings.enforce_priced_models:
        # Asked on every verb and every step: an embedding and a step's call book against the same
        # budgets as a chat request. The budget question is passed as a callable, resolved at most
        # once per request and only when a model turns out to have no price — so the ordinary
        # request pays for no extra query.
        checks.append(
            PricedWhereCostIsLimited(pricing_of(request), _cost_limited_once(request), registry)
        )
    return checks


def _cost_limited_once(request: Request) -> Callable[[], Awaitable[bool]]:
    """*Is spend capped for this caller?* — asked at most once per request, however many models.

    Memoised on the request rather than in the requirement, because a chain builds its conditions
    once and asks them per hop, and two hops must not be two queries about a fact that cannot
    change inside one request.
    """

    async def answer() -> bool:
        cached = getattr(request.state, "cost_limited", None)
        if cached is None:
            # The **same pot** the reservation books against: `attribution` is where the use case
            # and the person come from on this path (`enforce_pre_dispatch`), and resolving either
            # of them a second way is how one request gets asked about two different budgets.
            attribution = attribution_of(request)
            cached = await budgets_of(request).cost_limited(
                getattr(attribution, "use_case", None), getattr(attribution, "person", None)
            )
            request.state.cost_limited = cached
        return bool(cached)

    return answer
