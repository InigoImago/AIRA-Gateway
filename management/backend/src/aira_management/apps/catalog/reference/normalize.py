"""A public model list, read into drafts of this catalog's own declaration (`FRD-508`).

The source is [models.dev](https://models.dev), an open list of models per provider — the one
OpenCode reads — with what each costs, how far its context reaches, what it accepts and how it
reasons. Each entry becomes a **draft**: the fields of the model editor, in this catalog's
vocabulary, for an administrator to check and save. Nothing here writes to the catalog or
approves anything (`FRD-307`).

Only the platforms and publishers this gateway can reach are read, under the provider name the
gateway stamps on them. A model whose output this gateway cannot carry (images, video) is left out:
a draft it could never serve would be a draft to delete.

What a draft carries is a **claim** by a third party, and it says so: its `source` and `as_of`
travel with it, prices are the list's (US dollars), and a model's capabilities remain what the
gateway's own checks and smoke tests measure (`FRD-131`, `FRD-504`).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

#: The list's providers this gateway reaches, as `(platform, publisher)` — the provider name the
#: gateway stamps on the model, and who made it. `None` takes the publisher from the model's id.
PROVIDERS: dict[str, tuple[str, str | None]] = {
    "google-vertex": ("vertex", None),
    "google-vertex-anthropic": ("vertex", "anthropic"),
    "google": ("generative-language", "google"),
}

#: The publishers the gateway's Vertex adapters speak: Gemini, Claude, and the managed open models
#: (`FRD-115` FR-2a). A Model Garden model from anyone else (Mistral, xAI) has no dialect here and
#: is not offered.
VERTEX_PUBLISHERS = frozenset({"google", "anthropic", "zai-org", "deepseek-ai"})

#: What an input modality means as attachment media types, in the console's vocabulary.
MEDIA_TYPES: dict[str, tuple[str, ...]] = {
    "image": ("image/png", "image/jpeg", "image/webp"),
    "pdf": ("application/pdf",),
}

#: The list's suffix for "the current version", which no platform's API spells.
DEFAULT_VERSION_SUFFIX = "@default"

#: The vendor word for "no reasoning", which is this gateway's own `disabled` mode.
NO_REASONING = "none"


def _split(model_id: str, fixed: str | None) -> tuple[str, str]:
    """``(publisher, name)``: the list writes a partner's model as `publisher/model`."""
    if "/" in model_id:
        publisher, _, name = model_id.partition("/")
        return publisher, name
    if fixed:
        return fixed, model_id
    return ("anthropic" if model_id.startswith("claude") else "google"), model_id


