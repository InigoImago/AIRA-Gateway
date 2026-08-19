"""Pre-dispatch pipeline engine (FRD-300/306).

Walks a use case's ordered steps over the canonical request:
- ``injection_filter`` — heuristic (built-in + custom patterns) or LLM classifier; may block.
- ``model_route`` — an LLM classifies the request (system + user) into one of the configured
  categories and routes to that category's model.

``run`` executes the pipeline (raising ``PipelineRejected`` on a block); ``dry_run`` evaluates it
without raising and returns a full per-step trace for the builder's preview/testing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aira_common.models import ThinkingMode
from aira_gateway.audit import ModelCall
from aira_gateway.catalog import ModelDeclaration
from aira_gateway.core.canonical import CanonicalRequest, Role, TextPart, Thinking
from aira_gateway.pipeline.classifiers import (
    Classification,
    HeuristicInjectionClassifier,
    InjectionClassifier,
    LlmCategoryRouter,
    LlmInjectionClassifier,
    LlmRedactor,
    Verdict,
)
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.thinking import for_a_classifier
from aira_gateway.upstreams.base import ProviderRegistry

#: What a filter does when its classifier could not reach a verdict (`FRD-125`).
#:
#: The default is to **block**, and that is a deliberate reversal. The classifier used to fail
#: open, on the reasoning that an outage must not take down legitimate traffic — which sounds
#: right until you notice what it means: a use case that configured a filter to *block* injections
#: served them instead, with a 200, while the builder went on showing the step as active. That is
#: not a degraded control, it is an absent one wearing the badge of a present one.
#:
#: `FRD-405` settled the same argument for rate limits — "not fail-open; that is the worst moment
#: to stop bounding a caller" — and there is no reason a security filter should answer it
#: differently. An operator who genuinely prefers availability can still choose it, but has to say
#: so, and the choice is on the audit row.
UNDETERMINED_BLOCKS = "block"
UNDETERMINED_ALLOWS = "allow"


def _blocks(verdict: Verdict, config: dict[str, Any]) -> bool:
    """Whether this verdict stops the request, given how the step is configured."""
    if config.get("action", "block") != "block":
        return False
    if verdict is Verdict.INJECTION:
        return True
    if verdict is Verdict.UNDETERMINED:
        return str(config.get("on_undetermined", UNDETERMINED_BLOCKS)) != UNDETERMINED_ALLOWS
    return False


def _rejection(verdict: Verdict) -> str:
    """Say which of the two happened. "Blocked" and "could not be checked" call for different
    actions from whoever reads it — one is a caller to talk to, the other is a classifier to fix."""
    if verdict is Verdict.UNDETERMINED:
        return (
            "The prompt-injection filter could not reach a verdict, and this use case is "
            "configured to refuse rather than serve an unchecked request."
        )
    return "Request rejected by the prompt-injection filter."


#: How much of a model's reply the dry run shows. A reasoning model asked for one word can return
#: several hundred tokens of deliberation, and a trace entry is a box on a screen next to five
#: others. Cut rather than hidden behind a toggle: the interesting part of a classifier's answer is
#: at the front, and the ellipsis says the rest exists.
_SHOWN = 600


def _shown(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _SHOWN else text[:_SHOWN] + "…"


def _said(reply: str, model: str | None) -> dict[str, Any]:
    """What a step's model replied, for the dry run's trace — **screen only, never persisted**.

    One function for both LLM steps, because the two consumers of a trace entry are a template and
    a reader, and a key that means `output` in one step and something else in another is how a
    screen comes to explain the wrong thing. `FRD-122` §5.3 keeps this off the audit row.
    """
    return {
        **({"output": _shown(reply)} if reply.strip() else {}),
        **({"classifier": model} if model else {}),
    }


@dataclass(frozen=True, slots=True)
class _Routed:
    """What `_route` worked out: the category, the model to send to, the cost, and the reply."""

    category: str | None
    target: str | None
    call: ModelCall | None = None
    reply: str = ""
    #: Which model was asked to classify — not the one the request is routed *to*. A screen showing
    #: a router's answer has to name who gave it, or the reader reads the routed model's name and
    #: concludes the wrong model made the decision.
    classifier: str | None = None


@dataclass
class StepEvaluation:
    """What one step did, in the vocabulary **both** consumers need.

    `run` and `dry_run` used to carry an `if/elif` chain each, over the same step types, differing
    only in what they did with the result — one appended an audit decision and raised, the other
    appended a trace entry and stopped. So every step was written twice and the two had to be kept
    saying the same thing by hand. That is the shape this project has paid for repeatedly, most
    expensively across the two API surfaces, and a third step would have made it permanent.

    A step is now **one function** returning this, and the two loops interpret it. What differs
    between them is exactly what should: `run` records for the audit and refuses; `dry_run` records
    for a screen and reports.
    """

    type: str
    #: passed · flagged · blocked · rerouted · unchanged · not_asked · redacted
    action: str
    #: For the dry run's trace, which is a screen and may say more.
    detail: dict[str, Any] = field(default_factory=dict)
    #: For the audit trail, or `None` where there is nothing to record. Deliberately separate from
    #: `detail`: what a reader may see and what is kept durably are different questions
    #: (`FRD-122` §5.3 — the allow-list exists because they are).
    decision: dict[str, Any] | None = None
    call: ModelCall | None = None
    #: The request as this step leaves it. A step that changes nothing returns what it was given.
    request: CanonicalRequest | None = None
    #: Set where the step refuses; `run` raises it, `dry_run` reports it.
    block_reason: str | None = None
    #: A sentence the caller is owed about their own request having been changed. Carried out of
    #: the pipeline rather than applied here: the pipeline runs **before** dispatch and there is no
    #: answer yet to put it on.
    notice: str | None = None
    #: ``(before, after)`` where this step rewrote the caller's text, so the stored request can be
    #: the rewritten one as well as the dispatched one.
    rewrote: tuple[str, str] | None = None


def _filled(template: str, **values: str) -> str:
    """A notice with ``{model}`` / ``{category}`` filled in.

    An explicit replacement rather than `str.format`: the template is written by an operator in a
    text box, and a stray brace — a JSON example, a smiley, an unclosed placeholder — makes
    `format` raise. A notice that crashes the request it is describing is worse than one that
    prints a brace, and an operator has no way to know they wrote a format string.

    An unknown placeholder is left standing rather than blanked, so `{modl}` reads as the typo it
    is instead of quietly disappearing.
    """
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template


def _with_user_text(request: CanonicalRequest, text: str) -> CanonicalRequest:
    """The request with the **last user message's text** replaced.

    Attachments and tool parts on that message are kept: a redactor rewrites prose, and dropping a
    document because its covering sentence was rewritten would be a silent loss of the thing the
    request was about. Their contents are not scanned — a name inside a PDF survives this step, and
    that is `FRD-110`'s stated blind spot rather than a new one.
    """
    messages = list(request.messages)
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role is Role.USER:
            message = messages[index]
            kept = [part for part in message.parts if not isinstance(part, TextPart)]
            messages[index] = message.model_copy(update={"parts": [TextPart(text=text), *kept]})
            break
    return request.model_copy(update={"messages": messages})


@dataclass
class PipelineOutcome:
    request: CanonicalRequest
    fallback_models: tuple[str, ...]
    decisions: list[dict[str, Any]] = field(default_factory=list)
    #: Model calls the steps made. Collected here rather than reported by each step, so a step
    #: that then *blocks* still hands back what it spent deciding to (`FRD-125`).
    model_calls: list[ModelCall] = field(default_factory=list)
    #: Sentences the caller is owed about their own request having been changed under them
    #: (`FRD-309`). Applied to the answer, which does not exist yet when the pipeline runs.
    notices: list[str] = field(default_factory=list)
    #: ``(before, after)`` for every rewrite a step made, so the **stored** request can be the
    #: rewritten one too.
    #:
    #: Measured on the running stack before this existed: the model was sent the redacted prompt
    #: and the audit row kept the original, because the payload written to `request_logs` is the
    #: *wire body* captured at the surface and the pipeline rewrites the *canonical* request. The
    #: redaction protected the model and not the database — which is the one thing the design said
    #: it must do. Found by reading a row, not by reading the code.
    rewrites: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class TraceEntry:
    type: str
    action: str  # passed | flagged | blocked | allowed | rejected | rerouted | unchanged
    detail: dict[str, Any]
    #: This step ran only because the caller asked the dry run to keep going past a block. In
    #: production the request would already have been refused, so what it says here is a
    #: **simulation** and the screen has to label it as one — an unlabelled outcome for a step
    #: production never reaches is exactly the confident statement about the wrong thing this panel
    #: exists to prevent.
    after_block: bool = False


@dataclass
class DryRunResult:
    trace: list[TraceEntry]
    blocked: bool
    block_reason: str | None
    effective_model: str
    fallback_models: tuple[str, ...]
    #: What the dry run **spent** (`FRD-125b`, extended to the dry run on 2026-08-11).
    #:
    #: A dry run runs the real steps, so an LLM-backed one calls a real model and costs real money
    #: — and it recorded none of it. `ADR-0013`'s promise is that a model call is auditable; the
    #: word "dry" describes the *dispatch* that does not happen, never the classifier that does.
    model_calls: list[ModelCall] = field(default_factory=list)


#: How a step learns what the **catalog** says about the model it wants to call.
#:
#: A plain `provider_for(model)` was enough while every servable model was named in configuration.
#: Since `FRD-507` stage B a model can be reachable **because it is catalogued**, and the catalog
#: is what says which provider serves it — so a step that looked the model up by name alone found
#: nothing and did nothing at all: an LLM injection filter fell back to the heuristic and a router
#: routed nowhere, both while the builder showed them active.
#:
#: That is `FRD-125`'s defect arriving through a different door, and the shape this project keeps
#: naming: **a control that stops working without saying so.** The resolver is handed in per
#: request, exactly as `dispatch_with_fallback` takes `provider_of`, because only the request has
#: the catalog.
#:
#: It answers with the **declaration** rather than with a provider name, because the step needs two
#: facts from the same place and asking twice is how they come to disagree: *who serves this model*
#: and *may its thinking be switched off*. The second is what made the first useless — with the
#: classifier finally reachable, it sent `thinkingBudget: 0` to a model that refuses it and got a
#: 400 it then swallowed.
DeclarationOf = Callable[[str], Awaitable[ModelDeclaration]]


class PipelineEngine:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    # -- public ---------------------------------------------------------------------------

    async def run(
        self,
        pipeline: Pipeline,
        request: CanonicalRequest,
        *,
        decisions: list[dict[str, Any]] | None = None,
        model_calls: list[ModelCall] | None = None,
        declaration_of: DeclarationOf | None = None,
    ) -> PipelineOutcome:
        """Run the configured steps.

        ``decisions`` lets a caller supply the list the steps append to, so that the decisions
        taken **before** a blocking step survive the exception. Without it a blocked request could
        record only *that* it was blocked, never the routing that led it to the step that blocked
        it (FRD-122 FR-4).

        ``model_calls`` is the same idea for money rather than for reasons: a step that blocks
        still spent whatever it took to decide that, and a caller-supplied list means the spend
        survives the exception exactly as the decisions do (`FRD-125`).

        ``provider_of`` is how a step reaches a model the **catalog** knows and configuration does
        not (`FRD-507` stage B). Without it such a step finds no provider and quietly does less.
        """
        outcome = PipelineOutcome(
            request=request,
            fallback_models=pipeline.fallback_models,
            decisions=decisions if decisions is not None else [],
            model_calls=model_calls if model_calls is not None else [],
        )
        for step in pipeline.steps:
            evaluation = await self._evaluate(step, outcome.request, declaration_of)
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

        ``model_calls`` is the same caller-supplied list `run` takes, and for the same reason: a
        step that blocks still spent whatever it took to decide that, and the spend has to survive
        the exception rather than being read off a result that was never returned.

        ``past_blocks`` keeps evaluating after a step refuses, which **production never does** —
        asked for by an operator checking whether the rest of a pipeline works at all: a filter
        that blocks the sample leaves every step behind it untested, and the only way to see them
        was to delete the filter and put it back. Off by default, because the default answer this
        screen gives has to be the one production would give, and every step it runs past a block
        spends real tokens on a call the served path would never make. The entries are marked so
        the screen can say which half is a simulation.
        """
        trace: list[TraceEntry] = []
        calls: list[ModelCall] = model_calls if model_calls is not None else []
        current = request
        blocked = False
        reason: str | None = None
        for step in pipeline.steps:
            evaluation = await self._evaluate(step, current, declaration_of)
            if evaluation.call is not None:
                calls.append(evaluation.call)
            if evaluation.request is not None:
                current = evaluation.request
            # `blocked` is read **before** this step's own outcome is folded in, so the step that
            # blocks is not marked as coming after itself.
            trace.append(TraceEntry(evaluation.type, evaluation.action, evaluation.detail, blocked))
            if evaluation.block_reason is not None and not blocked:
                # The **first** refusal is the one that describes production. A later step
                # refusing as well is a second simulated outcome, not a correction of this one.
                blocked, reason = True, evaluation.block_reason
                if not past_blocks:
                    break
        return DryRunResult(trace, blocked, reason, current.model, pipeline.fallback_models, calls)

    # -- one step, one answer -------------------------------------------------------------

    async def _evaluate(
        self,
        step: PipelineStep,
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
    ) -> StepEvaluation:
        """Run one step and say what it did, without deciding what to do about it.

        The single dispatch over step types. Adding one is a branch here and a function below,
        rather than two branches that have to keep agreeing.
        """
        if step.type is StepType.INJECTION_FILTER:
            return await self._evaluate_injection_filter(step.config, request, declaration_of)
        if step.type is StepType.MODEL_ROUTE:
            return await self._evaluate_model_route(step.config, request, declaration_of)
        if step.type is StepType.PII_FILTER:
            return await self._evaluate_pii_filter(step.config, request, declaration_of)
        # Unreachable while `StepType` is exhaustive; a step this build does not know is dropped
        # when the config is parsed, not here (`parse_pipeline`).
        return StepEvaluation(type=str(step.type), action="unchanged")

    async def _evaluate_injection_filter(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
    ) -> StepEvaluation:
        classification = await self._classify(config, request, declaration_of)
        verdict = classification.verdict
        # Screen only, and only where a model was asked. A box captioned "the model replied" over
        # nothing reads as a rendering fault rather than as the fact that no model was involved,
        # so an empty reply stays out of the dict entirely.
        said = _said(
            classification.reply, classification.call.model if classification.call else None
        )
        action = config.get("action", "block")
        blocking = _blocks(verdict, config)
        return StepEvaluation(
            type="injection_filter",
            action="blocked" if blocking else ("passed" if verdict is Verdict.CLEAN else "flagged"),
            detail={
                "mode": config.get("mode", "heuristic"),
                "action": action,
                "verdict": str(verdict),
                **said,
            },
            # Recorded on **every** outcome, not only a flagged one. "The filter ran and passed"
            # and "no filter was configured" are different facts, and after the fact an empty
            # decision list could not tell them apart (`FRD-122` FR-4, `FRD-125`).
            decision={
                "step": "injection_filter",
                "flagged": verdict is not Verdict.CLEAN,
                "action": "blocked" if blocking else action,
                "why": str(verdict),
            },
            call=classification.call,
            block_reason=_rejection(verdict) if blocking else None,
        )

    async def _evaluate_model_route(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
    ) -> StepEvaluation:
        routed = await self._route(config, request, declaration_of)
        category, target, call = routed.category, routed.target, routed.call
        # What the router's own model said, for the dry run's screen. Never persisted.
        said = _said(routed.reply, routed.classifier)
        if call is None and config.get("categories"):
            # **A router that could not be asked says so.** It used to return quietly, and after
            # `FRD-125` closed the same hole for the injection filter this was the one left: a
            # configured router whose classifier is unreachable, or whose provider refuses the
            # request, routes nowhere and leaves nothing behind — indistinguishable on the audit
            # row from a router that ran and matched no category. And on the dry run's screen
            # "unchanged" is the same word a working router uses when nothing matched.
            #
            # **The default still applies**, and that is the half the dry run used to get wrong.
            # `run` fell through to the re-target below; `dry_run` did `continue` and reported
            # "unchanged" — so the builder's preview named one model and production sent the
            # request to another. Found by the refactor that made both paths read one answer,
            # which is the whole reason for it: a divergence between two hand-written copies is
            # invisible until something compares them.
            fallback = config.get("default_model")
            retarget = bool(fallback) and fallback != request.model
            return StepEvaluation(
                type="model_route",
                action="not_asked",
                detail={
                    "why": "the classifier could not be reached or refused the request",
                    **({"to": fallback} if retarget else {"model": request.model}),
                },
                decision={"step": "model_route", "action": "not_asked", "why": "classifier_failed"},
                request=request.model_copy(update={"model": fallback}) if retarget else None,
            )
        # What the caller is told about the classification, in the operator's own words and only
        # where a category was actually matched (`FRD-309`). Nothing matched means there is nothing
        # to say — a sentence naming a category the router did not choose would be a confident
        # statement about a decision that was never taken.
        template = str(config.get("notice") or "").strip()
        notice = (
            _filled(template, category=category or "", model=target or request.model)
            if template and category
            else None
        )

        if target and target != request.model:
            return StepEvaluation(
                type="model_route",
                action="rerouted",
                detail={"category": category, "from": request.model, "to": target, **said},
                decision={
                    "step": "model_route",
                    "category": category,
                    "from": request.model,
                    "to": target,
                },
                call=call,
                request=request.model_copy(update={"model": target}),
                notice=notice,
            )
        # **A router that ran leaves a decision, even when it changed nothing.** It did not, and
        # the row was then identical to one where no router was configured at all — the same hole
        # `FRD-125` closed for the filter ("ran and passed" vs "no filter") and `J17` closed for
        # "could not be asked". Found by measuring: a live request whose classifier matched no
        # category produced an empty decision list and nothing to say why.
        #
        # The two are kept apart, because they send a reader to different places: a category that
        # matched and simply maps to the model already in use is a working router, and matching
        # nothing is a classifier or a category list to look at.
        return StepEvaluation(
            type="model_route",
            action="unchanged",
            detail={"category": category, "model": request.model, **said},
            decision={
                "step": "model_route",
                "action": "unchanged",
                "category": category or "",
                "why": "matched" if category else "no_category_matched",
            },
            call=call,
            notice=notice,
        )

    async def _evaluate_pii_filter(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None,
    ) -> StepEvaluation:
        """Replace personal data in the prompt with a model the use case trusts.

        **What a failure means here is the whole design.** The other two steps fail towards doing
        less — a filter that cannot reach its classifier still has the heuristic, a router that
        cannot be asked still has a default model. This one has no lesser version of itself: either
        the personal data was removed or it was not, and passing the original through would send
        exactly what the step exists to withhold, with a 200 and nothing to show for it. So it
        **blocks by default** (`FRD-125`: the moment a control stops working is the worst moment to
        stop applying it), and an operator who prefers availability says so and is recorded saying
        it.

        Only the **user's own text** is rewritten. A system instruction is written by the use case
        rather than typed by a caller, so it is not where personal data arrives — and rewriting it
        would let a redactor quietly edit the instructions the use case is built on.
        """
        model = config.get("model") or self._default_model()
        provider = await self._provider_for(model, declaration_of)
        if provider is None or not model:
            return self._pii_failure(config, "no model is available to redact with", None)

        original = request.last_user_text()
        redactor = LlmRedactor(
            provider,
            model,
            config.get("instruction"),
            await self._thinking_for(model, declaration_of),
        )
        result = await redactor.rewrite(original)
        if result.text is None:
            return self._pii_failure(config, result.failure or "the redactor failed", result.call)

        changed = result.text.strip() != original.strip()
        return StepEvaluation(
            type="pii_filter",
            action="redacted" if changed else "unchanged",
            # `before`/`after` are the dry run's whole point for this step: "redacted" is a badge,
            # and what somebody testing a redaction instruction needs to see is *what it did to
            # their sentence*. Both halves are the sample text they typed into the box on the same
            # screen — screen only, like every other `output` here.
            detail={
                "classifier": model,
                "changed": changed,
                "before": _shown(original),
                "after": _shown(result.text),
            },
            # **`changed`, not a count.** How many things were replaced is not knowable here: the
            # placeholder shape is whatever the operator's own instruction asks for, so counting
            # would mean dictating it. Recording a number nobody measured is the failure this
            # project keeps naming.
            decision={
                "step": "pii_filter",
                "action": "redacted" if changed else "unchanged",
                "why": model,
            },
            call=result.call,
            request=_with_user_text(request, result.text) if changed else None,
            notice=str(config.get("notice") or "").strip() if changed else None,
            rewrote=(original, result.text) if changed else None,
        )

    @staticmethod
    def _pii_failure(config: dict[str, Any], why: str, call: ModelCall | None) -> StepEvaluation:
        """A redactor that did not produce a usable rewrite."""
        allows = str(config.get("on_failure", UNDETERMINED_BLOCKS)) == UNDETERMINED_ALLOWS
        return StepEvaluation(
            type="pii_filter",
            action="allowed" if allows else "blocked",
            detail={"why": why, "on_failure": "allow" if allows else "block"},
            decision={
                "step": "pii_filter",
                "action": "allowed" if allows else "blocked",
                "why": why,
            },
            call=call,
            block_reason=None if allows else f"Personal data could not be removed: {why}.",
        )

    # -- step primitives (shared by run + dry_run) ----------------------------------------

    async def _classify(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None = None,
    ) -> Classification:
        text = self._scanned_text(request, config.get("scope", "user"))
        classifier = await self._injection_classifier(config, declaration_of)
        return await classifier.classify_text(text)

    async def _injection_classifier(
        self, config: dict[str, Any], declaration_of: DeclarationOf | None = None
    ) -> InjectionClassifier:
        """The classifier this step is configured with.

        **An `llm` step that cannot find its model falls back to the heuristic**, which is the one
        line in this file worth arguing about. It is not a silent downgrade of the *verdict*: the
        heuristic still refuses what it recognises and `FRD-125` made an undetermined verdict block
        by default. It is a downgrade of the *method*, and it exists so a misconfigured model name
        cannot take a use case's whole traffic down.

        What made it dangerous was that the lookup ignored the catalog, so on a deployment where
        models are reached **by being catalogued** (`FRD-507` stage B) it was not a fallback for a
        typo — it was the ordinary path, every time, for every use case.
        """
        if config.get("mode") == "llm":
            model = config.get("model") or self._default_model()
            provider = await self._provider_for(model, declaration_of)
            if provider is not None and model is not None:
                return LlmInjectionClassifier(
                    provider,
                    model,
                    config.get("instruction"),
                    await self._thinking_for(model, declaration_of),
                )
        extras = tuple(config.get("patterns", []))
        return HeuristicInjectionClassifier(extras, use_builtins=config.get("use_builtins", True))

    async def _provider_for(self, model: str | None, declaration_of: DeclarationOf | None) -> Any:
        """The adapter serving ``model``, asking the catalog when configuration does not know it.

        `provider_for(name)` alone was the whole lookup until stage B made cataloguing enough to
        serve a model. Without the second argument a catalogued model resolves to nothing — and
        every caller of this decided that "nothing" meant "quietly do less".
        """
        if not model:
            return None
        direct = self._registry.provider_for(model)
        if direct is not None or declaration_of is None:
            return direct
        declaration = await declaration_of(model)
        # Publisher too: one platform can host two dialects, and on Vertex the provider alone
        # identifies neither. A resolution that passed only the provider found nothing there, and
        # every caller of this reads "nothing" as "quietly do less".
        return self._registry.provider_for(model, declaration.provider, declaration.publisher)

    async def _thinking_for(self, model: str | None, declaration_of: DeclarationOf | None) -> Any:
        """What to tell ``model`` about thinking, from the catalog rather than unconditionally.

        With no resolver — a caller that has no catalog to hand — the classifier keeps its old
        behaviour of asking for off, because `FRD-125`'s finding stands for every model that can
        honour it: sent nothing, a reasoning model thinks and spends a four-token allowance on it.
        """
        if declaration_of is None or not model:
            return Thinking(mode=ThinkingMode.DISABLED)
        return for_a_classifier(await declaration_of(model))

    async def _route(
        self,
        config: dict[str, Any],
        request: CanonicalRequest,
        declaration_of: DeclarationOf | None = None,
    ) -> _Routed:
        """The category, the model to use, what asking cost, and what the model said.

        The third exists because a router that reached no category still made the call, and a use
        case running one over traffic it then routes nowhere is paying for exactly those
        (`FRD-125`). The fourth is for the dry run's screen only.
        """
        categories: list[dict[str, str]] = config.get("categories", [])
        default_model = config.get("default_model")
        if not categories:
            return _Routed(None, default_model)
        model = config.get("model") or self._default_model()
        provider = await self._provider_for(model, declaration_of)
        if provider is None or model is None:
            return _Routed(None, default_model)
        router = LlmCategoryRouter(
            provider, model, categories, await self._thinking_for(model, declaration_of)
        )
        routing = await router.classify_text(self._route_text(request))
        if routing.category:
            for category in categories:
                if category.get("name") == routing.category:
                    return _Routed(
                        routing.category,
                        category.get("model") or default_model,
                        routing.call,
                        routing.reply,
                        model,
                    )
        return _Routed(None, default_model, routing.call, routing.reply, model)

    # -- helpers --------------------------------------------------------------------------

    def _scanned_text(self, request: CanonicalRequest, scope: str) -> str:
        """What the injection filter reads, and — in the default scope — what it does not.

        `user` is **the last user message**, not the conversation. That is the right default for
        the single-turn traffic this filter was written for, and it is a stated blind spot rather
        than an oversight for the multi-turn kind: an injection planted in an earlier turn is not
        scanned again when the caller sends the next one. `system_user` widens it to every system
        and user message, which is what a conversational use case should configure.

        Written down for the same reason `FRD-110` §8 writes down that attachments are not scanned:
        a control's *reach* is part of what it is, and a reader who assumes "the filter runs" means
        "the prompt is checked" has been misled by omission. `docs/REQUEST-LIFECYCLE.md` says it
        where an operator will meet it.
        """
        if scope == "system_user":
            return "\n".join(m.text for m in request.messages if m.role in (Role.SYSTEM, Role.USER))
        return request.last_user_text()

    def _route_text(self, request: CanonicalRequest) -> str:
        return "\n".join(m.text for m in request.messages if m.role in (Role.SYSTEM, Role.USER))

    def _default_model(self) -> str | None:
        models = self._registry.models()
        return models[0].name if models else None
