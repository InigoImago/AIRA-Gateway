"""Running steps over a payload that is only text — an embedding's batch (FRD-113, FRD-309).

A batch is **one step over many texts**: one decision, one billed call and one outcome for the
request, however many texts it held.
"""

from __future__ import annotations

from typing import Any

from aira_gateway.audit import APPLIED_ACTIONS, ModelCall
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, CanonicalUsage, Role
from aira_gateway.pipeline.config import PipelineStep, StepType
from aira_gateway.pipeline.engine.outcomes import StepEvaluation

#: Which steps mean anything for text alone. There is no answer to route and nothing that will obey
#: the text, so `model_route` and `injection_filter` do not apply — blocking would refuse a corpus
#: for quoting the phrases it exists to index. `pii_filter` is about where the caller's text goes
#: and what is stored, which is the same question for a text being embedded.
TEXT_ONLY_STEPS = frozenset({StepType.PII_FILTER})

#: How many texts of a batch are redacted at once. Each text is its own call, because a redaction is
#: checked per text (`FRD-309` FR-4): sequential makes a large batch unusable, unbounded opens as
#: many upstream connections as a caller asks for. Tune `AIRA_MAX_EMBEDDING_BATCH` first.
REDACTIONS_AT_ONCE = 8


def _one_message(text: str, model: str) -> CanonicalRequest:
    """One text as the request a step expects: to a redactor it *is* a user message."""
    return CanonicalRequest(model=model, messages=[CanonicalMessage(role=Role.USER, text=text)])


def _summed(step: str, calls: list[ModelCall]) -> ModelCall | None:
    """One `ModelCall` for a step that made several — the money exact, the row count sane.

    256 rows named `pipeline:pii_filter` for one request would bury the caller's own row; the summed
    usage prices the same. Only calls to one model can be summed, which a step guarantees: its model
    comes from its configuration, not from the text.
    """
    if not calls:
        return None
    return ModelCall(
        step=step,
        model=calls[0].model,
        usage=CanonicalUsage(
            prompt_tokens=sum(call.usage.prompt_tokens for call in calls),
            completion_tokens=sum(call.usage.completion_tokens for call in calls),
        ),
    )


def _worst(evaluations: list[StepEvaluation]) -> StepEvaluation | None:
    """The evaluation that describes what happened to the **request**: the least good of them.

    A refusal wins, because that is what the caller was told; then any text that was not applied —
    under `on_failure: allow` nothing else would say so, and `redaction_failed` reads this row.
    """
    if not evaluations:
        return None
    blocked = next((e for e in evaluations if e.block_reason is not None), None)
    if blocked is not None:
        return blocked
    return next((e for e in evaluations if e.action not in APPLIED_ACTIONS), evaluations[0])


def _over_the_batch(
    step: PipelineStep,
    evaluations: list[StepEvaluation],
    notable: StepEvaluation | None,
) -> dict[str, Any] | None:
    """One audit decision for a step over a batch: the notable outcome, how many texts, how many
    changed. The keys are the generation path's, with `texts` and `changed` added."""
    if not evaluations or notable is None or notable.decision is None:
        return None
    changed = sum(1 for evaluation in evaluations if evaluation.rewrote is not None)
    return {
        **notable.decision,
        "step": str(step.type),
        "texts": len(evaluations),
        "changed": changed,
    }
