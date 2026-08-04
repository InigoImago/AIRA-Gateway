"""Pre-dispatch pipeline engine (FRD-300).

Walks a use case's ordered steps over the canonical request: filter (may reject), allow-check
(may reject), and model routing (may override the model). Returns the effective request + the
dispatch fallback chain + the decisions taken (for logging/tracing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aira_gateway.core.canonical import CanonicalRequest
from aira_gateway.pipeline.classifiers import (
    HeuristicInjectionClassifier,
    InjectionClassifier,
    LlmInjectionClassifier,
)
from aira_gateway.pipeline.config import Pipeline, PipelineStep, StepType
from aira_gateway.pipeline.errors import PipelineRejected
from aira_gateway.upstreams.base import ProviderRegistry


@dataclass
class PipelineOutcome:
    request: CanonicalRequest
    fallback_models: tuple[str, ...]
    decisions: list[dict[str, Any]] = field(default_factory=list)


class PipelineEngine:
    def __init__(self, registry: ProviderRegistry) -> None:
        self._registry = registry

    async def run(self, pipeline: Pipeline, request: CanonicalRequest) -> PipelineOutcome:
        outcome = PipelineOutcome(request=request, fallback_models=pipeline.fallback_models)
        for step in pipeline.steps:
            await self._apply(step, outcome)
        return outcome

    async def _apply(self, step: PipelineStep, outcome: PipelineOutcome) -> None:
        if step.type is StepType.INJECTION_FILTER:
            await self._injection_filter(step, outcome)
        elif step.type is StepType.ALLOW_CHECK:
            self._allow_check(step, outcome)
        elif step.type is StepType.MODEL_ROUTE:
            self._model_route(step, outcome)

    async def _injection_filter(self, step: PipelineStep, outcome: PipelineOutcome) -> None:
        classifier = self._classifier(step.config)
        flagged = await classifier.is_injection(outcome.request.last_user_text())
        if not flagged:
            return
        action = step.config.get("action", "block")
        outcome.decisions.append({"step": "injection_filter", "flagged": True, "action": action})
        if action == "block":
            raise PipelineRejected("Request rejected by the prompt-injection filter.")

    def _classifier(self, config: dict[str, Any]) -> InjectionClassifier:
        if config.get("mode") == "llm":
            model = config.get("model") or self._default_model()
            provider = self._registry.provider_for(model) if model else None
            if provider is not None and model is not None:
                return LlmInjectionClassifier(provider, model)
        return HeuristicInjectionClassifier()

    def _default_model(self) -> str | None:
        models = self._registry.models()
        return models[0].name if models else None

    def _allow_check(self, step: PipelineStep, outcome: PipelineOutcome) -> None:
        allowed = step.config.get("models", [])
        if allowed and outcome.request.model not in allowed:
            raise PipelineRejected(
                f"Model '{outcome.request.model}' is not allowed for this use case.",
                code=403,
                status="PERMISSION_DENIED",
            )

    def _model_route(self, step: PipelineStep, outcome: PipelineOutcome) -> None:
        length = len(outcome.request.last_user_text())
        for rule in step.config.get("rules", []):
            model = rule.get("model")
            if not model:
                continue
            threshold = rule.get("if_under_chars")
            if threshold is None or length < int(threshold):
                if model != outcome.request.model:
                    outcome.decisions.append(
                        {"step": "model_route", "from": outcome.request.model, "to": model}
                    )
                    outcome.request = outcome.request.model_copy(update={"model": model})
                return
