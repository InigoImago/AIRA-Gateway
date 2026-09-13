"""The pipeline engine: runs a use case's steps in order, for real or as a dry run (FRD-300/306).

Every runner takes caller-supplied ``decisions``, ``model_calls`` and ``rewrites`` lists and appends
to them as it goes, so what a step decided, spent and rewrote survives a later step raising
``PipelineRejected``: a blocked request still records why (`FRD-122` FR-4), still bills its
classifier calls (`FRD-125`), and still stores the redacted prompt rather than the original.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from aira_gateway.audit import ModelCall
from aira_gateway.core.canonical import CanonicalRequest
from aira_gateway.pipeline.config import Pipeline, PipelineStep
from aira_gateway.pipeline.engine.outcomes import (
    DryRunResult,
    PipelineOutcome,
    StepEvaluation,
    TextsOutcome,
    TraceEntry,
)
from aira_gateway.pipeline.engine.steps import DeclarationOf, StepEvaluator
from aira_gateway.pipeline.engine.texts import (
    REDACTIONS_AT_ONCE,
    TEXT_ONLY_STEPS,
    _one_message,
    _over_the_batch,
    _summed,
    _worst,
)
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.upstreams.base import ProviderRegistry


class PipelineEngine:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._steps = StepEvaluator(registry)

    async def run(
        self,
        pipeline: Pipeline,
        request: CanonicalRequest,
        *,
        decisions: list[dict[str, Any]] | None = None,
        model_calls: list[ModelCall] | None = None,
        rewrites: list[tuple[str, str]] | None = None,
        declaration_of: DeclarationOf | None = None,
    ) -> PipelineOutcome:
        """Run the configured steps, raising ``PipelineRejected`` on the first block.

        ``declaration_of`` lets a step reach a model the catalogue knows and configuration does not
        (`FRD-507`); without it such a step finds no provider and does less.
        """
        outcome = PipelineOutcome(
            request=request,
            fallback_models=pipeline.fallback_models,
            decisions=decisions if decisions is not None else [],
            model_calls=model_calls if model_calls is not None else [],
            rewrites=rewrites if rewrites is not None else [],
        )
        for step in pipeline.steps:
            evaluation = await self._steps.evaluate(step, outcome.request, declaration_of)
            if evaluation.call is not None:
                outcome.model_calls.append(evaluation.call)
            if evaluation.decision is not None:
                outcome.decisions.append(evaluation.decision)
            if evaluation.request is not None:
                outcome.request = evaluation.request
            if evaluation.notice:
                outcome.notices.append(evaluation.notice)
            if evaluation.rewrote is not None:
                outcome.rewrites.append(evaluation.rewrote)
            if evaluation.block_reason is not None:
                raise PipelineRejected(evaluation.block_reason)
        return outcome

    async def run_over_texts(
        self,
        pipeline: Pipeline,
        texts: Sequence[str],
        *,
        model: str = "",
        decisions: list[dict[str, Any]] | None = None,
        model_calls: list[ModelCall] | None = None,
        rewrites: list[tuple[str, str]] | None = None,
        declaration_of: DeclarationOf | None = None,
    ) -> TextsOutcome:
        """Run the steps that mean anything for texts alone — an embedding (`TEXT_ONLY_STEPS`).

        The same step code `run` uses, over a one-message request per text — not a second, more
        permissive implementation of redaction. ``model`` only labels the synthetic request; a step
        reads its model from its own configuration. **A refusal of any text refuses the request**:
        half a batch of vectors is not an answer.
        """
        outcome = TextsOutcome(
            texts=tuple(texts),
            decisions=decisions if decisions is not None else [],
            model_calls=model_calls if model_calls is not None else [],
            rewrites=rewrites if rewrites is not None else [],
        )
        for step in pipeline.steps:
            if step.type not in TEXT_ONLY_STEPS:
                continue
            evaluations = await self._evaluate_each(step, outcome.texts, model, declaration_of)

            # Spend and rewrites first, as in `run`: recorded whether or not the step refuses.
            call = _summed(str(step.type), [e.call for e in evaluations if e.call])
            if call is not None:
                outcome.model_calls.append(call)
            outcome.rewrites.extend(e.rewrote for e in evaluations if e.rewrote is not None)

            notable = _worst(evaluations)
            decision = _over_the_batch(step, evaluations, notable)
            if decision is not None:
                outcome.decisions.append(decision)
            if notable is not None and notable.block_reason is not None:
                raise PipelineRejected(notable.block_reason)

            outcome.texts = tuple(
                evaluation.request.last_user_text() if evaluation.request is not None else text
                for text, evaluation in zip(outcome.texts, evaluations, strict=True)
            )
        return outcome

    async def _evaluate_each(
        self,
        step: PipelineStep,
        texts: Sequence[str],
        model: str,
        declaration_of: DeclarationOf | None,
    ) -> list[StepEvaluation]:
        """One evaluation per text, a bounded handful at a time, **in the texts' order** —
        `asyncio.gather` keeps argument order, and a rewrite applied to the wrong text is silent."""
        limit = asyncio.Semaphore(REDACTIONS_AT_ONCE)

        async def one(text: str) -> StepEvaluation:
            async with limit:
                return await self._steps.evaluate(step, _one_message(text, model), declaration_of)

        return list(await asyncio.gather(*(one(text) for text in texts)))

    async def dry_run(
        self,
        pipeline: Pipeline,
        request: CanonicalRequest,
        *,
        model_calls: list[ModelCall] | None = None,
        declaration_of: DeclarationOf | None = None,
        past_blocks: bool = False,
    ) -> DryRunResult:
        """Evaluate the pipeline without dispatching the caller's own generation.

        ``past_blocks`` keeps evaluating after a step refuses, which **production never does**, so
        an operator can test the steps behind a blocking filter. Off by default: the default answer
        must be production's, and every step past a block spends real tokens. Entries after the
        block are marked so the screen can label them a simulation.
        """
        trace: list[TraceEntry] = []
        calls: list[ModelCall] = model_calls if model_calls is not None else []
        current = request
        blocked = False
        reason: str | None = None
        for step in pipeline.steps:
            evaluation = await self._steps.evaluate(step, current, declaration_of)
            if evaluation.call is not None:
                calls.append(evaluation.call)
            if evaluation.request is not None:
                current = evaluation.request
            # `blocked` is read before this step's own outcome, so the blocking step is not marked
            # as coming after itself.
            trace.append(TraceEntry(evaluation.type, evaluation.action, evaluation.detail, blocked))
            if evaluation.block_reason is not None and not blocked:
                # The **first** refusal is the one that describes production.
                blocked, reason = True, evaluation.block_reason
                if not past_blocks:
                    break
        return DryRunResult(trace, blocked, reason, current.model, pipeline.fallback_models, calls)
