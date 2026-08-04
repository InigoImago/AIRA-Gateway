"""Idempotent application of config events into the gateway read-model (FRD-204).

Every handler is an upsert or delete keyed by natural keys, so re-delivering an event (or
replaying a compacted topic) converges to the same state.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from aira_gateway.db.models import (
    ApiKey,
    BudgetRead,
    PipelineConfigRead,
    UseCaseMemberRead,
    UseCaseRead,
)


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
    else:
        return
    await session.commit()


async def _upsert_usecase(session: AsyncSession, payload: dict[str, Any]) -> None:
    existing = await session.get(UseCaseRead, payload["slug"])
    if existing is None:
        session.add(
            UseCaseRead(
                slug=payload["slug"],
                name=payload.get("name", ""),
                description=payload.get("description", ""),
                processing_notes=payload.get("processing_notes", ""),
            )
        )
    else:
        existing.name = payload.get("name", "")
        existing.description = payload.get("description", "")
        existing.processing_notes = payload.get("processing_notes", "")


async def _delete_usecase(session: AsyncSession, slug: str) -> None:
    await session.execute(delete(UseCaseMemberRead).where(UseCaseMemberRead.use_case_slug == slug))
    await session.execute(delete(UseCaseRead).where(UseCaseRead.slug == slug))


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


async def _upsert_api_key(session: AsyncSession, payload: dict[str, Any]) -> None:
    """Upsert a Management-issued API key into the read-model, keyed by prefix (FRD-205)."""
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
            )
        )
    else:
        record.key_hash = payload["key_hash"]
        record.subject = payload.get("subject", "")
        record.use_case = payload.get("use_case")
        record.label = payload.get("label")
        record.is_active = True


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
