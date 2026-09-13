"""What the audit trail records beyond "a request happened" (FRD-122).

- **What was asked** — a refused request leaves a row too; a control that leaves no trace when it
  fires is a control nobody can review.
- **What was decided** — with routing and fallback (`ADR-0012`) the answering model need not be
  the named one.
- **Which system asked** — an API key's identity is its prefix, not the person who issued it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aira_gateway.core.canonical import CanonicalUsage

#: Names an audit row that is **not** a caller's own request: the model call a pipeline step made
#: (`FRD-125` FR-8, ``pipeline:<step>``). Both kinds are counted (`FR-9b`); the prefix is what tells
#: them apart on a screen.
PIPELINE_OPERATION_PREFIX = "pipeline:"

#: How the served model was arrived at. ``fallback:N`` names the candidate's position in the chain.
SELECTION_DIRECT = "direct"
SELECTION_ROUTE = "route"

#: What may be kept from a pipeline decision. An **allow-list**: a future step recording the
#: classifier's explanation must not start persisting model output about a prompt. `texts` and
#: `changed` are counts (`FRD-113`) — about the request's shape, never its content.
SAFE_DECISION_KEYS = frozenset(
    {"step", "action", "flagged", "category", "from", "to", "why", "texts", "changed"}
)

#: The actions a step reports when it **did** what it is for. Anything else from a `pii_filter`
#: means it could not apply its rule (see :func:`redaction_failed`).
APPLIED_ACTIONS = frozenset({"redacted", "unchanged", "passed"})


class Outcome(StrEnum):
    """Why a request ended the way it did.

    A **closed** vocabulary: reporting groups by it (`FRD-601`) and derives its labels from this
    enum, so a new value cannot show up as an unlabelled bucket.
    """

    SERVED = "served"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXCEEDED = "budget_exceeded"
    BLOCKED_BY_PIPELINE = "blocked_by_pipeline"
    MODEL_NOT_FOUND = "model_not_found"
    INVALID_REQUEST = "invalid_request"
    UPSTREAM_ERROR = "upstream_error"
    #: No candidate model could serve the request, e.g. none reads its attachment (`ADR-0012` §3).
    NO_CAPABLE_MODEL = "no_capable_model"
    #: Refused on size before any route ran — kept apart from malformed requests.
    REQUEST_TOO_LARGE = "request_too_large"
    #: The caller went away before the answer was ready; not an upstream fault (`FRD-128`).
    CLIENT_GONE = "client_gone"
    #: Stopped on purpose, by a rule or a person in an incident (`FRD-503`); not a rate limit.
    SUSPENDED = "suspended"
    #: An administrator asking a model about itself (`FRD-610`). These calls spend money, so they
    #: are recorded — but apart from `served`, so no use case's figures include them.
    DIAGNOSTIC = "diagnostic"


def fallback_selection(index: int) -> str:
    """``fallback:N`` for candidate ``index`` of the dispatch chain (0 is the primary)."""
    return SELECTION_DIRECT if index == 0 else f"fallback:{index}"


@dataclass(frozen=True, slots=True)
class ModelCall:
    """A model call a **pipeline step** made while deciding what to do with the caller's request.

    Recorded as its own row, or its spend (`FRD-601`), its unpriced-ness (`FRD-403`) and the model
    access itself (`ADR-0013`) would all be invisible.
    """

    step: str
    model: str
    usage: CanonicalUsage


@dataclass
class AuditTrail:
    """What a route learns about a request as it goes, kept so a refusal can still be recorded.

    Mutable and passed down deliberately: a fact that has to be repeated at every ``return`` is a
    fact eventually forgotten at one of them (the streaming and buffered exits included).
    """

    operation: str
    #: Which surface this request arrived on. **No default**: a defaulted surface once filed one
    #: surface's governance spend under the other, and a third surface would inherit it silently.
    api: str
    #: What the caller named, before any routing or fallback. Never overwritten.
    requested_model: str = ""
    #: What actually answered. Equal to ``requested_model`` unless routing or fallback intervened.
    model: str | None = None
    selection: str = SELECTION_DIRECT
    #: One entry per pipeline step that ran: its type and its verdict. Never the classifier's
    #: reasoning text — model output about a prompt inherits the prompt's data-protection questions
    #: (FRD-122 §5.3).
    decisions: list[dict[str, Any]] = field(default_factory=list)
    #: The parsed request body, so a refusal can be recorded with what was actually sent.
    body: dict[str, Any] | None = None
    #: How many functions the caller offered (`FRD-131` FR-7); "offered ten, called none" and
    #: "offered none" are different events.
    tools_declared: int = 0
    #: The names the model asked to have run, in order. **Names only**: arguments are caller
    #: content and belong under `store_payloads`, retention and `FRD-406` redaction.
    tool_calls: list[str] = field(default_factory=list)
    #: Model calls made *by the pipeline*. Recorded even when the request was then refused — a
    #: blocking filter still spent the tokens it took to decide.
    model_calls: list[ModelCall] = field(default_factory=list)
    #: Where the answer was actually produced, when the adapter said (`FRD-609`, `FRD-115` FR-10).
    #: With several catalogued regions tried in order, the configured region is not evidence of
    #: where a request went. Empty for single-place dialects and undispatched requests.
    served_region: str = ""

    @property
    def served_model(self) -> str:
        """The model to record. Falls back to the requested one for a request never dispatched."""
        return self.model or self.requested_model

    def routed_to(self, model: str) -> None:
        """A pipeline step chose a different model."""
        if model != self.requested_model:
            self.selection = SELECTION_ROUTE
        self.model = model

    def passed_over(self, skipped: list[Any]) -> None:
        """Record the candidates the chain declined, and why, alongside the pipeline decisions."""
        for entry in skipped:
            self.decisions.append(
                {"step": "dispatch", "action": "skipped", "to": entry.model, "why": entry.reason}
            )

    def served_by(self, model: str, candidate_index: int) -> None:
        """Record which candidate of the dispatch chain answered."""
        self.model = model
        if candidate_index > 0:
            self.selection = fallback_selection(candidate_index)


def redaction_failed(decisions: list[dict[str, Any]]) -> bool:
    """Whether a `pii_filter` ran and could not produce a usable rewrite (`FRD-309` FR-3).

    FR-3 is unconditional: where the substitution cannot be applied the payload is **dropped**,
    never kept. That includes `on_failure: allow` — that flag chooses to keep *serving* when the
    redactor is down, not to keep *storing*. The decision row still records what happened.
    """
    return any(
        decision.get("step") == "pii_filter" and str(decision.get("action")) not in APPLIED_ACTIONS
        for decision in decisions
    )


def decision_summary(decisions: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Reduce pipeline decisions to what is safe and useful to keep durably.

    ``None`` for an empty pipeline, so the column stays NULL rather than holding an empty list —
    "no pipeline ran" and "a pipeline ran and decided nothing" are different facts.
    """
    kept = [
        {key: value for key, value in decision.items() if key in SAFE_DECISION_KEYS}
        for decision in decisions
    ]
    return [entry for entry in kept if entry] or None


def tool_summary(trail: AuditTrail) -> dict[str, Any] | None:
    """What this request offered and what the model asked for, or ``None`` if neither.

    An **allow-list** (`FRD-122`): what reaches the column is enumerated here, so a later change
    cannot start persisting a tool's arguments by forgetting to exclude them.
    """
    if not trail.tools_declared and not trail.tool_calls:
        return None
    return {"declared": trail.tools_declared, "called": list(trail.tool_calls)}


def was_flagged(decisions: list[dict[str, Any]] | None, outcome: str) -> bool:
    """Did a pipeline step object to this request (`FRD-505` FR-5)?

    A step can **block** (`blocked_by_pipeline`) or **flag** and let the request through — a 200
    with a note attached, the one nobody would otherwise look at twice.
    """
    if outcome == Outcome.BLOCKED_BY_PIPELINE.value:
        return True
    return any(bool(decision.get("flagged")) for decision in decisions or ())
