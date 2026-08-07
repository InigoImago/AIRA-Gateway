"""What the audit trail records beyond "a request happened" (FRD-122).

Three facts the request log could not state before, each of which defeats a question the trail
exists to answer:

- **What was asked.** A refused request produced no row at all, so the log held what was *served*,
  not what was *asked*. A control that leaves no trace when it fires is a control nobody can review.
- **What was decided.** With routing and cross-vendor fallback (`ADR-0012`) the model that answers
  is not necessarily the one that was named.
- **Which system asked.** An API key's *identity* is its prefix; its ``subject`` is the person who
  issued it. Recording only the subject makes five keys of one use case one identity in the log —
  which is exactly the wrong shape on the day one of them leaks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aira_gateway.core.canonical import CanonicalUsage


class Outcome(StrEnum):
    """Why a request ended the way it did.

    A **closed** vocabulary on purpose: reporting groups by it (`FRD-601`), and free text would be
    greppable and nothing more. Adding a control means adding a value here, and the reporting
    labels are derived from this enum rather than restated, so a new value cannot show up as an
    unlabelled bucket.
    """

    SERVED = "served"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXCEEDED = "budget_exceeded"
    BLOCKED_BY_PIPELINE = "blocked_by_pipeline"
    MODEL_NOT_FOUND = "model_not_found"
    INVALID_REQUEST = "invalid_request"
    UPSTREAM_ERROR = "upstream_error"
    #: No candidate model could serve the request — e.g. none can read the attachment it carries
    #: (`ADR-0012` §3). Reachable once `FRD-110` lands; declared here so the vocabulary is complete.
    NO_CAPABLE_MODEL = "no_capable_model"
    #: Refused on size, before any route ran. Its own value rather than `invalid_request`:
    #: "somebody keeps posting 20 MB" and "somebody sent malformed JSON" are different operational
    #: facts, and a shared bucket would hide the first inside the second.
    REQUEST_TOO_LARGE = "request_too_large"
    #: The caller went away before the answer was ready. Its own value rather than an upstream
    #: error: the upstream did nothing wrong, and "clients keep hanging up" is a different thing
    #: to investigate from "the provider keeps failing" (`FRD-128`).
    CLIENT_GONE = "client_gone"


#: How the served model was arrived at. ``fallback:N`` names the candidate's position in the chain.
SELECTION_DIRECT = "direct"
SELECTION_ROUTE = "route"


def fallback_selection(index: int) -> str:
    """``fallback:N`` for candidate ``index`` of the dispatch chain (0 is the primary)."""
    return SELECTION_DIRECT if index == 0 else f"fallback:{index}"


@dataclass(frozen=True, slots=True)
class ModelCall:
    """A model call a **pipeline step** made while deciding what to do with the caller's request.

    It exists because one caller request with an LLM step makes two model calls and used to leave
    one audit row. The second call was invisible three ways at once: `FRD-601` reported a spend it
    was not part of, `FRD-403`'s "unpriced traffic is counted apart, never as zero" was violated by
    counting it as *nothing*, and `ADR-0013`'s auditable model access had a model call in it that
    nothing recorded. All three follow from the same omission, so one record fixes all three.
    """

    step: str
    model: str
    usage: CanonicalUsage


@dataclass
class AuditTrail:
    """What a route learns about a request as it goes, kept so a refusal can still be recorded.

    Mutable and passed down deliberately. The alternative — assembling the row at each exit — is
    how `:embedContent` once ended up bypassing the pre-dispatch gate: a fact that has to be
    repeated at every ``return`` is a fact that will eventually be forgotten at one of them.
    """

    operation: str = "unknown"
    #: What the caller named, before any routing or fallback. Never overwritten.
    requested_model: str = ""
    #: What actually answered. Equal to ``requested_model`` unless routing or fallback intervened.
    model: str | None = None
    selection: str = SELECTION_DIRECT
    #: One entry per pipeline step that ran: its type and its verdict. Never the classifier's
    #: reasoning text — that is model output about a caller's prompt and inherits every
    #: data-protection question the prompt itself has (FRD-122 §5.3).
    decisions: list[dict[str, Any]] = field(default_factory=list)
    #: The parsed request body, so a refusal can be recorded with what was actually sent.
    body: dict[str, Any] | None = None
    #: Model calls made *by the pipeline*, not by the caller. Recorded even when the request was
    #: then refused — a filter that blocked still spent the tokens it took to decide that, and a
    #: use case running a blocking filter over rejected traffic is paying for exactly those.
    model_calls: list[ModelCall] = field(default_factory=list)

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
        """Record the candidates the chain declined, and why.

        Kept with the pipeline decisions rather than in a column of its own: it is the same kind
        of fact — a decision the gateway took about this request — and a fallback that skipped
        three models for three different reasons is exactly what somebody needs to see when they
        ask why an answer came from the model it did.
        """
        for entry in skipped:
            self.decisions.append(
                {"step": "dispatch", "action": "skipped", "to": entry.model, "why": entry.reason}
            )

    def served_by(self, model: str, candidate_index: int) -> None:
        """Record which candidate of the dispatch chain answered."""
        self.model = model
        if candidate_index > 0:
            self.selection = fallback_selection(candidate_index)


#: What may be kept from a pipeline decision. An **allow-list**, not a deny-list: a future step
#: that records the classifier's explanation would otherwise start persisting model output about a
#: caller's prompt the day it is added, silently, in a column redaction cannot process.
SAFE_DECISION_KEYS = frozenset({"step", "action", "flagged", "category", "from", "to", "why"})


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
