"""Pipeline configuration schema (FRD-300).

A ``Pipeline`` is an ordered list of steps plus a dispatch fallback chain. It is authored in
Management and distributed to the gateway read-model as JSON; ``Pipeline.from_dict`` parses that
JSON leniently (unknown step types are ignored so old gateways tolerate new config).

**The gateway re-applies Management's bounds.** The read-model is also reachable by a direct write,
a publish onto an unauthenticated broker, and `pipeline:dryRun`'s unvalidated `pipeline` field, so
the protection cannot sit at one end of the link only (`ADR-0018`). Oversized values are truncated
with a log line rather than dropped: a step on a shortened instruction is degraded, a use case whose
filter vanished is unprotected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aira_common.logging import get_logger

_log = get_logger("aira_gateway.pipeline")

MAX_STEPS = 32
MAX_FALLBACK_MODELS = 16
#: Management's ceiling on a model name. A fallback name reaches an audit row and the caller's
#: `NoCapableModel` message, so it is bounded in length as well as in count.
MAX_MODEL_LENGTH = 128
#: Applied to **every** string in a step's config: an `instruction` is a system prompt sent on every
#: request (a bill), a `notice` is shown in front of somebody's answer (their screen).
MAX_TEXT_LENGTH = 4_000
#: The category list is pasted whole into the router's prompt.
MAX_CATEGORIES = 32


class StepType(StrEnum):
    """The steps a pipeline may run before dispatch.

    The former `allow_check` step is now `FRD-308`'s per-use-case model release, enforced at every
    dispatch hop (`ADR-0012` §3). A stored config still naming it degrades to a pipeline without it
    (see `Pipeline.from_dict`), which is correct because the release enforces what it used to.
    """

    INJECTION_FILTER = "injection_filter"
    MODEL_ROUTE = "model_route"
    #: Replaces personal data in the prompt with a trusted model of the use case's choosing
    #: (`FRD-309`). The only step that changes what the caller sent: the request dispatched **and**
    #: the one the audit trail keeps is the rewritten one — the one place `FRD-122`'s "log what was
    #: asked" is relaxed, in favour of the data the step exists to remove.
    PII_FILTER = "pii_filter"


def _clipped(value: Any, *, where: str, step: str) -> Any:
    """A text field at or under `MAX_TEXT_LENGTH`, saying so when it had to be cut."""
    if not isinstance(value, str) or len(value) <= MAX_TEXT_LENGTH:
        return value
    _log.warning(
        "pipeline_text_truncated", step=step, field=where, length=len(value), limit=MAX_TEXT_LENGTH
    )
    return value[:MAX_TEXT_LENGTH]


def _bounded(config: dict[str, Any], step: str) -> dict[str, Any]:
    """The step's configuration with every operator-authored text held to Management's bounds."""
    bounded = {key: _clipped(value, where=key, step=step) for key, value in config.items()}

    categories = bounded.get("categories")
    if isinstance(categories, list):
        if len(categories) > MAX_CATEGORIES:
            _log.warning(
                "pipeline_categories_truncated",
                step=step,
                count=len(categories),
                limit=MAX_CATEGORIES,
            )
        bounded["categories"] = [
            {
                key: _clipped(value, where=f"categories.{key}", step=step)
                for key, value in category.items()
            }
            if isinstance(category, dict)
            else category
            for category in categories[:MAX_CATEGORIES]
        ]
    return bounded


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
        """Whether running this changes nothing."""
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
                PipelineStep(
                    type=step_type,
                    config=_bounded(config, str(step_type)) if isinstance(config, dict) else {},
                )
            )
        fallbacks = tuple(
            str(m)[:MAX_MODEL_LENGTH]
            for m in list(data.get("fallback_models", []))[:MAX_FALLBACK_MODELS]
            if m
        )
        return cls(steps=tuple(steps), fallback_models=fallbacks)
