"""The event payloads the gateway's read-model is built from, one shape per record kind.

**Every writer of an event builds it here** — the views and the seeds alike. A payload written out
by hand beside its builder is a second definition of the event, and the field a view adds next
would not reach what the seed sends (`test_outbox_routing.py` holds every `emit` to its builder).
"""

from __future__ import annotations

from typing import Any

from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.budgets.models import Budget
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.usecases.models import UseCase
from aira_management.config.runtime import get_settings


def _snapshot(usecase: UseCase) -> dict[str, Any]:
    return {
        "slug": usecase.slug,
        "name": usecase.name,
        "description": usecase.description,
        "processing_notes": usecase.processing_notes,
        "store_payloads": usecase.store_payloads,
        # The **stored** choice, not the effective one: the gateway applies strict data mode when
        # it reads (`FRD-627`), so turning the mode off restores this without a new event.
        "payload_visibility": usecase.payload_visibility,
        # **The retired field, still emitted, derived.** A gateway older than
        # `payload_visibility`
        # reads this one and nothing else, and an absent boolean is `False` to it — so leaving it
        # out would *unrestrict* a use case on every instance not yet upgraded, which is a
        # disclosure rather than a missing feature. Management is upgraded first (`FRD-204`), so
        # this is the window that exists by design; it goes when no gateway reads it. **Restricted
        # under strict data mode** whatever is stored, because such a gateway knows no mode either.
        "restrict_members_to_own_requests": get_settings().strict_data_mode
        or usecase.payload_visibility != UseCase.PAYLOAD_VISIBILITY_MEMBERS,
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


def _membership_payload(usecase: UseCase, username: str, role: str) -> dict[str, Any]:
    """A direct membership, by the username the gateway matches it on (`FRD-209` §2.1)."""
    return {"slug": usecase.slug, "username": username, "role": role}


def _pipeline_payload(slug: str, config: PipelineConfig) -> dict[str, Any]:
    return {
        "use_case": slug,
        "steps": config.steps,
        "fallback_models": config.fallback_models,
        # What the use case does with the installation's chains (`FRD-310`).
        "fallback_mode": config.fallback_mode,
    }


def _api_key_payload(key: ApiKey) -> dict[str, Any]:
    """An active key: its hash, never the plaintext, and the lifetime the gateway enforces."""
    return {
        "prefix": key.prefix,
        "key_hash": key.key_hash,
        # The **owner**, the name every audit row carries: a row describes what called, not who
        # authorised the credential (`FRD-604` §5.3).
        "subject": key.owner.get_username(),
        "issued_by": key.issued_by,
        "use_case": key.use_case.slug,
        "label": key.label,
        "status": "active",
        # The gateway enforces it; Management only decides it. None only for a key issued before
        # keys expired at all.
        "expires_at": key.expires_at.isoformat() if key.expires_at is not None else None,
    }
