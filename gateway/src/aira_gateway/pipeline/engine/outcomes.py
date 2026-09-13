"""What a pipeline run hands back: per step, per request, per text batch, and per dry run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aira_gateway.audit import ModelCall
from aira_gateway.core.canonical import CanonicalRequest


@dataclass
class StepEvaluation:
    """What one step did, in the vocabulary both consumers need.

    A step is **one function** returning this, and `run` and `dry_run` interpret it: `run` records
    for the audit and refuses, `dry_run` records for a screen and reports. Two hand-written chains
    over the same step types drifted apart; one answer read twice cannot.
    """

    type: str
    #: passed · flagged · blocked · allowed · rerouted · unchanged · not_asked · redacted
    action: str
    #: For the dry run's trace, which is a screen and may say more.
    detail: dict[str, Any] = field(default_factory=dict)
    #: For the audit trail, or `None` where there is nothing to record. Separate from `detail`:
    #: what a reader may see and what is kept durably are different questions (`FRD-122` §5.3).
    decision: dict[str, Any] | None = None
    call: ModelCall | None = None
    #: The request as this step leaves it; `None` where the step changed nothing.
    request: CanonicalRequest | None = None
    #: Set where the step refuses; `run` raises it, `dry_run` reports it.
    block_reason: str | None = None
    #: A sentence the caller is owed about their request having been changed (`FRD-309`). Carried
    #: out rather than applied: the pipeline runs before dispatch, so there is no answer yet.
    notice: str | None = None
    #: ``(before, after)`` where this step rewrote the caller's text, so the stored request can be
    #: the rewritten one as well as the dispatched one.
    rewrote: tuple[str, str] | None = None


@dataclass
class PipelineOutcome:
    """The request as the pipeline leaves it, and everything the steps decided, spent and wrote.

    The three lists may be the caller's own, so what a step recorded survives a later step blocking.
    """

    request: CanonicalRequest
    fallback_models: tuple[str, ...]
    decisions: list[dict[str, Any]] = field(default_factory=list)
    #: Model calls the steps made — including a step that then blocked (`FRD-125`).
    model_calls: list[ModelCall] = field(default_factory=list)
    #: Sentences the caller is owed about their request having been changed (`FRD-309`).
    notices: list[str] = field(default_factory=list)
    #: ``(before, after)`` for every rewrite, so the **stored** request is the rewritten one too:
    #: the audit row is written from the wire body, which the pipeline does not touch.
    rewrites: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class TextsOutcome:
    """What the pipeline made of a payload that is only text — an embedding.

    Not a `PipelineOutcome`: an embedding has no `CanonicalRequest` and no fallback chain. It shares
    the three caller-supplied lists, for the same reason.
    """

    texts: tuple[str, ...]
    decisions: list[dict[str, Any]] = field(default_factory=list)
    model_calls: list[ModelCall] = field(default_factory=list)
    rewrites: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class TraceEntry:
    type: str
    action: str  # passed | flagged | blocked | allowed | rejected | rerouted | unchanged
    detail: dict[str, Any]
    #: This step ran only because the caller asked the dry run to continue past a block. Production
    #: would already have refused, so the screen must label the entry as a simulation.
    after_block: bool = False


@dataclass
class DryRunResult:
    trace: list[TraceEntry]
    blocked: bool
    block_reason: str | None
    effective_model: str
    fallback_models: tuple[str, ...]
    #: What the dry run **spent** (`FRD-125b`): "dry" describes the dispatch that does not happen,
    #: never the classifier calls that do.
    model_calls: list[ModelCall] = field(default_factory=list)
