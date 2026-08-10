"""What a model can do, in one vocabulary both planes share (FRD-114).

Management authors the declarations and validates them; the gateway enforces them. Two copies of
"which capabilities exist" would drift, and the drift would be discovered in whichever plane was
not tested — the same argument that put :mod:`aira_common.roles` here.

The rule the vocabulary exists to serve is `ADR-0011` rule 3: a flag says **whether** a model can
do something, never **how**. Three vendors already produce structured output by three unrelated
mechanisms, and the mechanism belongs in the upstream dialect, not in a catalog every plane reads.
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
    #: unsupported, like every other flag here — a model whose catalog entry is silent is not
    #: assumed capable, it is skipped by name.
    TOOLS = "tools"
    #: The provider will honour a cache marker on this model's stable prefix (`FRD-133`).
    #:
    #: **Undeclared means unsupported, and unsupported does not mean skipped** — the one place in
    #: this vocabulary where a missing capability is not a dispatch condition. A model that cannot
    #: cache still answers the question correctly; it just costs more. Skipping it would refuse a
    #: request over a *price*, which is the opposite of what a fallback chain is for. Every other
    #: flag here guards the **answer**, and that difference is why this comment exists: somebody
    #: will otherwise "fix" the inconsistency.
    PROMPT_CACHING = "prompt_caching"


class ThinkingMode(StrEnum):
    """The reasoning-effort vocabulary (`FRD-111`), taken from the predecessor's.

    It turned out to cover vendors it was not written for: Google takes a token budget, Anthropic
    a budget bounded by ``max_tokens``, Azure an abstract effort level — and ``mode`` plus an
    optional token count expresses all three.
    """

    DISABLED = "disabled"
    LIMITED = "limited"
    AUTO = "auto"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    MINIMAL = "minimal"


class Hosting(StrEnum):
    """Who runs the model, because the two fail differently (`ADR-0012` §5).

    A ``self_deployed`` endpoint scaled to zero can cold-start for minutes, and its 429 means *no
    free replica* rather than quota — so retrying the same endpoint cannot help. The dispatch
    timeout, the retry decision and the readiness probe all read this.
    """

    MANAGED = "managed"
    SELF_DEPLOYED = "self_deployed"


#: What a model with **no** declaration may do. Deliberately the two things that already worked
#: before this FRD, so nothing regresses — and deliberately nothing more (`FRD-114` FR-7).
#:
#: The tempting default is permissive: let an undeclared model accept everything and let the
#: provider complain. That is wrong here for the same reason "unpriced is not free" is: absence of
#: information is not permission. An undeclared model would otherwise accept a 32 768-token
#: thinking budget that the reservation would then have to estimate against nothing.
BASELINE_CAPABILITIES: frozenset[Capability] = frozenset({Capability.GENERATE, Capability.EMBED})


def parse_capabilities(values: object) -> frozenset[Capability]:
    """Read a declared capability set, ignoring anything not in the vocabulary.

    Unknown values are dropped rather than raising: this runs on the gateway's consumer path,
    where a Management release that adds a capability must not stop an older gateway from applying
    the rest of the event. Dropping is the fail-closed direction — an unrecognised capability is
    one the gateway could not enforce anyway.
    """
    if not isinstance(values, list | tuple | set | frozenset):
        return frozenset()
    known = {member.value for member in Capability}
    return frozenset(Capability(value) for value in values if value in known)
