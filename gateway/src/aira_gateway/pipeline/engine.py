"""Pre-dispatch pipeline engine (FRD-300/306).

Walks a use case's ordered steps over the canonical request:
- ``injection_filter`` — heuristic (built-in + custom patterns) or LLM classifier; may block.
- ``model_route`` — an LLM classifies the request (system + user) into one of the configured
  categories and routes to that category's model.

``run`` executes the pipeline (raising ``PipelineRejected`` on a block); ``dry_run`` evaluates it
without raising and returns a full per-step trace for the builder's preview/testing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aira_gateway.audit import ModelCall
from aira_gateway.core.canonical import CanonicalRequest, Role
from aira_gateway.pipeline.classifiers import (
    Classification,
    HeuristicInjectionClassifier,
    InjectionClassifier,
    LlmCategoryRouter,
    LlmInjectionClassifier,
    Verdict,
)
from aira_gateway.pipeline.config import Pipeline, StepType
from aira_gateway.pipeline.errors import PipelineRejected
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


@dataclass
class PipelineOutcome:
    request: CanonicalRequest
    fallback_models: tuple[str, ...]
    decisions: list[dict[str, Any]] = field(default_factory=list)
    #: Model calls the steps made. Collected here rather than reported by each step, so a step
    #: that then *blocks* still hands back what it spent deciding to (`FRD-125`).
    model_calls: list[ModelCall] = field(default_factory=list)


@dataclass
class TraceEntry:
    type: str
    action: str  # passed | flagged | blocked | allowed | rejected | rerouted | unchanged
    detail: dict[str, Any]


@dataclass
class DryRunResult:
    trace: list[TraceEntry]
    blocked: bool
    block_reason: str | None
    effective_model: str
    fallback_models: tuple[str, ...]


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
    ) -> PipelineOutcome:
        """Run the configured steps.

        ``decisions`` lets a caller supply the list the steps append to, so that the decisions
        taken **before** a blocking step survive the exception. Without it a blocked request could
        record only *that* it was blocked, never the routing that led it to the step that blocked
        it (FRD-122 FR-4).

        ``model_calls`` is the same idea for money rather than for reasons: a step that blocks
        still spent whatever it took to decide that, and a caller-supplied list means the spend
        survives the exception exactly as the decisions do (`FRD-125`).
        """
        outcome = PipelineOutcome(
            request=request,
            fallback_models=pipeline.fallback_models,
            decisions=decisions if decisions is not None else [],
            model_calls=model_calls if model_calls is not None else [],
        )
        for step in pipeline.steps:
            if step.type is StepType.INJECTION_FILTER:
                classification = await self._classify(step.config, outcome.request)
                if classification.call is not None:
                    outcome.model_calls.append(classification.call)
                verdict = classification.verdict
                action = step.config.get("action", "block")
                blocking = _blocks(verdict, step.config)
                # Recorded on **every** outcome, not only a flagged one. "The filter ran and passed"
                # and "no filter was configured" are different facts, and after the fact an empty
                # decision list could not tell them apart (`FRD-122` FR-4, `FRD-125`).
                outcome.decisions.append(
                    {
                        "step": "injection_filter",
                        "flagged": verdict is not Verdict.CLEAN,
                        "action": "blocked" if blocking else action,
                        "why": str(verdict),
                    }
                )
                if blocking:
                    raise PipelineRejected(_rejection(verdict))
            elif step.type is StepType.MODEL_ROUTE:
                category, target, call = await self._route(step.config, outcome.request)
                if call is not None:
                    outcome.model_calls.append(call)
                if target and target != outcome.request.model:
                    outcome.decisions.append(
                        {
                            "step": "model_route",
                            "category": category,
                            "from": outcome.request.model,
                            "to": target,
                        }
                    )
                    outcome.request = outcome.request.model_copy(update={"model": target})
        return outcome

    async def dry_run(self, pipeline: Pipeline, request: CanonicalRequest) -> DryRunResult:
        trace: list[TraceEntry] = []
        current = request
        blocked = False
        reason: str | None = None
        for step in pipeline.steps:
            if step.type is StepType.INJECTION_FILTER:
                verdict = await self._injection_verdict(step.config, current)
                action = step.config.get("action", "block")
                detail = {
                    "mode": step.config.get("mode", "heuristic"),
                    "action": action,
                    "verdict": str(verdict),
                }
                if _blocks(verdict, step.config):
                    trace.append(TraceEntry("injection_filter", "blocked", detail))
                    blocked, reason = True, _rejection(verdict)
                    break
                # The dry run shows the operator what the builder would do, including the case
                # they are least likely to have thought about: the classifier not answering.
                trace.append(
                    TraceEntry(
                        "injection_filter",
                        "passed" if verdict is Verdict.CLEAN else "flagged",
                        detail,
                    )
                )
            elif step.type is StepType.MODEL_ROUTE:
                category, target, _ = await self._route(step.config, current)
                if target and target != current.model:
                    trace.append(
                        TraceEntry(
                            "model_route",
                            "rerouted",
                            {"category": category, "from": current.model, "to": target},
                        )
                    )
                    current = current.model_copy(update={"model": target})
                else:
                    trace.append(
                        TraceEntry(
                            "model_route",
                            "unchanged",
                            {"category": category, "model": current.model},
                        )
                    )
        return DryRunResult(trace, blocked, reason, current.model, pipeline.fallback_models)

    # -- step primitives (shared by run + dry_run) ----------------------------------------

    async def _classify(self, config: dict[str, Any], request: CanonicalRequest) -> Classification:
        text = self._scanned_text(request, config.get("scope", "user"))
        return await self._injection_classifier(config).classify_text(text)

    async def _injection_verdict(
        self, config: dict[str, Any], request: CanonicalRequest
    ) -> Verdict:
        return (await self._classify(config, request)).verdict

    def _injection_classifier(self, config: dict[str, Any]) -> InjectionClassifier:
        if config.get("mode") == "llm":
            model = config.get("model") or self._default_model()
            provider = self._registry.provider_for(model) if model else None
            if provider is not None and model is not None:
                return LlmInjectionClassifier(provider, model, config.get("instruction"))
        extras = tuple(config.get("patterns", []))
        return HeuristicInjectionClassifier(extras, use_builtins=config.get("use_builtins", True))

    async def _route(
        self, config: dict[str, Any], request: CanonicalRequest
    ) -> tuple[str | None, str | None, ModelCall | None]:
        """The category, the model to use, and what asking cost — the third is new (`FRD-125`).

        A router that reached no category still made the call, and a use case running one over
        traffic it then routes nowhere is paying for exactly those.
        """
        categories: list[dict[str, str]] = config.get("categories", [])
        default_model = config.get("default_model")
        if not categories:
            return None, default_model, None
        model = config.get("model") or self._default_model()
        provider = self._registry.provider_for(model) if model else None
        if provider is None or model is None:
            return None, default_model, None
        name, call = await LlmCategoryRouter(provider, model, categories).classify_text(
            self._route_text(request)
        )
        if name:
            for category in categories:
                if category.get("name") == name:
                    return name, category.get("model") or default_model, call
        return None, default_model, call

    # -- helpers --------------------------------------------------------------------------

    def _scanned_text(self, request: CanonicalRequest, scope: str) -> str:
        if scope == "system_user":
            return "\n".join(m.text for m in request.messages if m.role in (Role.SYSTEM, Role.USER))
        return request.last_user_text()

    def _route_text(self, request: CanonicalRequest) -> str:
        return "\n".join(m.text for m in request.messages if m.role in (Role.SYSTEM, Role.USER))

    def _default_model(self) -> str | None:
        models = self._registry.models()
        return models[0].name if models else None
