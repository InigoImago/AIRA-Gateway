"""Idempotent application of configuration events into the gateway read-model (`FRD-204`).

Every handler is an upsert or delete keyed by natural keys, so a redelivered event — or a replayed
compacted topic — converges to the same state. :data:`HANDLERS`, at the end of the module, is the
event vocabulary; `tools/tests` check it against what Management emits.

**Absent is not empty.** During a rolling update an event from an older Management lacks newer
fields (`FRD-127`), so each handler states what an absent field means where it reads it — and the
readings differ on purpose.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aira_common.money import to_nanos
from aira_gateway.db.base import Base
from aira_gateway.db.models import (
    AnomalyRuleRead,
    ApiKey,
    BudgetRead,
    BudgetUsage,
    ModelRead,
    PipelineConfigRead,
    RateLimitRead,
    RoleRead,
    UseCaseGroupRead,
    UseCaseMemberRead,
    UseCaseRead,
)
from aira_gateway.retention import DEFAULT_RETENTION_DAYS

_log = structlog.get_logger(__name__)

Handler = Callable[[AsyncSession, dict[str, Any]], Awaitable[None]]

#: Event types this gateway knowingly does not apply, each with the reason. Any other type without
#: a handler is logged, because an unapplied event is a control an operator believes is in force.
IGNORED_EVENT_TYPES: frozenset[str] = frozenset()

#: A model event's declaration fields, and the value applied when the event carries one as null.
#: A field the event omits is left alone: an older Management sends prices without capabilities,
#: and applying them must not blank a declaration somebody made (`FRD-114`).
_DECLARATION_DEFAULTS: dict[str, Any] = {
    "capabilities": None,
    "publisher": "",
    "platform": "",
    "addressing": None,
    "underlying_model": "",
    "context_window": None,
    "max_output_tokens": None,
    "default_max_output_tokens": None,
    "thinking": None,
    "embedding": None,
    "attachments": None,
    "hosting": "",
    "deprecated": False,
    "numeric_id": None,
}


async def apply_event(session: AsyncSession, event_type: str, payload: dict[str, Any]) -> None:
    """Apply one configuration event and commit it.

    An unknown type is tolerated — an older gateway must survive a newer Management's events rather
    than crash-loop its consumer (`FRD-127`) — but never silently.
    """
    handler = HANDLERS.get(event_type)
    if handler is None:
        if event_type not in IGNORED_EVENT_TYPES:
            _log.warning(
                "config_event_not_applied",
                event_type=event_type,
                # Names only: configuration carries `client_secret`-shaped fields (`FRD-122`).
                fields=sorted(payload),
            )
        return
    await handler(session, payload)
    await session.commit()


# == reading a payload ============================================================================


def _price_nanos(value: object) -> int | None:
    """Prices arrive as exact decimal strings; absent means "no price on file"."""
    return None if value is None else to_nanos(str(value))


def _moment(value: Any) -> datetime | None:
    """An ISO-8601 instant from an event, or ``None`` if absent or unparsable.

    Not raising keeps the stream moving past one malformed field; for an expiry that is safe only
    because the key can still be revoked by hand.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _released_models(payload: dict[str, Any]) -> list[str] | None:
    """The models the use case may call, or ``None`` if the event did not say (`FRD-308`).

    Anything but a list of strings reads as "did not say": a malformed payload must not be able to
    stop a use case.
    """
    released = payload.get("allowed_models")
    if not isinstance(released, list):
        return None
    return sorted({str(name) for name in released if isinstance(name, str) and name})


async def _upsert(
    session: AsyncSession, entity: type[Base], fields: dict[str, Any], **key: Any
) -> None:
    """Insert the row ``key`` names, or overwrite ``fields`` on it — so a redelivery converges."""
    (identity,) = key.values()
    record = await session.get(entity, identity)
    if record is None:
        session.add(entity(**key, **fields))
    else:
        for name, value in fields.items():
            setattr(record, name, value)


# == use cases ====================================================================================


