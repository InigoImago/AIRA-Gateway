"""The pre-dispatch pipeline engine (FRD-300/306).

Walks a use case's ordered steps over the canonical request — ``injection_filter`` (heuristic or
LLM; may block), ``model_route`` (an LLM picks a category and its model) and ``pii_filter`` (a
trusted model rewrites personal data out of the prompt). ``run`` executes the pipeline and raises
``PipelineRejected`` on a block; ``dry_run`` evaluates it without raising and returns a per-step
trace for the builder.

    runner     PipelineEngine: run, run_over_texts, dry_run — the order and what is kept
    steps      StepEvaluator: one step, one answer
    texts      the steps over an embedding's batch of texts
    outcomes   what each of those hands back
"""

from aira_gateway.pipeline.engine.outcomes import (
    DryRunResult,
    PipelineOutcome,
    StepEvaluation,
    TextsOutcome,
    TraceEntry,
)
from aira_gateway.pipeline.engine.runner import PipelineEngine
from aira_gateway.pipeline.engine.steps import (
    UNDETERMINED_ALLOWS,
    UNDETERMINED_BLOCKS,
    DeclarationOf,
    StepEvaluator,
    _filled,
)
from aira_gateway.pipeline.engine.texts import REDACTIONS_AT_ONCE, TEXT_ONLY_STEPS

__all__ = [
    "REDACTIONS_AT_ONCE",
    "TEXT_ONLY_STEPS",
    "UNDETERMINED_ALLOWS",
    "UNDETERMINED_BLOCKS",
    "DeclarationOf",
    "DryRunResult",
    "PipelineEngine",
    "PipelineOutcome",
    "StepEvaluation",
    "StepEvaluator",
    "TextsOutcome",
    "TraceEntry",
    "_filled",
]
