"""The event payloads the gateway's read-model is built from, one shape per record kind."""

from __future__ import annotations

from typing import Any

from aira_management.apps.budgets.models import Budget
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.usecases.models import UseCase


def _snapshot(usecase: UseCase) -> dict[str, Any]:
    return {
        "slug": usecase.slug,
        "name": usecase.name,
        "description": usecase.description,
        "processing_notes": usecase.processing_notes,
        "store_payloads": usecase.store_payloads,
        "payload_visibility": usecase.payload_visibility,
        # **The retired field, still emitted, derived.** A gateway older than
        # `payload_visibility`
        # reads this one and nothing else, and an absent boolean is `False` to it — so leaving it
        # out would *unrestrict* a use case on every instance not yet upgraded, which is a
        # disclosure rather than a missing feature. Management is upgraded first (`FRD-204`), so
        # this is the window that exists by design; it goes when no gateway reads it.
        "restrict_members_to_own_requests": usecase.payload_visibility
        != UseCase.PAYLOAD_VISIBILITY_MEMBERS,
        "tools_enabled": usecase.tools_enabled,
        "include_reasoning": usecase.include_reasoning,
        "prompt_caching_enabled": usecase.prompt_caching_enabled,
        "prompt_cache_ttl": usecase.prompt_cache_ttl,
        "retention_days": usecase.retention_days,
        # `FRD-308`. Sorted so the event is stable: the topic is compacted by slug, and a payload
        # differing only in ordering would make every diff of the log a false positive.
        "allowed_models": sorted(usecase.allowed_models.values_list("name", flat=True)),
    }


def _budget_payload(budget: Budget, slug: str) -> dict[str, Any]:
    return {
        "id": budget.pk,
        "use_case": slug,
        "scope": budget.scope,
        "subject": budget.subject,
        "period": budget.period,
        # Decimal as a string: money must not round-trip through a JSON float.
        "limit_cost": str(budget.limit_cost) if budget.limit_cost is not None else None,
        "limit_tokens": budget.limit_tokens,
        "limit_requests": budget.limit_requests,
        "enabled": budget.enabled,
    }


def _rate_limit_payload(limit: RateLimit, slug: str) -> dict[str, Any]:
    return {
        "id": limit.pk,
        "use_case": slug,
        "scope": limit.scope,
        "subject": limit.subject,
        "limit_rpm": limit.limit_rpm,
        "burst": limit.burst,
        "enabled": limit.enabled,
    }
