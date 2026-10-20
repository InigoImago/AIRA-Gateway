"""Consistency rules for a model declaration (FRD-114 FR-3, FR-4).

The catalog is a **runtime authority**: what it says decides whether a request is accepted. So a
declaration that cannot work is refused where it is written, not discovered as a vendor error on
somebody's request. Kept apart from the serializer because the rules are about the declaration,
not the HTTP shape.

**A declaration says only what something reads, within bounds.** Every block travels to the gateway
on every save and is read on the request path: a key nothing reads is a setting somebody tunes and
then wonders why nothing changes, and an unbounded one is an event too large for the bus to carry.
Both are refused by name. The keys are the ones `aira_gateway.catalog.ModelDeclaration` reads.
"""

from __future__ import annotations

from typing import Any

from aira_common.models import Capability, Hosting, ThinkingMode

MAX_MEDIA_TYPES = 32
MAX_DIMENSIONS = 8
#: A media type with its parameters stays far below this.
MAX_MEDIA_TYPE_LENGTH = 128
#: How many level words and task types one model declares. Generous, and finite.
MAX_LEVELS = 16
MAX_TASK_TYPES = 16
#: A level is a word a caller sends, and the gateway refuses a caller's thinking word longer than
#: its `MAX_MODE_LENGTH` — so a longer declared level could be stored and never asked for.
MAX_LEVEL_LENGTH = 32
#: A task type is a vendor's enum name, such as `SEMANTIC_SIMILARITY`.
MAX_TASK_TYPE_LENGTH = 64

THINKING_KEYS = frozenset({"modes", "min_tokens", "max_tokens", "default", "levels"})
THINKING_DEFAULT_KEYS = frozenset({"mode", "tokens"})
EMBEDDING_KEYS = frozenset({"task_types", "supports_batch", "dimensions", "default"})
ATTACHMENTS_KEYS = frozenset({"media_types"})
MEDIA_TYPE_KEYS = frozenset({"tokens"})


def _positive_int(value: Any, field: str, errors: list[str]) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        errors.append(f"{field} must be a positive integer.")
        return None
    return value


def _unknown_keys(block: dict[str, Any], allowed: frozenset[str], where: str) -> list[str]:
    """A refusal naming every key of ``block`` that nothing reads, and what it does carry."""
    extra = sorted(str(key) for key in block if key not in allowed)
    if not extra:
        return []
    return [
        f"{where} does not take {', '.join(repr(key) for key in extra)}: "
        f"it carries {', '.join(sorted(allowed))}."
    ]


def _member_of(value: Any, known: set[str]) -> bool:
    """Whether a caller's value is one of ``known`` — asked of a string only, because membership
    in a set hashes the value and a list or an object cannot be hashed."""
    return isinstance(value, str) and value in known


def validate_capabilities(values: Any) -> list[str]:
    if not isinstance(values, list):
        return ["capabilities must be a list."]
    known = {member.value for member in Capability}
    unknown = [value for value in values if not _member_of(value, known)]
    if unknown:
        return [f"Unknown capabilities: {', '.join(map(str, unknown))}. Known: {sorted(known)}."]
    return []


def validate_hosting(value: Any) -> list[str]:
    if not value:
        return []
    if not _member_of(value, {member.value for member in Hosting}):
        return [f"hosting must be one of {sorted(member.value for member in Hosting)}."]
    return []


def validate_fixed_sampling(value: Any) -> list[str]:
    """A yes or a no, never a word that reads as one: the gateway branches on it per request, and
    `"false"` is a string that is true."""
    if value is None or isinstance(value, bool):
        return []
    return ["fixed_sampling must be true or false."]


def validate_thinking(block: Any, *, max_output_tokens: int | None) -> list[str]:
    """The declaration a request is validated against, checked against itself first."""
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["thinking must be an object."]

    errors = _unknown_keys(block, THINKING_KEYS, "thinking")
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
    levels = _check_thinking_levels(block.get("levels"), errors)
    _check_thinking_default(block.get("default"), modes, levels, minimum, maximum, errors)
    # A model with a thinking floor above zero cannot stop thinking (Vertex's `gemini-2.5-pro`
    # refuses a budget below 128), so offering `disabled` beside it would be refused by the
    # provider on every request that asks for it.
    if ThinkingMode.DISABLED in modes and minimum:
        errors.append(
            f"thinking.modes offers 'disabled' while thinking.min_tokens is {minimum} — a model "
            "with a floor above zero cannot stop thinking, and every request asking it to would "
            "be refused by the provider. Drop one of the two."
        )
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
    unknown = [mode for mode in modes if not _member_of(mode, known)]
    if unknown:
        errors.append(
            f"Unknown thinking modes: {', '.join(map(str, unknown))}. "
            f"The modes are {sorted(known)}; a vendor's level word goes in thinking.levels."
        )
    elif len(modes) > len(known):
        errors.append(f"thinking.modes names each of the {len(known)} modes at most once.")
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
    levels: list[str],
    minimum: int | None,
    maximum: int | None,
    errors: list[str],
) -> None:
    """The setting a request gets when its caller names none (`FRD-111` FR-4).

    A declared mode **or a declared level**: the gateway sends the default's word as it would a
    caller's, and it accepts a caller's declared level, so a level is a default the model honours.
    Compared as the gateway compares it — stripped and lower-cased.
    """
    if default is None:
        return
    word = ""
    if isinstance(default, dict):
        errors.extend(_unknown_keys(default, THINKING_DEFAULT_KEYS, "thinking.default"))
        mode = default.get("mode")
        word = mode.strip().lower() if isinstance(mode, str) else ""
    offered = [mode for mode in modes if isinstance(mode, str)] + levels
    if not word or word not in offered:
        errors.append("thinking.default.mode must be one of the declared modes or levels.")
    elif word == ThinkingMode.LIMITED and isinstance(default, dict):
        tokens = _positive_int(default.get("tokens"), "thinking.default.tokens", errors)
        if tokens is not None and maximum is not None and not (minimum or 1) <= tokens <= maximum:
            errors.append("thinking.default.tokens must lie within the declared bounds.")