def _price(value: Any) -> str | None:
    """A per-million price as the catalog stores it — a decimal string — or nothing."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    return format(amount.normalize(), "f")


def _positive(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _thinking(options: Any, max_output: int | None, notes: list[str]) -> dict[str, Any] | None:
    """The list's reasoning options as this catalog's thinking declaration, or nothing.

    `effort` values are the vendor's level words, except `none`, which is the gateway's
    `disabled`; a `budget_tokens` range is the `limited` mode with its bounds; a `toggle` is
    `disabled` and `auto`. A floor above zero cannot stop thinking (`validation`), so where the list
    offers both, the floor is kept: a budget the provider refuses is the worse surprise.
    """
    if not isinstance(options, list) or not options:
        return None
    modes: list[str] = []
    levels: list[str] = []
    minimum: int | None = None
    maximum: int | None = None
    for option in options:
        if not isinstance(option, dict):
            continue
        kind = option.get("type")
        if kind == "effort":
            for word in option.get("values") or []:
                if word == NO_REASONING:
                    modes.append("disabled")
                elif isinstance(word, str) and word and word not in levels:
                    levels.append(word)
        elif kind == "budget_tokens":
            modes.append("limited")
            minimum = _positive(option.get("min"))
            maximum = _positive(option.get("max"))
        elif kind == "toggle":
            modes.extend(["disabled", "auto"])
    unique = list(dict.fromkeys(modes))
    if minimum and "disabled" in unique:
        unique.remove("disabled")
    if maximum and max_output and maximum >= max_output:
        maximum = None
    if "limited" in unique and maximum is None:
        if max_output and max_output > 1:
            # The technical ceiling, not a guess at a cost: the budget is drawn from the output
            # allowance and must stay below it.
            maximum = max_output - 1
            notes.append("the thinking ceiling is the output cap less one; the list states none")
        else:
            unique.remove("limited")
    if not unique and not levels:
        return None
    block: dict[str, Any] = {"modes": unique}
    if levels:
        block["levels"] = levels
    if minimum:
        block["min_tokens"] = minimum
    if maximum:
        block["max_tokens"] = maximum
    return block


def draft(name: str, entry: dict[str, Any], platform: str, publisher: str) -> dict[str, Any]:
    """One list entry as a draft of this catalog's declaration."""
    modalities = entry.get("modalities") or {}
    inputs = [str(item) for item in modalities.get("input") or []]
    outputs = [str(item) for item in modalities.get("output") or []]
    limit = entry.get("limit") or {}
    cost = entry.get("cost") or {}
    context = _positive(limit.get("context"))
    max_output = _positive(limit.get("output"))
    if max_output and context and max_output > context:
        # A speech model's output is audio, counted against no text window.
        max_output = None
    embeds = "embedding" in name
    notes: list[str] = []

    capabilities: list[str] = []
    if embeds:
        capabilities.append("embed")
    elif "text" in outputs:
        capabilities.append("generate")
    elif "audio" in outputs:
        capabilities.append("speech")
    if not embeds and entry.get("tool_call"):
        capabilities.append("tools")
    if not embeds and entry.get("structured_output"):
        capabilities.append("structured_output")

    media = [kind for name in inputs for kind in MEDIA_TYPES.get(name, ())]
    attachments = (
        {"media_types": dict.fromkeys(media)} if media and entry.get("attachment") else None
    )
    if attachments:
        capabilities.append("attachments")

    thinking = (
        _thinking(entry.get("reasoning_options"), max_output, notes)
        if entry.get("reasoning") and not embeds
        else None
    )
    if thinking:
        capabilities.append("thinking")
    if _price(cost.get("cache_read")) is not None and not embeds:
        capabilities.append("prompt_caching")

    name = name.removesuffix(DEFAULT_VERSION_SUFFIX)
    if cost.get("context_over_200k") or cost.get("tiers"):
        notes.append("costs more above a context length — the list price is the lower tier")
    if cost.get("input_audio") is not None:
        notes.append("audio input is priced apart")
    return {
        "platform": platform,
        "name": name,
        "display_name": str(entry.get("name") or name),
        "publisher": publisher,
        "family": str(entry.get("family") or ""),
        "status": str(entry.get("status") or ""),
        "release_date": str(entry.get("release_date") or ""),
        "knowledge": str(entry.get("knowledge") or ""),
        "description": str(entry.get("description") or ""),
        "declaration": {
            "capabilities": capabilities,
            "context_window": context,
            "max_output_tokens": None if embeds else max_output,
            "thinking": thinking,
            "attachments": attachments,
        },
        "prices": {
            "input": _price(cost.get("input")),
            "output": None if embeds else _price(cost.get("output")),
            "cached_input": None if embeds else _price(cost.get("cache_read")),
            "cache_write": None if embeds else _price(cost.get("cache_write")),
        },
        "notes": notes,
    }


def drafts(listing: Any) -> list[dict[str, Any]]:
    """Every entry of the list this gateway could serve, as drafts, in a stable order."""
    if not isinstance(listing, dict):
        return []
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for provider, (platform, fixed) in PROVIDERS.items():
        block = listing.get(provider)
        models = block.get("models") if isinstance(block, dict) else None
        if not isinstance(models, dict):
            continue
        for model_id, entry in models.items():
            if not isinstance(entry, dict) or not isinstance(model_id, str):
                continue
            outputs = (entry.get("modalities") or {}).get("output") or []
            if "text" not in outputs and outputs != ["audio"]:
                continue
            publisher, name = _split(model_id, fixed)
            if platform == "vertex" and publisher not in VERTEX_PUBLISHERS:
                continue
            if provider == "google-vertex" and publisher == "anthropic":
                # The anthropic list says the same models with their own provider block.
                continue
            made = draft(name, entry, platform, publisher)
            found.setdefault((platform, made["name"]), made)
    return [found[key] for key in sorted(found)]
