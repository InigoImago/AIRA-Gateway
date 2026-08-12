"""What lives on ``app.state``, said out loud so a type checker can read it.

**Why this module exists.** `app.state` is a bag of attributes typed `Any`. Everything the request
path needs hangs off it — the budget service, the rate limiter, the suspensions, the catalog, the
pricing, the writer — so a large share of this codebase's calls between components are made through
a boundary mypy cannot see. `mypy --strict` passes over 255 files and verifies **none** of them.

That is not a theoretical exposure. On 2026-08-11 it cost a control: `SuspensionService.check`
returns `Throttle`, `RateLimitService.check` consumes `BucketRequest`, and the gate passed the
first straight into the second. The two share no field the limiter reads, so a `throttle`
suspension raised `AttributeError` and became a **500** for every request from the caller it was
meant to slow down — while the console showed the decision as active and enforcing. A one-line
annotation would have made it a build failure.

The pattern was already here and applied twice: `registry_of` and `catalog_of` in `serving.py`
exist for exactly this reason, and `enforce_pre_dispatch` carries the comment *"`app.state` is
untyped, so the annotation is what states the contract the route relies on"*. What was missing was
finishing the thought — eighteen other attributes were read raw.

**Accessors rather than a typed container.** Starlette builds `app.state` itself and hands the same
object to every request, so replacing it would mean fighting the framework for no gain. A function
per attribute costs one line, reads at the call site exactly as the attribute did, and gives mypy
the one thing it needs: a declared return type. The names follow the two that already existed
(`<thing>_of`), so nothing has to be learned.

**A `Protocol`, not the concrete classes.** `AppServices` describes what the request path *uses*;
`create_app` assembles the real objects and the tests substitute their own. Naming the concrete
classes here would make every stand-in in the suite a type error and push the tests toward
inheriting from production classes, which is how a test double stops standing in for anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from aira_gateway.anomalies.suspensions import SuspensionService
    from aira_gateway.budgets.service import BudgetService
    from aira_gateway.catalog import ModelCatalog
    from aira_gateway.config import GatewaySettings
    from aira_gateway.persistence.writer import RequestLogWriter
    from aira_gateway.pricing import PricingService
    from aira_gateway.ratelimit.service import RateLimitService
    from aira_gateway.upstreams.base import ProviderRegistry


def settings_of(request: Request) -> GatewaySettings:
    """This deployment's configuration."""
    settings: GatewaySettings = request.app.state.settings
    return settings


def sessionmaker_of(request: Request) -> async_sessionmaker[AsyncSession]:
    """The gateway database. Read-model lookups and the audit trail both go through it."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.db_sessionmaker
    return sessionmaker


def budgets_of(request: Request) -> BudgetService:
    """Reservations, settlement, and the "already over budget" refusal (`FRD-401`, `FRD-405`)."""
    budgets: BudgetService = request.app.state.budgets
    return budgets


def rate_limits_of(request: Request) -> RateLimitService:
    """Per-use-case and per-member request rates (`FRD-405`)."""
    limits: RateLimitService = request.app.state.rate_limits
    return limits


def suspensions_of(request: Request) -> SuspensionService:
    """The kill switch and its throttles (`FRD-503`).

    The seam that proved why this module is worth having: the throttles this returns are not the
    shape the rate limiter consumes, and nothing said so for as long as both sides were `Any`.
    """
    suspensions: SuspensionService = request.app.state.suspensions
    return suspensions


def pricing_of(request: Request) -> PricingService:
    """What a model costs, in integer nano-units (`FRD-403`)."""
    pricing: PricingService = request.app.state.pricing
    return pricing


def writer_of(request: Request) -> RequestLogWriter:
    """The off-path audit writer (`FRD-405` §4.4)."""
    writer: RequestLogWriter = request.app.state.log_writer
    return writer


def providers_of(request: Request) -> ProviderRegistry:
    """Every registered adapter. Also reachable as `serving.registry_of`, which predates this
    module and is kept because both surfaces already read it under that name."""
    registry: ProviderRegistry = request.app.state.providers
    return registry


def model_catalog_of(request: Request) -> ModelCatalog:
    """What a model is declared to be (`FRD-114`). Also `serving.catalog_of`, for the same
    reason as above."""
    catalog: ModelCatalog = request.app.state.catalog
    return catalog
