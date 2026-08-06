"""Pre-dispatch pipeline engine (FRD-300/306).

Walks a use case's ordered steps over the canonical request:
- ``injection_filter`` — heuristic (built-in + custom patterns) or LLM classifier; may block.
- ``allow_check`` — model allow-list; may block.
- ``model_route`` — an LLM classifies the request (system + user) into one of the configured
  categories and routes to that category's model.

``run`` executes the pipeline (raising ``PipelineRejected`` on a block); ``dry_run`` evaluates it
without raising and returns a full per-step trace for the builder's preview/testing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aira_gateway.core.canonical import CanonicalRequest, Role
from aira_gateway.pipeline.classifiers import (
    HeuristicInjectionClassifier,
    InjectionClassifier,
    LlmCategoryRouter,
    LlmInjectionClassifier,
)
from aira_gateway.pipeline.config import Pipeline, StepType
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.upstreams.base import ProviderRegistry


@dataclass
class PipelineOutcome:
    request: CanonicalRequest
    fallback_models: tuple[str, ...]
    decisions: list[dict[str, Any]] = field(default_factory=list)


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
    ) -> PipelineOutcome:
        """Run the configured steps.

        ``decisions`` lets a caller supply the list the steps append to, so that the decisions
        taken **before** a blocking step survive the exception. Without it a blocked request could
        record only *that* it was blocked, never the routing that led it to the step that blocked
        it (FRD-122 FR-4).
        """
        outcome = PipelineOutcome(
            request=request,
            fallback_models=pipeline.fallback_models,
            decisions=decisions if decisions is not None else [],
        )
        for step in pipeline.steps:
            if step.type is StepType.INJECTION_FILTER:
                if await self._is_injection(step.config, outcome.request):
                    action = step.config.get("action", "block")
                    outcome.decisions.append(
                        {"step": "injection_filter", "flagged": True, "action": action}
                    )
                    if action == "block":
                        raise PipelineRejected("Request rejected by the prompt-injection filter.")
            elif step.type is StepType.ALLOW_CHECK:
                if self._allow_violation(step.config, outcome.request):
                    outcome.decisions.append(
                        {"step": "allow_check", "action": "blocked", "to": outcome.request.model}
                    )
                    raise PipelineRejected(
                        f"Model '{outcome.request.model}' is not allowed for this use case.",
                        code=403,
                        status="PERMISSION_DENIED",
                    )
            elif step.type is StepType.MODEL_ROUTE:
                category, target = await self._route(step.config, outcome.request)
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
                flagged = await self._is_injection(step.config, current)
                action = step.config.get("action", "block")
                detail = {"mode": step.config.get("mode", "heuristic"), "action": action}
                if flagged and action == "block":
                    trace.append(TraceEntry("injection_filter", "blocked", detail))
                    blocked, reason = True, "Prompt-injection filter blocked the request."
                    break
                trace.append(
                    TraceEntry("injection_filter", "flagged" if flagged else "passed", detail)
                )
            elif step.type is StepType.ALLOW_CHECK:
                if self._allow_violation(step.config, current):
                    trace.append(TraceEntry("allow_check", "rejected", {"model": current.model}))
                    blocked, reason = True, f"Model '{current.model}' is not allowed."
                    break
                trace.append(TraceEntry("allow_check", "allowed", {"model": current.model}))
            elif step.type is StepType.MODEL_ROUTE:
                category, target = await self._route(step.config, current)
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

    async def _is_injection(self, config: dict[str, Any], request: CanonicalRequest) -> bool:
        text = self._scanned_text(request, config.get("scope", "user"))
        return await self._injection_classifier(config).is_injection(text)

    def _injection_classifier(self, config: dict[str, Any]) -> InjectionClassifier:
        if config.get("mode") == "llm":
            model = config.get("model") or self._default_model()
            provider = self._registry.provider_for(model) if model else None
            if provider is not None and model is not None:
                return LlmInjectionClassifier(provider, model, config.get("instruction"))
        extras = tuple(config.get("patterns", []))
        return HeuristicInjectionClassifier(extras, use_builtins=config.get("use_builtins", True))

    def _allow_violation(self, config: dict[str, Any], request: CanonicalRequest) -> bool:
        allowed = config.get("models", [])
        return bool(allowed) and request.model not in allowed

    async def _route(
        self, config: dict[str, Any], request: CanonicalRequest
    ) -> tuple[str | None, str | None]:
        categories: list[dict[str, str]] = config.get("categories", [])
        default_model = config.get("default_model")
        if not categories:
            return None, default_model
        model = config.get("model") or self._default_model()
        provider = self._registry.provider_for(model) if model else None
        if provider is None or model is None:
            return None, default_model
        name = await LlmCategoryRouter(provider, model, categories).classify(
            self._route_text(request)
        )
        if name:
            for category in categories:
                if category.get("name") == name:
                    return name, category.get("model") or default_model
        return None, default_model

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