def _check_thinking_levels(levels: Any, errors: list[str]) -> list[str]:
    """A level is a word the vendor accepts; only its shape, length and duplication are checked.

    Deliberately thin (`ADR-0021`): whether a word *works* is a question for the model, which the
    console asks with one capped request. Refusing an unrecognised word here would have this plane
    invent a vocabulary again, one release behind the vendors. Returns the words as the gateway
    reads them.
    """
    if levels is None:
        return []
    if not isinstance(levels, list) or not all(isinstance(level, str) for level in levels):
        errors.append(
            "thinking.levels must be a list of the level words this model accepts, "
            "for example ['low', 'high']."
        )
        return []
    if len(levels) > MAX_LEVELS:
        errors.append(f"thinking.levels lists at most {MAX_LEVELS} words.")
        return []
    words = [level.strip().lower() for level in levels]
    if any(not word for word in words):
        errors.append("thinking.levels may not contain an empty word.")
    if any(len(word) > MAX_LEVEL_LENGTH for word in words):
        errors.append(
            f"thinking.levels holds words of at most {MAX_LEVEL_LENGTH} characters: a caller's "
            "thinking word is refused above that length, so a longer one could never be asked for."
        )
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
    return [word for word in words if word]


def validate_embedding(block: Any) -> list[str]:
    if block is None:
        return []
    if not isinstance(block, dict):
        return ["embedding must be an object."]

    errors = _unknown_keys(block, EMBEDDING_KEYS, "embedding")
    task_types = block.get("task_types")
    if task_types is not None:
        if not isinstance(task_types, list) or not all(
            isinstance(t, str) and t for t in task_types
        ):
            errors.append("embedding.task_types must be a list of non-empty strings.")
        elif len(task_types) > MAX_TASK_TYPES:
            errors.append(f"embedding.task_types lists at most {MAX_TASK_TYPES} task types.")
        elif any(len(task_type) > MAX_TASK_TYPE_LENGTH for task_type in task_types):
            errors.append(
                f"embedding.task_types holds names of at most {MAX_TASK_TYPE_LENGTH} characters."
            )

    supports_batch = block.get("supports_batch")
    if supports_batch is not None and not isinstance(supports_batch, bool):
        errors.append("embedding.supports_batch must be true or false.")

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

    errors = _unknown_keys(block, ATTACHMENTS_KEYS, "attachments")
    media_types = block.get("media_types")
    if media_types is None:
        return errors
    if not isinstance(media_types, dict) or not media_types:
        return [*errors, "attachments.media_types must be a non-empty object."]
    if len(media_types) > MAX_MEDIA_TYPES:
        return [*errors, f"attachments.media_types may declare at most {MAX_MEDIA_TYPES} types."]

    for media_type, spec in media_types.items():
        if "/" not in str(media_type):
            errors.append(f"'{media_type}' is not a media type.")
        elif len(str(media_type)) > MAX_MEDIA_TYPE_LENGTH:
            errors.append(
                f"'{str(media_type)[:40]}…' is longer than a media type: a media type is at most "
                f"{MAX_MEDIA_TYPE_LENGTH} characters."
            )
        if spec is None:
            continue
        if not isinstance(spec, dict):
            errors.append(f"attachments.media_types['{media_type}'] must be an object.")
            continue
        where = f"attachments.media_types['{media_type}']"
        errors.extend(_unknown_keys(spec, MEDIA_TYPE_KEYS, where))
        # Vendors tokenise images and documents differently, so the estimate is per model
        # (FRD-110 §5.3).
        if "tokens" in spec:
            _positive_int(spec["tokens"], f"{where}.tokens", errors)
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
        *validate_fixed_sampling(data.get("fixed_sampling")),
    ]
    # Positive like every other token figure here: a window or a cap of zero is a model that can
    # never answer, and a default cap of zero answers every caller who sets none with nothing.
    for field, value in (
        ("context_window", context),
        ("max_output_tokens", max_output),
        ("default_max_output_tokens", default_output),
    ):
        _positive_int(value, field, errors)
    if max_output is not None and default_output is not None and default_output > max_output:
        errors.append("default_max_output_tokens must not exceed max_output_tokens.")
    # The answer is drawn from the same window as the prompt, so a cap above the window cannot
    # happen (FRD-114 FR-3).
    if context is not None and max_output is not None and max_output > context:
        errors.append("max_output_tokens must not exceed context_window.")
    return errors
