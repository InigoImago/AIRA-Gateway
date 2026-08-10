"""Seed contribution: the local verification models (FRD-123).

Declares the models a `--profile verify` stack serves, so the whole governed path is exercisable
against something that did not agree with us by construction: real token counts, real latency, a
real answer stored in `request_logs`.

**The prices are invented, and they say so in their own display name.** A local model costs no
money. A price is what makes `FRD-403` demonstrable end to end — spend limits, the reporting
screen, the cost column — and inventing one is the right move for a demonstration and the wrong
one for a report. The distinction has to survive a screenshot, so it lives in the data rather than
in a comment somebody reads once.

Idempotent and keyed by model name, like every other contribution. Skipped silently when no local
endpoint is configured: a catalog full of models nobody serves is a list of things that return 404.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from aira_management.apps.catalog.models import Model
from aira_management.apps.seed.registry import SeedResult, register

#: The two models the `verify` profile pulls. Small on purpose — a 0.6b model answers badly, which
#: is irrelevant when what is under test is the gateway, and its size is what keeps a CI job from
#: pulling gigabytes.
CHAT_MODEL = os.environ.get("AIRA_SEED_LOCAL_CHAT_MODEL", "qwen3:0.6b")
EMBED_MODEL = os.environ.get("AIRA_SEED_LOCAL_EMBED_MODEL", "all-minilm")

#: Loud enough to survive being pasted into a report. `FRD-123` FR-5.
FICTITIOUS = "[local, fictitious price]"

#: Thinking modes **per model**, each measured against this Ollama on 2026-08-08. A model that is
#: absent here is declared with no thinking at all, which is the baseline and nothing more.
THINKING_BY_MODEL: dict[str, dict[str, Any]] = {
    "qwen3:0.6b": {
        "modes": ["disabled", "low", "medium", "high"],
        "default": {"mode": "disabled"},
    },
    # No `disabled`: `reasoning_effort: "none"` does not switch thinking off on this model, it
    # switches off the *separation*, and the thoughts arrive as the answer. `FRD-111` refuses a
    # request asking for a mode a model does not declare, which is the outcome that helps.
    "qwen3:4b": {"modes": ["low", "medium", "high"]},
    # `qwen2.5-coder` is deliberately **absent**: it is not a reasoning model, so it has no
    # thinking modes to declare, and the table's own rule — a model nobody has measured gets no
    # declaration — produces exactly the right answer for it.
}


#: Which models were **seen** to emit a real tool call, by sending one (`FRD-131`, measured
#: 2026-08-08). Absent means the capability is not declared, so the dispatch chain refuses a tool
#: request against that model **by name** — far better than prose a client tries to parse as a
#: function call.
#:
#: The same table exists in `tools/seed_local_catalog.py`, and this one was **missing**, which is
#: the defect this repository has now recorded twice in the same pair of files: a measurement fixed
#: in one seed and left wrong in the other. The consequence here was quiet and complete — the
#: showcase declared a model with no `tools`, so a coding assistant pointed at the demo was refused
#: by name and the whole `FRD-131` capability was unreachable from `make showcase`.
#:
#: `qwen2.5-coder:7b` is deliberately absent: `ollama show` lists `tools` for it and it returns the
#: JSON **as prose**. A vendor's capability flag is a claim; this catalog is supposed to hold
#: evidence.
TOOLS_BY_MODEL: frozenset[str] = frozenset(
    {"qwen3:0.6b", "qwen3:4b", "qwen2.5:1.5b", "qwen2.5:3b", "qwen2.5:7b"}
)


def _chat_capabilities() -> list[str]:
    """What the configured chat model is declared able to do, measured model by model.

    `prompt_caching` is **not** here and that is deliberate: this runtime reports no cached tokens
    at all, so declaring it would put a marker on the wire that nothing honours and a share in the
    console that can only ever read 0 %. Absence of information is not permission (`FRD-114`
    FR-7), and an invented capability is worse than a missing one.
    """
    capabilities = ["generate", "structured_output"]
    if CHAT_MODEL in THINKING_BY_MODEL:
        capabilities.append("thinking")
    if CHAT_MODEL in TOOLS_BY_MODEL:
        capabilities.append("tools")
    return capabilities


def _declarations() -> list[dict[str, Any]]:
    return [
        {
            "name": CHAT_MODEL,
            "display_name": f"Local chat model {FICTITIOUS}",
            "provider": "ollama",
            "publisher": "local",
            "platform": "ollama",
            "hosting": "self_deployed",
            # Priced like a small cloud model so the arithmetic in a report is recognisable, and
            # so a budget set in the UI actually bites within a demonstration rather than after
            # ten thousand requests.
            "input_price_per_million": Decimal("0.100000"),
            "output_price_per_million": Decimal("0.400000"),
            # `structured_output` is measured too: this dialect takes a named `json_schema`, and
            # the running model returns a document that satisfies one.
            "capabilities": _chat_capabilities(),
            "max_output_tokens": 4096,
            "default_max_output_tokens": 512,
            # **Measured, not guessed.** This block was deliberately empty until an integration
            # run against the running model answered the question (`FRD-114` FR-7: undeclared
            # means the baseline and nothing more). It has now answered it, and the answer
            # includes a correction worth keeping:
            #
            #   `minimal` is **not** declared. The catalog is not free to invent a mode: this
            #   server accepts `none`, `low`, `medium`, `high` and `max`, and refuses `minimal`
            #   *by name*. A hand-written declaration that included it produced a request the
            #   model rejected — the mirror image of the mistake this comment was written to
            #   prevent, and the reason to declare what a run showed rather than what an enum
            #   offers.
            #
            # `attachments` stays undeclared: this endpoint carries images in the dialect, but no
            # local model here has been measured reading one.
            #   **Corrected again on 2026-08-08, and this time the correction is structural.**
            #   The set above was measured against `qwen3:0.6b` and then asserted about whatever
            #   model `AIRA_SEED_LOCAL_CHAT_MODEL` happens to name. Against `qwen3:4b`, on the
            #   same server in the same minute, `disabled` is **wrong**: the dialect maps it to
            #   `reasoning_effort: "none"`, and that model reads `none` as "emit no separate
            #   reasoning channel" rather than "do not think" — so 103 tokens of raw
            #   chain-of-thought come back **as the answer**, with a 200 and nothing to say why.
            #   The 0.6B model answers the same request in 3 tokens.
            #
            #   So the declaration is keyed by model, and a model nobody has measured gets **no
            #   thinking declaration at all** (`FRD-114` FR-7 — absence of information is not
            #   permission). A capability belongs to a model, not to a family or a runtime.
            "thinking": THINKING_BY_MODEL.get(CHAT_MODEL),
            "approved": True,
            "numeric_id": 9001,
        },
        {
            "name": EMBED_MODEL,
            "display_name": f"Local embedding model {FICTITIOUS}",
            "provider": "ollama",
            "publisher": "local",
            "platform": "ollama",
            "hosting": "self_deployed",
            "input_price_per_million": Decimal("0.010000"),
            "output_price_per_million": Decimal("0.010000"),
            "capabilities": ["embed"],
            # This surface takes the whole batch in one `input` array, so batching is real here.
            # Task types are **not** declared, because the wire format has none — and `FRD-113`
            # refuses an undeclared one rather than sending a request that would be ignored.
            "embedding": {"supports_batch": True},
            "approved": True,
            "numeric_id": 9002,
        },
    ]


@register(name="local_models", order=40)
def seed_local_models(fresh: bool) -> SeedResult:
    """Declare the local models, if a local endpoint is configured."""
    # Either configuration form counts. `AIRA_OLLAMA_URL` is the single-endpoint setting this
    # contribution was written against; `AIRA_OPENAI_SERVERS` is the named-server form `FRD-123`
    # moved to when a self-hosted fleet turned out to be several machines. Checking only the first
    # meant the demo stack — configured with the second — seeded **no models at all**, and the
    # catalog silently had nothing for the use cases to point at.
    if not (os.environ.get("AIRA_OLLAMA_URL") or os.environ.get("AIRA_OPENAI_SERVERS")):
        return {"local_models": 0}

    if fresh:
        Model.objects.filter(platform="ollama").delete()

    created = 0
    for declaration in _declarations():
        _, was_created = Model.objects.update_or_create(
            name=declaration["name"], defaults=declaration
        )
        created += int(was_created)
    return {"local_models": created}
