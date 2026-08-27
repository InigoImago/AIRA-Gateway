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

#: What names an audit row that is **not** a caller's own request: the model call a pipeline step
#: made on its way to a decision (`FRD-125` FR-8, ``pipeline:<step>``).
#:
#: A constant because two places need the same answer and had different ones — briefly in each
#: direction. `FR-9` booked those calls with ``requests=0`` while reporting counted rows; the owner
#: then reversed the rule (`FR-9b`, 2026-08-15) and both sides count them. The prefix survives the
#: reversal because it is what still tells the two kinds of row apart on a screen: `by_model` and
#: the operation give a reader the split, and only the *total* is deliberately one number.
PIPELINE_OPERATION_PREFIX = "pipeline:"

# `is_pipeline_operation(operation)` stood here until 2026-08-20. It was written for the reporting
# split that `FR-9b` then reversed — both kinds of row are counted now — and nothing has called it
# since. The prefix above still earns its place, because it is what tells the two kinds apart on a
# screen; a predicate nobody asks is a rule this module claims and does not apply.


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
    #: Stopped on purpose, by a rule that fired or by a person in an incident (`FRD-503`). Its own
    #: value rather than `rate_limited`: "we stopped this caller" and "this caller is going too
    #: fast" want different answers, and a shared bucket would hide the first inside the second.
    SUSPENDED = "suspended"
    #: **An administrator asking a model about itself** (`FRD-610`) — the console's reachability and
    #: thinking-word checks. Not a caller's traffic: it belongs to no use case, answers nobody, and
    #: exists so that a declaration can be verified before anything is released against it.
    #:
    #: Its own value rather than `served`, for the reason this vocabulary is closed at all. These
    #: calls **spend money**, so they have to be in the audit trail — and counted as *served* they
    #: would inflate every use case's request figure with traffic no use case made, which is the
    #: shape `FRD-125b` refused for pipeline calls. Separable is what makes them governable:
    #: *"what did diagnostics cost this month"* is a question somebody can now ask.
    DIAGNOSTIC = "diagnostic"


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

    operation: str
    #: Which surface this request arrived on, carried so every recording site agrees.
    #:
    #: **It was a defaulted parameter of `record_request` and that made one surface right by
    #: accident.** `api: str = "gemini"` meant a call site that forgot it produced a Gemini row —
    #: correct for the Gemini surface, wrong for the other, and silent either way. Measured on
    #: 2026-08-13: a KIRA request whose pipeline ran an LLM filter left its classifier row under
    #: `api='gemini'`, so a use case's *governance* spend was reported against a surface it never
    #: used (`FRD-125b` exists precisely so that spend is visible and attributable).
    #:
    #: On the trail rather than passed down, because the trail is created once per request by the
    #: surface that owns it and already reaches every exit — while `record_pipeline_calls` sits in
    #: the shared layer and has no other way to know. A discriminator that each call site restates
    #: is a discriminator one of them eventually restates wrongly.
    #:
    #: **No default here either**, which is why ``operation`` lost its own: a dataclass cannot put
    #: a required field after a defaulted one, and the alternative — leaving ``api`` defaulted
    #: because rearranging is inconvenient — is the accident moved one level up, where a third
    #: surface would inherit "gemini" from a field it never knew existed.
    api: str
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
    #: How many functions the caller offered this request (`FRD-131` FR-7). Zero for the ordinary
    #: request, and worth recording because "offered ten, asked for none" and "offered none" are
    #: different events — only one of them is a model behaving oddly.
    tools_declared: int = 0
    #: The names the model asked to have run, in order. **Names only**: arguments are caller
    #: content and belong under `store_payloads`, inside the retention clock and behind `FRD-406`'s
    #: redaction — not in a metadata column no clock covers.
    #:
    #: Lives on the trail rather than on either surface, for the reason `FRD-126` gives: the
    #: streaming path and the buffered path are two exits, and a fact recorded at one of them is a
    #: fact eventually missing from the other. It was, in fact, missing from the streamed one for
    #: the length of an afternoon — the audit row of a real assistant turn read `{"text": ""}`,
    #: because a streamed tool call has no text delta to accumulate and nothing else looked.
    tool_calls: list[str] = field(default_factory=list)
    #: Model calls made *by the pipeline*, not by the caller. Recorded even when the request was
    #: then refused — a filter that blocked still spent the tokens it took to decide that, and a
    #: use case running a blocking filter over rejected traffic is paying for exactly those.
    model_calls: list[ModelCall] = field(default_factory=list)

    #: Where the answer was actually produced, when the adapter said (`FRD-609`, `FRD-115` FR-10).
    #:
    #: Empty for every dialect that has one place, and for a request that never reached one. On
    #: this trail rather than beside either exit, for the reason `tool_calls` above gives and paid
    #: for: a fact recorded at each `return` is a fact eventually missing from one of them.
    #:
    #: It exists because a model may now be catalogued in several regions and tried in order, so
    #: *"the configuration says europe-west1"* and *"this request went to europe-west1"* stopped
    #: being the same sentence. The audit row had only the first — `provenance_for(provider)`
    #: answers with the region of the first *configured* model on that adapter — which is right for
    #: a configured model, a guess for a catalogued one, and would have been a confident wrong
    #: residency claim on every request that used a fallback region.
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
#:
#: `texts` and `changed` are **counts**, added when a step began running over an embedding batch
#: (`FRD-113`, 2026-08-27): how many texts the step saw and how many it rewrote. Two integers about
#: the request's shape, never about its content — which is the test every key here has to pass, and
#: the reason the list is enumerated rather than inherited.
SAFE_DECISION_KEYS = frozenset(
    {"step", "action", "flagged", "category", "from", "to", "why", "texts", "changed"}
)


