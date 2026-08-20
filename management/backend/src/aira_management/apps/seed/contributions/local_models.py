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

import json
import os
import urllib.request
from decimal import Decimal
from typing import Any

from django.db import transaction

from aira_management.apps.catalog.models import Model
from aira_management.apps.catalog.views import _payload
from aira_management.apps.seed.registry import SeedResult, register
from aira_management.apps.usecases.events import emit

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
    # **`minimal` is gone, and this is the third time it has moved.** It was struck on 2026-08-06
    # because this server refuses the value by name; put back on the argument that `FRD-111`'s
    # OpenAI mapping sent it as `"low"`, so a caller asking for it was honoured by the request
    # `low` makes; and removed again now, because `ADR-0021` deleted that translation. A level is
    # the vendor's own word and travels untranslated, so declaring `minimal` here writes a
    # declaration that answers `400` on its first use.
    #
    # Measured against this Ollama on 2026-08-19, and the message is the server's own:
    #
    #     invalid reasoning value: 'minimal' (must be "high", "medium", "low", "max", or "none")
    #
    # A fact about the **server's parameter validation**, not about either model — which is why it
    # is removed from both, and why `max` (a word no vocabulary in this project ever had) is
    # declared for the one it was measured on.
    "qwen3:0.6b": {
        "modes": ["disabled"],
        "levels": ["low", "medium", "high", "max"],
        "default": {"mode": "disabled"},
    },
    # No `disabled`: `reasoning_effort: "none"` does not switch thinking off on this model, it
    # switches off the *separation*, and the thoughts arrive as the answer. `FRD-111` refuses a
    # request asking for a mode a model does not declare, which is the outcome that helps.
    #
    # `max` is **not** claimed here: the server takes it, and whether this model is the one it was
    # measured on is a different question. A declaration nobody measured is the thing the console's
    # *Ask the model* button exists to catch, and seeding one would be seeding the defect.
    "qwen3:4b": {"levels": ["low", "medium", "high"]},
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
            # **Measured, and it had not been.** This said `4096` — a plausible-looking number
            # sitting between two fields that each carry a "measured on …" note, with none of its
            # own. It was not a fact about this model: `ollama show` reports
            # `qwen3.context_length = 40960`, and the runtime accepts `max_tokens` of 32 000,
            # 40 961 and 100 000 without complaint, truncating at the context window instead.
            #
            # The consequence was a refusal of traffic the model would have served. An agentic
            # coding client asks for a large output budget as a matter of course — OpenCode sends
            # 32 000 — so the first ordinary request from the very client `FRD-132` set this up for
            # was answered `maxOutputTokens 32000 exceeds the 4096 this model accepts`. The control
            # was working; the number it enforced was invented. `FRD-114` FR-7 in the direction
            # this repository usually meets it from the other side.
            #
            # **The context length, because this runtime has no separate output limit.** Ollama
            # enforces nothing here — a cap above the window is silently truncated — so the only
            # honest ceiling is the window itself. Prompt and answer share it, which makes this a
            # ceiling rather than a promise: 40 960 output tokens are reachable only with an empty
            # prompt, exactly as on any model whose two figures are one.
            "max_output_tokens": 40960,
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
            # **The predecessor's own chat id** (`ADR-0010`, `docs/MIGRATION-KIRA.md`), carried by
            # the demo deliberately. `FRD-107`'s promise is that a KIRA client migrates by changing
            # a base URL; while this was `9001`, every document said `model_id: 1004` and the one
            # command the showcase prints said something else. The number is assigned in the
            # catalog exactly so an installation can match what its clients already send.
            #
            # Must stay equal to `tools/seed_local_catalog.py`'s `CHAT_NUMERIC_ID`: two seeds
            # write this row, and a mismatch is a model that answers to a different number
            # depending on which one ran last.
            "numeric_id": 1004,
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
            # Measured against this Ollama, not taken from the model card: two texts of very
            # different length both came back with **384** values. `all-minilm` is the only
            # embedding model this seed declares, and a second one would need its own measurement
            # — a dimension is a property of a model, not of a runtime (`FRD-114` FR-7).
            #
            # Declared because the compatibility surface reports it in `GET /models`, and a client
            # sizing a vector store reads that field. Absent, it had to guess or embed once and
            # count, which is the thing this catalog exists to spare it.
            "embedding": {"supports_batch": True, "dimensions": [384], "default": 384},
            "approved": True,
            "numeric_id": 9002,
        },
    ]


