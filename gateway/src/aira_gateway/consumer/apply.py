"""Idempotent application of config events into the gateway read-model (FRD-204).

Every handler is an upsert or delete keyed by natural keys, so re-delivering an event (or
replaying a compacted topic) converges to the same state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aira_common.money import to_nanos
from aira_gateway.db.models import (
    AnomalyRuleRead,
    ApiKey,
    BudgetRead,
    BudgetUsage,
    ModelRead,
    PipelineConfigRead,
    RateLimitRead,
    UseCaseGroupRead,
    UseCaseMemberRead,
    UseCaseRead,
)
from aira_gateway.retention import DEFAULT_RETENTION_DAYS


def _price_nanos(value: object) -> int | None:
    """Prices arrive as exact decimal strings; absent means "no price on file"."""
    return None if value is None else to_nanos(str(value))


async def apply_event(session: AsyncSession, event_type: str, payload: dict[str, Any]) -> None:
    """Apply one config event; unknown types are ignored (forward-compatible)."""
    if event_type == "usecase.upserted":
        await _upsert_usecase(session, payload)
    elif event_type == "usecase.deleted":
        await _delete_usecase(session, payload["slug"])
    elif event_type == "membership.upserted":
        await _upsert_member(session, payload)
    elif event_type == "membership.removed":
        await _remove_member(session, payload["slug"], payload["username"])
    elif event_type == "use_case_group.granted":
        await _upsert_group_grant(session, payload)
    elif event_type == "use_case_group.revoked":
        await _remove_group_grant(session, payload["slug"], payload["group"])
    elif event_type == "api_key.created":
        await _upsert_api_key(session, payload)
    elif event_type == "api_key.revoked":
        await _set_api_key_active(session, payload["prefix"], active=False)
    elif event_type == "pipeline.upserted":
        await _upsert_pipeline(session, payload)
    elif event_type == "pipeline.deleted":
        await _delete_pipeline(session, payload["use_case"])
    elif event_type == "budget.upserted":
        await _upsert_budget(session, payload)
    elif event_type == "budget.deleted":
        await _delete_budget(session, payload["id"])
    elif event_type == "ratelimit.upserted":
        await _upsert_rate_limit(session, payload)
    elif event_type == "ratelimit.deleted":
        await _delete_rate_limit(session, payload["id"])
    elif event_type == "anomaly_rule.upserted":
        await _upsert_anomaly_rule(session, payload)
    elif event_type == "anomaly_rule.deleted":
        await _delete_anomaly_rule(session, payload["id"])
    elif event_type == "model.upserted":
        await _upsert_model(session, payload)
    elif event_type == "model.deleted":
        await _delete_model(session, payload["name"])
    else:
        return
    await session.commit()


async def _upsert_usecase(session: AsyncSession, payload: dict[str, Any]) -> None:
    existing = await session.get(UseCaseRead, payload["slug"])
    fields = {
        "name": payload.get("name", ""),
        "description": payload.get("description", ""),
        "processing_notes": payload.get("processing_notes", ""),
        # Older Management versions do not send these; the defaults keep today's behaviour for
        # storage and the conservative promise for retention.
        "store_payloads": bool(payload.get("store_payloads", True)),
        "retention_days": int(payload.get("retention_days") or DEFAULT_RETENTION_DAYS),
    }
    if existing is None:
        session.add(UseCaseRead(slug=payload["slug"], **fields))
    else:
        for key, value in fields.items():
            setattr(existing, key, value)


async def _delete_usecase(session: AsyncSession, slug: str) -> None:
    """Remove a use case and everything that hung off it.

    Management cascades the deletion in its own database but publishes only ``usecase.deleted``,
    so this is the one place the gateway can learn that the children are gone. Leaving them was a
    real defect: an API key kept authenticating after its use case had been deleted, so whoever
    deleted it believed access had ended when it had not — and a slug created again later
    silently inherited the old budgets, limits and pipeline.

    Two deliberate asymmetries:

    - Keys are **deactivated, not deleted**. Delivery is at-least-once, so a re-delivered
      ``api_key.created`` would otherwise resurrect one; revocation has to be terminal for the
      same reason it is in :func:`_upsert_api_key` (ADR-0007).
    - ``request_logs`` are **kept**. The audit trail and the spend history are what a later
      question about what was spent, and by whom, is answered from; they outlive the use case on
      purpose (FRD-404 §4.1). Their payloads still expire on the retention clock.

    This tombstone is the *only* place the check belongs. Refusing a key at authentication time
    because its use case is unknown looks like cheap defence in depth and is not: keys and use
    cases arrive on different Kafka topics with no ordering between them, so a freshly issued key
    can legitimately reach the gateway before the use case it belongs to, and the check would
    refuse it.
    """
    await session.execute(update(ApiKey).where(ApiKey.use_case == slug).values(is_active=False))
    await session.execute(delete(BudgetRead).where(BudgetRead.use_case == slug))
    # Group grants go too. Leaving one would let a re-created slug silently inherit access an
    # entire department still holds — the same defect the keys had, one route further out.
    await session.execute(delete(UseCaseGroupRead).where(UseCaseGroupRead.use_case_slug == slug))
    await session.execute(delete(RateLimitRead).where(RateLimitRead.use_case == slug))
    # A rule scoped to this use case goes with it; a **global** rule does not, and the filter says
    # so explicitly. `use_case IS NULL` means "everywhere", and a cascade that swept those away
    # would let deleting one use case silently switch off detection for every other.
    await session.execute(delete(AnomalyRuleRead).where(AnomalyRuleRead.use_case == slug))
    await session.execute(delete(PipelineConfigRead).where(PipelineConfigRead.use_case == slug))
    # Usage counters are keyed by scope, not by a foreign key: "uc:<slug>" for the whole use case
    # and "member:<slug>:<subject>" for each member.
    await session.execute(
        delete(BudgetUsage).where(
            (BudgetUsage.scope_key == f"uc:{slug}")
            | (BudgetUsage.scope_key.startswith(f"member:{slug}:"))
        )
    )
    await session.execute(delete(UseCaseMemberRead).where(UseCaseMemberRead.use_case_slug == slug))
    await session.execute(delete(UseCaseRead).where(UseCaseRead.slug == slug))


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


async def _remove_group_grant(session: AsyncSession, slug: str, group_path: str) -> None:
    await session.execute(
        delete(UseCaseGroupRead).where(
            UseCaseGroupRead.use_case_slug == slug,
            UseCaseGroupRead.group_path == group_path,
        )
    )


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


async def _remove_member(session: AsyncSession, slug: str, subject: str) -> None:
    await session.execute(
        delete(UseCaseMemberRead).where(
            UseCaseMemberRead.use_case_slug == slug, UseCaseMemberRead.subject == subject
        )
    )


def _moment(value: Any) -> datetime | None:
    """Parse an ISO-8601 instant from an event, or ``None``.

    An unparsable value yields ``None`` rather than raising: the event stream must not stall on one
    malformed field, and for an *expiry* the failure direction is the safe one only because the
    key is still revocable by hand. Nothing else in the payload is optional in this way.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def _upsert_api_key(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a Management-issued API key into the read-model, keyed by prefix (FRD-205).

    Delivery is at-least-once, so a ``created`` event can be re-delivered *after* the matching
    ``revoked`` event. Revocation is therefore terminal here: an existing record's metadata is
    refreshed, but a key that has been deactivated is never brought back to life (ADR-0007).
    """
    result = await session.execute(select(ApiKey).where(ApiKey.prefix == payload["prefix"]))
    record = result.scalar_one_or_none()
    if record is None:
        session.add(
            ApiKey(
                prefix=payload["prefix"],
                key_hash=payload["key_hash"],
                subject=payload.get("subject", ""),
                use_case=payload.get("use_case"),
                label=payload.get("label"),
                is_active=True,
                expires_at=_moment(payload.get("expires_at")),
            )
        )
    else:
        record.key_hash = payload["key_hash"]
        record.subject = payload.get("subject", "")
        record.use_case = payload.get("use_case")
        record.label = payload.get("label")
        record.expires_at = _moment(payload.get("expires_at"))


async def _set_api_key_active(session: AsyncSession, prefix: str, *, active: bool) -> None:
    result = await session.execute(select(ApiKey).where(ApiKey.prefix == prefix))
    record = result.scalar_one_or_none()
    if record is not None:
        record.is_active = active


async def _upsert_pipeline(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a use case's pipeline config, keyed by use case (FRD-300)."""
    steps = payload.get("steps", [])
    fallback = payload.get("fallback_models", [])
    record = await session.get(PipelineConfigRead, payload["use_case"])
    if record is None:
        session.add(
            PipelineConfigRead(use_case=payload["use_case"], steps=steps, fallback_models=fallback)
        )
    else:
        record.steps = steps
        record.fallback_models = fallback


async def _delete_pipeline(session: AsyncSession, use_case: str) -> None:
    await session.execute(delete(PipelineConfigRead).where(PipelineConfigRead.use_case == use_case))


async def _upsert_budget(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a budget definition into the read-model, keyed by id (FRD-400)."""
    record = await session.get(BudgetRead, payload["id"])
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
    if record is None:
        session.add(BudgetRead(id=payload["id"], **fields))
    else:
        for key, value in fields.items():
            setattr(record, key, value)


async def _delete_budget(session: AsyncSession, budget_id: int) -> None:
    await session.execute(delete(BudgetRead).where(BudgetRead.id == budget_id))


async def _upsert_rate_limit(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a request-rate limit into the read-model, keyed by id (FRD-405)."""
    record = await session.get(RateLimitRead, payload["id"])
    fields = {
        "use_case": payload["use_case"],
        "scope": payload["scope"],
        "subject": payload.get("subject", ""),
        "limit_rpm": int(payload.get("limit_rpm") or 0),
        "burst": int(payload.get("burst") or 0),
        "enabled": payload.get("enabled", True),
    }
    if record is None:
        session.add(RateLimitRead(id=payload["id"], **fields))
    else:
        for key, value in fields.items():
            setattr(record, key, value)


async def _delete_rate_limit(session: AsyncSession, limit_id: int) -> None:
    await session.execute(delete(RateLimitRead).where(RateLimitRead.id == limit_id))


async def _upsert_anomaly_rule(session: AsyncSession, payload: dict[str, Any]) -> None:
    existing = await session.get(AnomalyRuleRead, payload["id"])
    fields = {
        # `None` means the rule is global. An older Management that sends no key at all would be
        # read as global, which is the wrong default for a rule that can block traffic — so a
        # missing key is treated as a malformed event and the rule is skipped rather than widened.
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
    if existing is None:
        session.add(AnomalyRuleRead(id=payload["id"], **fields))
    else:
        for key, value in fields.items():
            setattr(existing, key, value)


async def _delete_anomaly_rule(session: AsyncSession, rule_id: int) -> None:
    await session.execute(delete(AnomalyRuleRead).where(AnomalyRuleRead.id == rule_id))


#: Declaration fields, with the value applied when the event does not carry them at all.
#:
#: The defaults matter during a rolling deploy: an older Management sends the FRD-403 payload with
#: no capability fields, and the consumer must apply the prices it *did* send without blanking a
#: declaration somebody made — while a payload that carries the field with a null clears it, which
#: is the same event saying "this model no longer declares that".
_DECLARATION_DEFAULTS: dict[str, Any] = {
    "capabilities": None,
    "publisher": "",
    "platform": "",
    "addressing": None,
    "underlying_model": "",
    "max_output_tokens": None,
    "default_max_output_tokens": None,
    "thinking": None,
    "embedding": None,
    "attachments": None,
    "hosting": "",
    "deprecated": False,
    "numeric_id": None,
}


async def _upsert_model(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a catalogued model, keyed by model name (FRD-403, FRD-114)."""
    fields: dict[str, Any] = {
        "display_name": payload.get("display_name", ""),
        "provider": payload.get("provider", ""),
        "input_price_per_million_nanos": _price_nanos(payload.get("input_price_per_million")),
        "output_price_per_million_nanos": _price_nanos(payload.get("output_price_per_million")),
    }
    for field, default in _DECLARATION_DEFAULTS.items():
        if field in payload:
            fields[field] = payload[field] if payload[field] is not None else default
    record = await session.get(ModelRead, payload["name"])
    if record is None:
        session.add(ModelRead(model=payload["name"], **fields))
    else:
        for key, value in fields.items():
            setattr(record, key, value)


async def _delete_model(session: AsyncSession, name: str) -> None:
    await session.execute(delete(ModelRead).where(ModelRead.model == name))