#: The actions a step reports when it **did** what it is for.
#:
#: Anything else a `pii_filter` can say — `blocked`, or `allowed` under `on_failure: allow` — means
#: it could not apply its rule to that text. The distinction is not cosmetic: it decides whether
#: the caller's original text may be kept (see :func:`redaction_failed`).
APPLIED_ACTIONS = frozenset({"redacted", "unchanged", "passed"})


def redaction_failed(decisions: list[dict[str, Any]]) -> bool:
    """Whether a `pii_filter` ran and could not produce a usable rewrite (`FRD-309` FR-3).

    FR-3 is unconditional: *"where the substitution cannot be applied the payload is **dropped**,
    never kept"*. Only half of that was implemented. `_rewritten_body` drops the payload when a
    rewrite's own text cannot be found in the wire body — a body it does not understand — and
    nothing dropped it in the other case, which is the commoner one: **the redactor failed, so
    there was no rewrite at all**.

    Measured on 2026-08-27 with an unreachable redactor: `400 blocked_by_pipeline` on both
    `:generateContent` and `:embedContent`, nobody served, and `request_logs.request_payload`
    holding the caller's name and address on both rows. The same shape as the defect found the day
    before — personal data kept in the audit row of a request nobody was served — arriving through
    the other door.

    **`on_failure: allow` drops it too**, and that is the part worth stating. The operator who set
    that flag chose to keep *serving* when the redactor is down; they did not choose to keep
    *storing*, and folding two decisions into one flag is how a control comes to mean something
    nobody asked for. The decision row still records the step, the action and why, so what happened
    stays visible; what goes is the content the step exists to remove.

    Read off the decisions rather than carried on a flag of its own, because the decisions are
    already the caller-supplied list that survives a step raising — the same reason `rewrites` is
    passed in — and a second channel for the same fact is a second thing to forget to pass.
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

    An **allow-list**, in the shape `FRD-122` established: what reaches the column is enumerated
    here and nowhere else, so a later change cannot start persisting a tool's arguments by
    forgetting to exclude them.
    """
    if not trail.tools_declared and not trail.tool_calls:
        return None
    return {"declared": trail.tools_declared, "called": list(trail.tool_calls)}


def was_flagged(decisions: list[dict[str, Any]] | None, outcome: str) -> bool:
    """Did a pipeline step object to this request (`FRD-505` FR-5)?

    Two ways to object, and both count. A step can **block** — which ends as
    `blocked_by_pipeline` — or it can **flag** and let the request through, which is the
    `injection_filter`'s `flag` action and leaves a served request nobody would otherwise look at
    twice. The second is the more interesting of the two on a screen: a blocked request already
    announces itself by failing, while a flagged one is a 200 with a note attached.
    """
    if outcome == Outcome.BLOCKED_BY_PIPELINE.value:
        return True
    return any(bool(decision.get("flagged")) for decision in decisions or ())