async def _upsert_usecase(session: AsyncSession, payload: dict[str, Any]) -> None:
    fields = {
        "name": payload.get("name", ""),
        "description": payload.get("description", ""),
        "processing_notes": payload.get("processing_notes", ""),
        # Absent: storage on, as before the field existed.
        "store_payloads": bool(payload.get("store_payloads", True)),
        # Absent means **off**: a missing capability must not read as permission (`FRD-114` FR-7).
        "tools_enabled": bool(payload.get("tools_enabled", False)),
        "include_reasoning": bool(payload.get("include_reasoning", False)),
        # Off as well: the cache scope is shared across the organisation (`FRD-133` §4b).
        "prompt_caching_enabled": bool(payload.get("prompt_caching_enabled", False)),
        # The cheap TTL; reading silence as "1h" would double every write price.
        "prompt_cache_ttl": str(payload.get("prompt_cache_ttl") or "5m"),
        # Absent means **unrestricted**: a missing restriction must not narrow what members see.
        "restrict_members_to_own_requests": bool(
            payload.get("restrict_members_to_own_requests", False)
        ),
        "retention_days": int(payload.get("retention_days") or DEFAULT_RETENTION_DAYS),
        # Absent keeps `None`, read as unrestricted; an empty list releases nothing (`FRD-308`).
        "allowed_models": _released_models(payload),
    }
    await _upsert(session, UseCaseRead, fields, slug=payload["slug"])


async def _retire_usecase(session: AsyncSession, payload: dict[str, Any]) -> None:
    """End every kind of access, and **keep the row as a tombstone** (`FRD-607`).

    Management cascades the deletion in its own database but publishes only ``usecase.deleted``,
    so the children go here — otherwise a key keeps authenticating and a re-created slug inherits
    the old budgets, limits and pipeline.

    - Keys are **deactivated, not deleted**: delivery is at-least-once, and a redelivered
      ``api_key.created`` must not resurrect one (`ADR-0007`).
    - ``request_logs`` are **kept**: the audit trail outlives the use case (`FRD-404` §4.1).
    - The tombstone keeps the use case's retention promise and lets the payload view tell
      `NOT_STORED` from `EXPIRED`. It grants nothing.

    The check belongs here and not at authentication: keys and use cases arrive on different
    topics with no ordering, so a new key may legitimately arrive before its use case.
    """
    slug = payload["slug"]
    await session.execute(update(ApiKey).where(ApiKey.use_case == slug).values(is_active=False))
    await session.execute(delete(BudgetRead).where(BudgetRead.use_case == slug))
    await session.execute(delete(UseCaseGroupRead).where(UseCaseGroupRead.use_case_slug == slug))
    await session.execute(delete(RateLimitRead).where(RateLimitRead.use_case == slug))
    # Only rules scoped to this use case: `use_case IS NULL` is a global rule, and sweeping those
    # would switch off detection for every other use case.
    await session.execute(delete(AnomalyRuleRead).where(AnomalyRuleRead.use_case == slug))
    await session.execute(delete(PipelineConfigRead).where(PipelineConfigRead.use_case == slug))
    # Usage counters are keyed by scope, not by a foreign key: "uc:<slug>" for the use case and
    # "member:<slug>:<subject>" for each member.
    await session.execute(
        delete(BudgetUsage).where(
            (BudgetUsage.scope_key == f"uc:{slug}")
            | (BudgetUsage.scope_key.startswith(f"member:{slug}:"))
        )
    )
    await session.execute(delete(UseCaseMemberRead).where(UseCaseMemberRead.use_case_slug == slug))
    await session.execute(
        update(UseCaseRead).where(UseCaseRead.slug == slug).values(deleted_at=datetime.now(UTC))
    )


