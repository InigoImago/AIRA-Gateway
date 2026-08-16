"""Loads a use case's pipeline from the gateway read-model (FRD-300).

The config is fed from Management over Kafka (FRD-303/204). No config for a use case → None,
which the request path treats as pass-through.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aira_gateway.db.models import PipelineConfigRead
from aira_gateway.pipeline.config import Pipeline


class PipelineStore:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, use_case: str | None) -> Pipeline | None:
        if not use_case:
            return None
        async with self._sessionmaker() as session:
            record = await session.get(PipelineConfigRead, use_case)
        if record is None:
            return None
        pipeline = Pipeline.from_dict(
            {
                "steps": record.steps,
                "fallback_models": record.fallback_models,
                "start_model": record.start_model,
            }
        )
        return None if pipeline.is_empty else pipeline

    async def start_model(self, use_case: str | None) -> str:
        """Where a request enters this use case's pipeline when the caller names no model.

        Read separately from :meth:`get`, which answers ``None`` for a pipeline with no steps and
        no chain — and a use case may perfectly well declare only where a request starts. Folding
        the two would make "there is nothing to run" also mean "there is nowhere to start", which
        are different facts about different things (`ADR-0020`).
        """
        if not use_case:
            return ""
        async with self._sessionmaker() as session:
            record = await session.get(PipelineConfigRead, use_case)
        return "" if record is None else record.start_model
