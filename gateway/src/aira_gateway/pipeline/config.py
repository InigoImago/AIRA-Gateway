"""Pipeline configuration schema (FRD-300).

A ``Pipeline`` is an ordered list of steps plus a dispatch fallback chain. It is authored in
Management and distributed to the gateway read-model as JSON; ``Pipeline.from_dict`` parses that
JSON leniently (unknown step types are ignored so old gateways tolerate new config).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# Defence in depth: Management validates configs at authoring time, but the gateway also
# refuses to build an unbounded pipeline out of whatever the read-model happens to contain.
MAX_STEPS = 32
MAX_FALLBACK_MODELS = 16


class StepType(StrEnum):
    INJECTION_FILTER = "injection_filter"
    ALLOW_CHECK = "allow_check"
    MODEL_ROUTE = "model_route"


@dataclass(frozen=True, slots=True)
class PipelineStep:
    type: StepType
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Pipeline:
    steps: tuple[PipelineStep, ...] = ()
    fallback_models: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.steps and not self.fallback_models

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Pipeline:
        steps: list[PipelineStep] = []
        for raw in list(data.get("steps", []))[:MAX_STEPS]:
            try:
                step_type = StepType(raw["type"])
            except KeyError, ValueError, TypeError:
                continue  # forward-compatible: skip unknown/malformed steps
            config = raw.get("config", {})
            steps.append(
                PipelineStep(type=step_type, config=config if isinstance(config, dict) else {})
            )
        fallbacks = tuple(
            str(m) for m in list(data.get("fallback_models", []))[:MAX_FALLBACK_MODELS] if m
        )
        return cls(steps=tuple(steps), fallback_models=fallbacks)