def _served_models(timeout: float = 5.0) -> set[str] | None:
    """Which models the local endpoint actually serves right now.

    `None` when it cannot be asked, which is different from "serves nothing" and is treated as
    such: an unreachable endpoint means *do not declare*, not *declare everything*.

    This replaces an ordering constraint with evidence. The seed container used to wait for the
    model pull to **succeed**, so one failed download — a blocked registry, a flaky minute — meant
    the seed never ran at all: no demo accounts, no use cases, no budgets, and a console that came
    up empty with nothing anywhere connecting the two. `FRD-130`'s rule is the one that matters
    (a model nobody pulled must not be catalogued, because every request against it fails), and
    asking the endpoint keeps it while letting everything unrelated to models be seeded.

    Uses the OpenAI-compatible listing, because that is the dialect this catalog is written
    against (`FRD-123`) — not Ollama's native API, which would tie the check to one runtime.
    """
    endpoint = _local_endpoint()
    if not endpoint:
        return None
    try:
        with urllib.request.urlopen(  # noqa: S310 - scheme comes from our own configuration
            f"{endpoint.rstrip('/')}/v1/models", timeout=timeout
        ) as response:
            payload = json.loads(response.read())
    except Exception:  # noqa: BLE001 - unreachable is an answer, and it is "do not declare"
        return None
    # `or []`, not a default argument. A default only applies when the key is **absent**, and this
    # endpoint answers `{"data": null}` while it serves nothing — which is precisely the state on a
    # first run, mid-pull. `payload.get("data", [])` therefore returned `None` and this line raised
    # `TypeError: 'NoneType' object is not iterable`, taking the whole seed down with it: no demo
    # accounts, no use cases, no keys, and eleven 401s from traffic that ran anyway.
    #
    # The mechanism above is written to answer "which models are really there" with evidence, and
    # it crashed in the one case it exists for. *Absent and empty are different answers* — this
    # project's own rule, here between absent and **null**.
    return {_tagged(entry["id"]) for entry in payload.get("data") or [] if entry.get("id")}


def _tagged(model: str) -> str:
    """A model name with its tag made explicit.

    An absent tag means `:latest`, and the endpoint answers with the explicit form: the catalog
    says `all-minilm`, the listing says `all-minilm:latest`, and comparing them as strings quietly
    dropped the embedding model from the catalog. Found by running the check rather than by
    reading it — the same family as the colon that once split `qwen3:0.6b` into a model nobody
    served.
    """
    return model if ":" in model else f"{model}:latest"


def _local_endpoint() -> str | None:
    """The first URL in whichever of the two configuration forms is set (`FRD-123`)."""
    single = os.environ.get("AIRA_OLLAMA_URL")
    if single:
        return single
    servers = os.environ.get("AIRA_OPENAI_SERVERS", "")
    for server in servers.split(";"):
        parts = server.split("=", 1)
        if len(parts) == 2 and parts[1]:
            return parts[1].split("|")[0]
    return None


@register(name="local_models", order=40)
def seed_local_models(fresh: bool) -> SeedResult:
    """Declare the local models the endpoint actually serves, if one is configured."""
    # Either configuration form counts. `AIRA_OLLAMA_URL` is the single-endpoint setting this
    # contribution was written against; `AIRA_OPENAI_SERVERS` is the named-server form `FRD-123`
    # moved to when a self-hosted fleet turned out to be several machines. Checking only the first
    # meant the demo stack — configured with the second — seeded **no models at all**, and the
    # catalog silently had nothing for the use cases to point at.
    if not (os.environ.get("AIRA_OLLAMA_URL") or os.environ.get("AIRA_OPENAI_SERVERS")):
        return {"local_models": 0}

    if fresh:
        for stale in Model.objects.filter(platform="ollama"):
            name = stale.name
            with transaction.atomic():
                stale.delete()
                emit("model.deleted", {"name": name})

    served = _served_models()
    if served is None:
        print("[seed] local_models: the local endpoint did not answer; declaring nothing")
        return {"local_models": 0}

    created = 0
    for declaration in _declarations():
        if _tagged(str(declaration["name"])) not in served:
            # Said out loud. A model missing from the catalog is the difference between a demo
            # that works and one whose every request is refused as "not in the model catalog",
            # and the seed is the only place that knows why.
            print(f"[seed] local_models: '{declaration['name']}' is not served here; skipping")
            continue
        with transaction.atomic():
            model, was_created = Model.objects.update_or_create(
                name=declaration["name"], defaults=declaration
            )
            # **Announced, not merely written.** This contribution wrote the rows and emitted
            # nothing, so Management's catalog filled up and the gateway's read-model stayed
            # empty — and since `FRD-307` only a catalogued, approved model may be served. On a
            # machine that had never run the gateway-side seed by hand, `make showcase` therefore
            # drove ten requests and had all ten refused with "not in the model catalog", while
            # reporting success. Nothing failed anywhere: two correct halves and no wire between
            # them, the fourth instance of that shape in this repository.
            #
            # `_payload` is the viewset's, deliberately. A second hand-written payload here is a
            # second place to forget a field — and prices travel as decimal strings for a reason
            # that only shows up as costs nobody can reconcile.
            emit("model.upserted", _payload(model))
        created += int(was_created)
    return {"local_models": created}
