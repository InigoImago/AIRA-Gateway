"""Pipeline configuration schema (FRD-300).

A ``Pipeline`` is an ordered list of steps plus a dispatch fallback chain. It is authored in
Management and distributed to the gateway read-model as JSON; ``Pipeline.from_dict`` parses that
JSON leniently (unknown step types are ignored so old gateways tolerate new config).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from aira_common.logging import get_logger

_log = get_logger("aira_gateway.pipeline")

# Defence in depth: Management validates configs at authoring time, but the gateway also
# refuses to build an unbounded pipeline out of whatever the read-model happens to contain.
MAX_STEPS = 32
MAX_FALLBACK_MODELS = 16

#: The **same bounds the Management serializer applies**, asked again here.
#:
#: `aira_common.patterns` states the rule this closes: *"Both planes ask, because only one of them
#: used to. The protection sat at one end of a link and the other end trusted it, which is the
#: shape of three of the four findings in `ADR-0018`."* That was acted on for the regex bound and
#: for the step count, and not for the rest — so `instruction`, `notice` and the category list
#: arrived here unbounded. Three ways in, none of them exotic: a row written straight into the
#: read-model, a publish onto an unauthenticated broker (`KafkaSecurity`), and
#: `POST /v1beta/pipeline:dryRun`, whose `pipeline` field is an unvalidated object by design.
#:
#: What each one costs, unbounded: an `instruction` is a system prompt sent on **every** request of
#: the use case, so its length is a bill; a `notice` is put in front of somebody else's answer, so
#: its length is their screen; and the category list is pasted whole into the router's prompt.
#:
#: **Truncated with a log line, not dropped.** The same treatment the pattern bounds beside it
#: already give: a step running on a shortened instruction is degraded, a use case whose filter
#: vanished is unprotected, and only one of those announces itself.
MAX_TEXT_LENGTH = 4_000
MAX_CATEGORIES = 32
#: Every step key holding operator-authored prose. Named rather than inferred: a step that grows a
#: fourth text field should have to add it here, which is a line of code instead of a hole.
TEXT_KEYS = ("instruction", "notice")
CATEGORY_TEXT_KEYS = ("name", "description", "model")


class StepType(StrEnum):
    """The steps a pipeline may run before dispatch.

    `allow_check` was a member until 2026-08-11 and is now `FRD-308`'s per-use-case model release.
    It is not a rename: the step ran **once, before routing**, against the model the caller named,
    and measurement showed both ways around it — a `model_route` step re-targeted a request to a
    forbidden model and it was served 200, and a fallback chain dispatched to one and it was served
    200. A release is a property of the use case rather than a stage of its pipeline, and it is
    enforced at every hop like every other dispatch condition (`ADR-0012` §3).

    An unknown step name in a stored config is dropped rather than refused (see `parse_pipeline`),
    so a read-model row still carrying the old step degrades to a pipeline without it — which is
    correct, because the release now enforces what it used to.
    """

    INJECTION_FILTER = "injection_filter"
    MODEL_ROUTE = "model_route"
    #: Replace personal data in the prompt before it reaches the model, with a trusted model of
    #: the use case's own choosing (2026-08-14).
    #:
    #: **The first step that changes what the caller sent.** The other two block or re-target; this
    #: one rewrites, and the request that goes upstream — and the one the audit trail keeps — is
    #: the rewritten one. That is the point: the original exists nowhere afterwards, which is what
    #: makes it a data-protection control rather than a note about one. The consequence is stated
    #: where it is decided (`FRD-122` holds that the log records what was *asked*, and this is the
    #: one place that is relaxed, in favour of exactly the data the step exists to remove).
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
    """The step's configuration with the operator-authored text held to the bounds Management
    applies when it is written. See `MAX_TEXT_LENGTH` for why the gateway asks a second time."""
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
            str(m) for m in list(data.get("fallback_models", []))[:MAX_FALLBACK_MODELS] if m
        )
        return cls(steps=tuple(steps), fallback_models=fallbacks)