async def _purge_usecase(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Drop the tombstone — a Global Administrator's separate decision (`FRD-607`).

    `request_logs` still stay (`FRD-404` §4.1); their payloads fall back to the installation's
    retention default once the row that named a shorter one is gone.
    """
    slug = payload["slug"]
    await session.execute(delete(UseCaseRead).where(UseCaseRead.slug == slug))


# == access: members, group grants, API keys ======================================================


async def _upsert_member(session: AsyncSession, payload: dict[str, Any]) -> None:
    result = await session.execute(
        select(UseCaseMemberRead).where(
            UseCaseMemberRead.use_case_slug == payload["slug"],
            UseCaseMemberRead.subject == payload["username"],
        )
    )
    member = result.scalar_one_or_none()
    role = payload.get("role", "user")
    if member is None:
        session.add(
            UseCaseMemberRead(use_case_slug=payload["slug"], subject=payload["username"], role=role)
        )
    else:
        member.role = role


async def _remove_member(session: AsyncSession, payload: dict[str, Any]) -> None:
    await session.execute(
        delete(UseCaseMemberRead).where(
            UseCaseMemberRead.use_case_slug == payload["slug"],
            UseCaseMemberRead.subject == payload["username"],
        )
    )


async def _upsert_group_grant(session: AsyncSession, payload: dict[str, Any]) -> None:
    result = await session.execute(
        select(UseCaseGroupRead).where(
            UseCaseGroupRead.use_case_slug == payload["slug"],
            UseCaseGroupRead.group_path == payload["group"],
        )
    )
    row = result.scalar_one_or_none()
    role = payload.get("role", "user")
    if row is None:
        session.add(
            UseCaseGroupRead(use_case_slug=payload["slug"], group_path=payload["group"], role=role)
        )
    else:
        row.role = role


async def _remove_group_grant(session: AsyncSession, payload: dict[str, Any]) -> None:
    await session.execute(
        delete(UseCaseGroupRead).where(
            UseCaseGroupRead.use_case_slug == payload["slug"],
            UseCaseGroupRead.group_path == payload["group"],
        )
    )


async def _upsert_api_key(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a Management-issued API key, keyed by prefix (`FRD-205`).

    A ``created`` event can be redelivered *after* the matching ``revoked``, so revocation is
    terminal: metadata is refreshed, but a deactivated key is never reactivated (`ADR-0007`).
    """
    result = await session.execute(select(ApiKey).where(ApiKey.prefix == payload["prefix"]))
    record = result.scalar_one_or_none()
    if record is None:
        session.add(
            ApiKey(
                prefix=payload["prefix"],
                key_hash=payload["key_hash"],
                subject=payload.get("subject", ""),
                issued_by=payload.get("issued_by") or None,
                use_case=payload.get("use_case"),
                label=payload.get("label"),
                is_active=True,
                expires_at=_moment(payload.get("expires_at")),
            )
        )
    else:
        record.key_hash = payload["key_hash"]
        record.subject = payload.get("subject", "")
        record.issued_by = payload.get("issued_by") or None
        record.use_case = payload.get("use_case")
        record.label = payload.get("label")
        record.expires_at = _moment(payload.get("expires_at"))


async def _revoke_api_key(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Deactivate a key, and **write down when**.

    `revoked_at` is the record an incident asks for ("when was this credential revoked"). The event
    carries no timestamp, so this is when the gateway learned of it. Stamped once: revocation is
    terminal, and a later event must not erase the record of the decision.
    """
    result = await session.execute(select(ApiKey).where(ApiKey.prefix == payload["prefix"]))
    record = result.scalar_one_or_none()
    if record is not None:
        record.is_active = False
        if record.revoked_at is None:
            record.revoked_at = datetime.now(UTC)


# == pipeline, budgets, rate limits, anomaly rules ================================================


async def _upsert_pipeline(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a use case's pipeline configuration, keyed by use case (`FRD-300`)."""
    fields = {
        "steps": payload.get("steps", []),
        "fallback_models": payload.get("fallback_models", []),
    }
    await _upsert(session, PipelineConfigRead, fields, use_case=payload["use_case"])


async def _delete_pipeline(session: AsyncSession, payload: dict[str, Any]) -> None:
    use_case = payload["use_case"]
    await session.execute(delete(PipelineConfigRead).where(PipelineConfigRead.use_case == use_case))


async def _upsert_budget(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a budget definition, keyed by id (`FRD-400`)."""
    fields = {
        "use_case": payload["use_case"],
        "scope": payload["scope"],
        "subject": payload.get("subject", ""),
        "period": payload["period"],
        "limit_cost_nanos": _price_nanos(payload.get("limit_cost")),
        "limit_tokens": payload.get("limit_tokens"),
        "limit_requests": payload.get("limit_requests"),
        "enabled": payload.get("enabled", True),
    }
    await _upsert(session, BudgetRead, fields, id=payload["id"])


async def _delete_budget(session: AsyncSession, payload: dict[str, Any]) -> None:
    await session.execute(delete(BudgetRead).where(BudgetRead.id == payload["id"]))


async def _upsert_rate_limit(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a request-rate limit, keyed by id (`FRD-405`)."""
    fields = {
        "use_case": payload["use_case"],
        "scope": payload["scope"],
        "subject": payload.get("subject", ""),
        "limit_rpm": int(payload.get("limit_rpm") or 0),
        "burst": int(payload.get("burst") or 0),
        "enabled": payload.get("enabled", True),
    }
    await _upsert(session, RateLimitRead, fields, id=payload["id"])


async def _delete_rate_limit(session: AsyncSession, payload: dict[str, Any]) -> None:
    await session.execute(delete(RateLimitRead).where(RateLimitRead.id == payload["id"]))


async def _upsert_anomaly_rule(session: AsyncSession, payload: dict[str, Any]) -> None:
    fields = {
        # `None` means the rule is global. An event with no key at all is malformed and skipped
        # below: reading it as global would widen a rule that can block traffic.
        "use_case": payload.get("use_case"),
        "name": payload.get("name", ""),
        "kind": payload["kind"],
        "window_minutes": int(payload.get("window_minutes", 15)),
        "threshold": int(payload["threshold"]),
        "parameter": payload.get("parameter"),
        "min_sample": int(payload.get("min_sample") or 0),
        "action": payload.get("action", "alert"),
        "target": payload.get("target", "subject"),
        "action_minutes": payload.get("action_minutes"),
        "throttle_rpm": payload.get("throttle_rpm"),
        "enabled": bool(payload.get("enabled", True)),
    }
    if "use_case" not in payload:
        return
    await _upsert(session, AnomalyRuleRead, fields, id=payload["id"])


async def _delete_anomaly_rule(session: AsyncSession, payload: dict[str, Any]) -> None:
    await session.execute(delete(AnomalyRuleRead).where(AnomalyRuleRead.id == payload["id"]))


# == the model catalogue ==========================================================================


async def _upsert_model(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a catalogued model, keyed by model name (`FRD-403`, `FRD-114`)."""
    fields: dict[str, Any] = {
        # Absent means approved (see `ModelRead.approved`): an older Management's event must not
        # retire every model in the catalogue.
        "approved": bool(payload.get("approved", True)),
        "display_name": payload.get("display_name", ""),
        "provider": payload.get("provider", ""),
        "input_price_per_million_nanos": _price_nanos(payload.get("input_price_per_million")),
        "cached_input_price_per_million_nanos": _price_nanos(
            payload.get("cached_input_price_per_million")
        ),
        "cache_write_price_per_million_nanos": _price_nanos(
            payload.get("cache_write_price_per_million")
        ),
        "output_price_per_million_nanos": _price_nanos(payload.get("output_price_per_million")),
    }
    for field, default in _DECLARATION_DEFAULTS.items():
        if field in payload:
            fields[field] = payload[field] if payload[field] is not None else default
    await _upsert(session, ModelRead, fields, model=payload["name"])


async def _delete_model(session: AsyncSession, payload: dict[str, Any]) -> None:
    await session.execute(delete(ModelRead).where(ModelRead.model == payload["name"]))


# == roles ========================================================================================


async def _upsert_role(session: AsyncSession, payload: dict[str, Any]) -> None:
    """What a stored role may do (`FRD-614` FR-8).

    An absent ``permissions`` is an empty set: a role whose event names none grants nothing, rather
    than keeping what an earlier event granted.
    """
    fields = {
        "label": payload.get("label", ""),
        "group_path": payload.get("group_path", ""),
        "permissions": [name for name in payload.get("permissions") or [] if isinstance(name, str)],
        "builtin": bool(payload.get("builtin", False)),
        "updated_at": datetime.now(UTC),
    }
    await _upsert(session, RoleRead, fields, slug=payload["slug"])


async def _remove_role(session: AsyncSession, payload: dict[str, Any]) -> None:
    await session.execute(delete(RoleRead).where(RoleRead.slug == payload["slug"]))


#: The event vocabulary: every configuration event this gateway applies, and its handler.
HANDLERS: dict[str, Handler] = {
    "usecase.upserted": _upsert_usecase,
    "usecase.deleted": _retire_usecase,
    "usecase.purged": _purge_usecase,
    "membership.upserted": _upsert_member,
    "membership.removed": _remove_member,
    "use_case_group.granted": _upsert_group_grant,
    "use_case_group.revoked": _remove_group_grant,
    "api_key.created": _upsert_api_key,
    "api_key.revoked": _revoke_api_key,
    "pipeline.upserted": _upsert_pipeline,
    "pipeline.deleted": _delete_pipeline,
    "budget.upserted": _upsert_budget,
    "budget.deleted": _delete_budget,
    "ratelimit.upserted": _upsert_rate_limit,
    "ratelimit.deleted": _delete_rate_limit,
    "anomaly_rule.upserted": _upsert_anomaly_rule,
    "anomaly_rule.deleted": _delete_anomaly_rule,
    "model.upserted": _upsert_model,
    "model.deleted": _delete_model,
    "role.upserted": _upsert_role,
    "role.removed": _remove_role,
}
