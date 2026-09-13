"""What lives on ``app.state``, said out loud so a type checker can read it.

`app.state` is typed `Any`, and most calls between the request path's components go through it —
a boundary mypy cannot see. A `throttle` suspension once reached the rate limiter in the wrong
shape and became a 500 for the caller it was meant to slow; an annotation would have been a build
failure. `test_app_state_is_typed.py` requires every read outside this module to be annotated.

- **Accessors, not a typed container**: Starlette owns `app.state`, and a `<thing>_of` function per
  attribute reads like the attribute while giving mypy a declared return type.
- **Types imported for annotation only**: `create_app` assembles the real objects, and nothing is
  checked at runtime, so tests substitute their own stand-ins freely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from aira_gateway.anomalies.suspensions import SuspensionService
    from aira_gateway.budgets.service import BudgetService
    from aira_gateway.config import GatewaySettings
    from aira_gateway.persistence.writer import RequestLogWriter
    from aira_gateway.pricing import PricingService
    from aira_gateway.ratelimit.service import RateLimitService
    from aira_gateway.upstreams.base import ProviderRegistry

# The catalogue has no accessor here on purpose: readers use `api.serving.catalog_of`, which hands
# back the **per-request** view (`ModelCatalog.per_request`) rather than `app.state.catalog`.


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

    Its throttles are not the shape the rate limiter consumes — the seam this module exists for.
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
    """Every registered adapter."""
    registry: ProviderRegistry = request.app.state.providers
    return registry
