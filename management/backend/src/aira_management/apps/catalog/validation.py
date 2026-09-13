"""Consistency rules for a model declaration (FRD-114 FR-3, FR-4).

The catalog is a **runtime authority**: what it says decides whether a request is accepted. So a
declaration that cannot work is refused where it is written, not discovered as a vendor error on
somebody's request. Kept apart from the serializer because the rules are about the declaration,
not the HTTP shape.
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
    modes = _thinking_modes(block.get("modes"), errors)
    # Modes and levels are each optional: a model may offer level words and no control mode at
    # all (an OpenAI-compatible server with `reasoning_effort` and no "you decide").
    if not modes and not block.get("levels"):
        errors.append(
            "thinking declares neither a mode nor a level, so nothing about it can be asked for. "
            "Give it thinking.modes, thinking.levels, or remove the block."
        )
    minimum = _positive_int(block.get("min_tokens"), "thinking.min_tokens", errors)
    maximum = _positive_int(block.get("max_tokens"), "thinking.max_tokens", errors)
    _check_thinking_bounds(modes, minimum, maximum, max_output_tokens, errors)
    _check_thinking_default(block.get("default"), modes, minimum, maximum, errors)
    # A model with a thinking floor above zero cannot stop thinking (Vertex's `gemini-2.5-pro`
    # refuses a budget below 128), so offering `disabled` beside it would be refused by the
    # provider on every request that asks for it.
    if ThinkingMode.DISABLED in modes and minimum:
        errors.append(
            f"thinking.modes offers 'disabled' while thinking.min_tokens is {minimum} — a model "
            "with a floor above zero cannot stop thinking, and every request asking it to would "
            "be refused by the provider. Drop one of the two."
        )
    _check_thinking_levels(block.get("levels"), errors)
    return errors


def _thinking_modes(modes: Any, errors: list[str]) -> list[Any]:
    """The declared modes — a **closed set**, unlike the levels.

    The modes are settings the gateway owns and translates per dialect (`disabled` is Google's
    budget `0` and OpenAI's `"none"`), so an unknown one is a typo rather than a vendor's new word.
    """
    if modes is None:
        return []
    if not isinstance(modes, list):
        errors.append("thinking.modes must be a list.")
        return []
    known = {member.value for member in ThinkingMode}
    unknown = [mode for mode in modes if mode not in known]
    if unknown:
        errors.append(
            f"Unknown thinking modes: {', '.join(map(str, unknown))}. "
            f"The modes are {sorted(known)}; a vendor's level word goes in thinking.levels."
        )
    return modes


def _check_thinking_bounds(
    modes: list[Any],
    minimum: int | None,
    maximum: int | None,
    max_output_tokens: int | None,
    errors: list[str],
) -> None:
    if minimum is not None and maximum is not None and minimum > maximum:
        errors.append("thinking.min_tokens must not exceed thinking.max_tokens.")
    # Anthropic draws thinking tokens from `max_tokens`, so a budget at or above the output cap
    # describes a model that can never answer (FRD-119 §5.4).
    if maximum is not None and max_output_tokens is not None and maximum >= max_output_tokens:
        errors.append(
            "thinking.max_tokens must be below max_output_tokens — the thinking budget is drawn "
            "from the output allowance, so a request at this budget could never answer."
        )
    if ThinkingMode.LIMITED in modes and maximum is None:
        errors.append("thinking.max_tokens is required when 'limited' is offered.")


def _check_thinking_default(
    default: Any,
    modes: list[Any],
    minimum: int | None,
    maximum: int | None,
    errors: list[str],
) -> None:
    if default is None:
        return
    if not isinstance(default, dict) or default.get("mode") not in modes:
        errors.append("thinking.default.mode must be one of the declared modes.")
    elif default.get("mode") == ThinkingMode.LIMITED:
        tokens = _positive_int(default.get("tokens"), "thinking.default.tokens", errors)
        if tokens is not None and maximum is not None and not (minimum or 1) <= tokens <= maximum:
            errors.append("thinking.default.tokens must lie within the declared bounds.")


def _check_thinking_levels(levels: Any, errors: list[str]) -> None:
    """A level is a word the vendor accepts; only its shape and duplication are checked here.

    Deliberately thin (`ADR-0021`): whether a word *works* is a question for the model, which the
    console asks with one capped request. Refusing an unrecognised word here would have this plane
    invent a vocabulary again, one release behind the vendors.
    """
    if levels is None:
        return
    if not isinstance(levels, list) or not all(isinstance(level, str) for level in levels):
        errors.append(
            "thinking.levels must be a list of the level words this model accepts, "
            "for example ['low', 'high']."
        )
        return
    words = [level.strip().lower() for level in levels]
    if any(not word for word in words):
        errors.append("thinking.levels may not contain an empty word.")
    if len(set(words)) != len(words):
        errors.append("thinking.levels names the same level twice.")
    # A mode is translated per dialect; listed as a level it would be sent verbatim and mean
    # something else.
    clashes = sorted(set(words) & {str(member) for member in ThinkingMode})
    if clashes:
        errors.append(
            f"thinking.levels names {clashes}, which are thinking modes rather than "
            "vendor level words — declare them in thinking.modes."
        )


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
        # Vendors tokenise images and documents differently, so the estimate is per model
        # (FRD-110 §5.3).
        if "tokens" in spec:
            _positive_int(spec["tokens"], f"attachments.media_types['{media_type}'].tokens", errors)
    return errors


def validate_declaration(data: dict[str, Any]) -> list[str]:
    """Every consistency rule, in one call. Returns the problems; empty means consistent."""
    max_output = data.get("max_output_tokens")
    default_output = data.get("default_max_output_tokens")
    context = data.get("context_window")

    errors = [
        *validate_capabilities(data.get("capabilities", [])),
        *validate_hosting(data.get("hosting", "")),
        *validate_thinking(data.get("thinking"), max_output_tokens=max_output),
        *validate_embedding(data.get("embedding")),
        *validate_attachments(data.get("attachments")),
    ]
    if max_output is not None and default_output is not None and default_output > max_output:
        errors.append("default_max_output_tokens must not exceed max_output_tokens.")
    # The answer is drawn from the same window as the prompt, so a cap above the window cannot
    # happen (FRD-114 FR-3).
    if context is not None and max_output is not None and max_output > context:
        errors.append("max_output_tokens must not exceed context_window.")
    return errors
