"""What a model can do, in one vocabulary both planes share (FRD-114).

Management authors and validates the declarations; the gateway enforces them. One definition, so
the two cannot drift — the argument that put :mod:`aira_common.roles` here.

A flag says **whether** a model can do something, never **how** (`ADR-0011` rule 3): vendors
produce structured output by unrelated mechanisms, and the mechanism belongs in the upstream
dialect, not in a catalog every plane reads.
"""

from __future__ import annotations

from enum import StrEnum


class Capability(StrEnum):
    """What a model is declared able to do."""

    GENERATE = "generate"
    EMBED = "embed"
    STRUCTURED_OUTPUT = "structured_output"
    THINKING = "thinking"
    ATTACHMENTS = "attachments"
    #: The model can be given functions and answer by asking for one (`FRD-131`). Undeclared means
    #: unsupported, like every other flag here: the model is skipped by name.
    TOOLS = "tools"
    #: The provider will honour a cache marker on this model's stable prefix (`FRD-133`).
    #:
    #: **The one flag that is not a dispatch condition.** A model that cannot cache still answers
    #: correctly, only at a higher price, and skipping it would refuse a request over cost. Every
    #: other flag guards the answer; the difference is deliberate, not an inconsistency to fix.
    PROMPT_CACHING = "prompt_caching"
    #: The model answers with speech: text in, audio out (`FRD-624`). A speech-only model declares
    #: this and not `generate`, so a text request to it is refused by name rather than sent to a
    #: provider that refuses it without saying why.
    SPEECH = "speech"


class ThinkingMode(StrEnum):
    """The three reasoning settings that are **ours**, not a vendor's (`FRD-111`, `ADR-0021`).

    Decisions the gateway makes and every dialect spells differently: ``disabled`` is Google's
    ``thinkingBudget: 0`` and OpenAI's ``reasoning_effort: "none"``; ``auto`` is Google's ``-1`` and
    does not exist on Anthropic; ``limited`` is a number the **caller** named.

    Anything else a caller asks for is a vendor **level word** (``low``, ``high``, …), declared per
    model as free text and passed through untranslated. No vendor publishes what a level costs in
    tokens, so there is deliberately no ``level → tokens`` table: an invented figure would silently
    truncate a run that needed more.
    """

    DISABLED = "disabled"
    LIMITED = "limited"
    AUTO = "auto"


#: The three above as plain strings, for code that reads a caller's word before knowing the model.
CONTROL_MODES = frozenset(member.value for member in ThinkingMode)


def is_control_mode(mode: str) -> bool:
    """Whether this word is one of the gateway's three, rather than a vendor's level word."""
    return mode in CONTROL_MODES


class Hosting(StrEnum):
    """Who runs the model, because the two fail differently (`ADR-0012` §5).

    A ``self_deployed`` endpoint scaled to zero can cold-start for minutes, and its 429 means *no
    free replica* rather than quota — so retrying the same endpoint cannot help. The dispatch
    timeout, the retry decision and the readiness probe all read this.
    """

    MANAGED = "managed"
    SELF_DEPLOYED = "self_deployed"


#: What a model with **no** declaration may do: the two things that worked before `FRD-114`, and
#: nothing more (FR-7). Absence of information is not permission — an undeclared model would
#: otherwise accept a thinking budget the reservation has nothing to estimate against.
BASELINE_CAPABILITIES: frozenset[Capability] = frozenset({Capability.GENERATE, Capability.EMBED})


def parse_capabilities(values: object) -> frozenset[Capability]:
    """Read a declared capability set, ignoring anything not in the vocabulary.

    Unknown values are dropped rather than raising: on the gateway's consumer path, a newer
    Management's capability must not stop an older gateway from applying the rest of the event.
    Dropping is the fail-closed direction — the gateway could not enforce it anyway.
    """
    if not isinstance(values, list | tuple | set | frozenset):
        return frozenset()
    known = {member.value for member in Capability}
    return frozenset(Capability(value) for value in values if value in known)
