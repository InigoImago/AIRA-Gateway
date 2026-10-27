"""What a model does when a request names no thinking setting, where its vendor says (`FRD-151`).

The list states which thinking settings a model takes, never what it does when a request names
none — and that is the fact the gateway needs to return reasoning a model produces anyway. Google
sends thoughts only when a request asks for them, and refuses the asking where the model is not
thinking; Claude's newest models think on every request and return the thinking empty unless the
request asks otherwise. Either way the gateway asks only when it knows the model is thinking, and
the catalogue says so with the model's thinking default.

So a draft proposes the default the vendor documents for **that model**: the setting that, sent
on this gateway's wire, reproduces what the model does when nothing is sent. It costs nothing the
model would not spend anyway. Where the vendor documents nothing, or documents that the model
thinks only when asked, nothing is proposed and the administrator decides.
"""

from __future__ import annotations

from typing import Any

#: The gateway's own mode for "the model decides how much": a dynamic budget on Google's wire,
#: adaptive thinking without an effort on Anthropic's.
AUTO = "auto"

#: Per model, the setting that reproduces what it does unasked, as its vendor documents it. Named
#: one by one: a vendor sets this per model, not per family — Claude Sonnet 4.6 thinks only when
#: asked, Claude Sonnet 5 whenever it is not told otherwise.
VENDOR_DEFAULTS: dict[str, str] = {
    # Google, "Supported thinking levels by model" and "Supported thinking budgets by model"
    # (https://docs.cloud.google.com/vertex-ai/generative-ai/docs/thinking): a Gemini 3 or later
    # model uses the level named there; a Gemini 2.5 model sets its budget dynamically, which is
    # `auto`. Where the documented level is `minimal`, nothing is proposed: the model then barely
    # thinks, so there is next to no reasoning to return. Gemini 2.5 Flash-Lite is left out because
    # Google's two references disagree on it — dynamic on Vertex AI, off in the Gemini API.
    "gemini-2.5-flash": AUTO,
    "gemini-2.5-pro": AUTO,
    "gemini-3-flash-preview": "high",
    "gemini-3-pro-image": "high",
    "gemini-3.1-pro-preview": "high",
    "gemini-3.5-flash": "medium",
    "gemini-3.6-flash": "medium",
    "gemini-3.7-flash": "medium",
    "gemini-3.8-flash": "medium",
    "gemini-4-argon": "medium",
    "gemini-nano-banana-2.1": "medium",
    # Anthropic (https://platform.claude.com/docs/en/about-claude/models/migration-guide): adaptive
    # thinking runs whenever a request leaves `thinking` out — always on for Claude Fable 5 and 5.1
    # and Claude Opus 5.5, on by default for Claude Opus 5, Sonnet 5, Sonnet 5.5 and Haiku 5.5. Opus
    # 4.7 and 4.8 think adaptively too, but only when asked.
    "claude-fable-5": AUTO,
    "claude-fable-5-1": AUTO,
    "claude-haiku-5-5": AUTO,
    "claude-opus-5": AUTO,
    "claude-opus-5-5": AUTO,
    "claude-sonnet-5": AUTO,
    "claude-sonnet-5-5": AUTO,
}


def with_vendor_default(
    name: str, thinking: dict[str, Any] | None, notes: list[str]
) -> dict[str, Any] | None:
    """``thinking`` with the vendor's documented default proposed, where it documents one.

    A proposal of `auto` declares `auto` too: what the vendor describes is the model deciding,
    which is what `auto` asks for, whether or not the list names it. A proposed level must already
    be one the list declares — the two sources disagreeing is for the administrator to settle, not
    for a draft to paper over.
    """
    word = VENDOR_DEFAULTS.get(name)
    if thinking is None or word is None:
        return thinking
    if word == AUTO:
        modes = list(thinking.get("modes") or [])
        proposed = {**thinking, "modes": modes if AUTO in modes else [*modes, AUTO]}
    elif word in (thinking.get("levels") or []):
        proposed = dict(thinking)
    else:
        notes.append(
            f"the vendor's own thinking level '{word}' is not among the levels the list declares"
        )
        return thinking
    notes.append(
        f"thinking default '{word}' proposed — what the vendor documents the model doing unasked, "
        "so it costs nothing more and lets the gateway return the reasoning"
    )
    return {**proposed, "default": {"mode": word}}
