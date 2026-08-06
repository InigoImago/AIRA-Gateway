"""Consistency rules for a model declaration (FRD-114 FR-3, FR-4).

The catalog is a **runtime authority**: what it says decides whether a request is accepted. So a
declaration that cannot work must be refused where it is written, not discovered where it is
enforced — a model whose thinking maximum exceeds its output cap describes something that would
fail every request, and finding that out from a vendor error message is the expensive way.

Kept apart from the serializer because the rules are about the *declaration*, not about the HTTP
shape, and because they are what the tests actually pin.
"""

from __future__ import annotations

from typing import Any

from aira_common.models import Capability, Hosting, ThinkingMode

MAX_MEDIA_TYPES = 32
MAX_DIMENSIONS = 8


def _positive_int(value: Any, field: str, errors: list[str]) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        errors.append(f"{field} must be a positive integer.")
        return None
    return value


def validate_capabilities(values: Any) -> list[str]:
    if not isinstance(values, list):
        return ["capabilities must be a list."]
    known = {member.value for member in Capability}
    unknown = [value for value in values if value not in known]
    if unknown:
        return [f"Unknown capabilities: {', '.join(map(str, unknown))}. Known: {sorted(known)}."]
    return []


def validate_hosting(value: Any) -> list[str]:
    if not value:
        return []
    if value not in {member.value for member in Hosting}:
        return [f"hosting must be one of {sorted(member.value for member in Hosting)}."]
    return []


def validate_thinking(block: Any, *, max_output_tokens: int | None) -> list[str]:
    """The declaration a request is validated against, checked against itself first."""
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["thinking must be an object."]

    errors: list[str] = []
    modes = block.get("modes")
    if not isinstance(modes, list) or not modes:
        errors.append("thinking.modes must be a non-empty list.")
        modes = []
    known = {member.value for member in ThinkingMode}
    unknown = [mode for mode in modes if mode not in known]
    if unknown:
        errors.append(f"Unknown thinking modes: {', '.join(map(str, unknown))}.")

    minimum = _positive_int(block.get("min_tokens"), "thinking.min_tokens", errors)
    maximum = _positive_int(block.get("max_tokens"), "thinking.max_tokens", errors)
    if minimum is not None and maximum is not None and minimum > maximum:
        errors.append("thinking.min_tokens must not exceed thinking.max_tokens.")

    # The rule with teeth: Anthropic draws thinking tokens from `max_tokens`, so a budget at or
    # above the model's output cap describes a model that can never answer (FRD-119 §5.4). The
    # catalog must not be able to hold that combination.
    if maximum is not None and max_output_tokens is not None and maximum >= max_output_tokens:
        errors.append(
            "thinking.max_tokens must be below max_output_tokens — the thinking budget is drawn "
            "from the output allowance, so a request at this budget could never answer."
        )

    if ThinkingMode.LIMITED in modes and maximum is None:
        errors.append("thinking.max_tokens is required when 'limited' is offered.")

    default = block.get("default")
    if default is not None:
        if not isinstance(default, dict) or default.get("mode") not in modes:
            errors.append("thinking.default.mode must be one of the declared modes.")
        elif default.get("mode") == ThinkingMode.LIMITED:
            tokens = _positive_int(default.get("tokens"), "thinking.default.tokens", errors)
            if (
                tokens is not None
                and maximum is not None
                and not (minimum or 1) <= tokens <= maximum
            ):
                errors.append("thinking.default.tokens must lie within the declared bounds.")

    levels = block.get("levels")
    if levels is not None:
        if not isinstance(levels, dict):
            errors.append("thinking.levels must be an object mapping a mode to a token budget.")
        else:
            for mode, budget in levels.items():
                if mode not in known:
                    errors.append(f"thinking.levels has an unknown mode '{mode}'.")
                _positive_int(budget, f"thinking.levels.{mode}", errors)
    return errors


def validate_embedding(block: Any) -> list[str]:
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["embedding must be an object."]

    errors: list[str] = []
    task_types = block.get("task_types")
    if task_types is not None and (
        not isinstance(task_types, list) or not all(isinstance(t, str) and t for t in task_types)
    ):
        errors.append("embedding.task_types must be a list of non-empty strings.")

    dimensions = block.get("dimensions")
    if dimensions is not None:
        if not isinstance(dimensions, list) or not dimensions:
            errors.append("embedding.dimensions must be a non-empty list.")
        elif len(dimensions) > MAX_DIMENSIONS:
            errors.append(f"embedding.dimensions may list at most {MAX_DIMENSIONS} values.")
        else:
            for value in dimensions:
                _positive_int(value, "embedding.dimensions", errors)

    default = block.get("default")
    if default is not None:
        if isinstance(dimensions, list) and default not in dimensions:
            errors.append("embedding.default must be one of the declared dimensions.")
        else:
            _positive_int(default, "embedding.default", errors)
    return errors


def validate_attachments(block: Any) -> list[str]:
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["attachments must be an object."]

    media_types = block.get("media_types")
    if media_types is None:
        return []
    if not isinstance(media_types, dict) or not media_types:
        return ["attachments.media_types must be a non-empty object."]
    if len(media_types) > MAX_MEDIA_TYPES:
        return [f"attachments.media_types may declare at most {MAX_MEDIA_TYPES} types."]

    errors: list[str] = []
    for media_type, spec in media_types.items():
        if "/" not in str(media_type):
            errors.append(f"'{media_type}' is not a media type.")
        if spec is None:
            continue
        if not isinstance(spec, dict):
            errors.append(f"attachments.media_types['{media_type}'] must be an object.")
            continue
        # Vendors tokenise images and documents differently, so the estimate is per model — a
        # global figure would be wrong for one of them by construction (FRD-110 §5.3).
        if "tokens" in spec:
            _positive_int(spec["tokens"], f"attachments.media_types['{media_type}'].tokens", errors)
    return errors


def validate_declaration(data: dict[str, Any]) -> list[str]:
    """Every consistency rule, in one call. Returns the problems; empty means consistent."""
    max_output = data.get("max_output_tokens")
    default_output = data.get("default_max_output_tokens")

    errors = [
        *validate_capabilities(data.get("capabilities", [])),
        *validate_hosting(data.get("hosting", "")),
        *validate_thinking(data.get("thinking"), max_output_tokens=max_output),
        *validate_embedding(data.get("embedding")),
        *validate_attachments(data.get("attachments")),
    ]
    if max_output is not None and default_output is not None and default_output > max_output:
        errors.append("default_max_output_tokens must not exceed max_output_tokens.")
    return errors
