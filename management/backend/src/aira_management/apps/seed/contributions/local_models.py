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
            "capabilities": ["generate", "structured_output", "thinking"],
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
            "thinking": {
                "modes": ["disabled", "low", "medium", "high"],
                "default": {"mode": "disabled"},
            },
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
