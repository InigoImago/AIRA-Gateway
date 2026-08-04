"""Idempotent application of config events into the gateway read-model (FRD-204).

Every handler is an upsert or delete keyed by natural keys, so re-delivering an event (or
replaying a compacted topic) converges to the same state.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from aira_gateway.db.models import UseCaseMemberRead, UseCaseRead


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
