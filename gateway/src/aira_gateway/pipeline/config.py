"""Pipeline configuration schema (FRD-300).

A ``Pipeline`` is an ordered list of steps plus a dispatch fallback chain. It is authored in
Management and distributed to the gateway read-model as JSON; ``Pipeline.from_dict`` parses that
JSON leniently (unknown step types are ignored so old gateways tolerate new config).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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
        for raw in data.get("steps", []):
            try:
                step_type = StepType(raw["type"])
            except KeyError, ValueError:
                continue  # forward-compatible: skip unknown/malformed steps
            steps.append(PipelineStep(type=step_type, config=raw.get("config", {})))
        fallbacks = tuple(str(m) for m in data.get("fallback_models", []) if m)
        return cls(steps=tuple(steps), fallback_models=fallbacks)
