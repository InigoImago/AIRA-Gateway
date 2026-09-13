"""Seed contribution: the local verification models (FRD-123).

Declares the models a `--profile verify` stack serves, so the governed path is exercisable against
a real model: real token counts, real latency, a real answer stored in `request_logs`.

**The prices are invented, and say so in their own display name** (`FRD-123` FR-5). A price is what
makes `FRD-403`'s limits and reports demonstrable, and the distinction has to survive a screenshot.

Every capability here is **measured** against the running model, never taken from a vendor flag or
an enum (`FRD-114` FR-7: absence of information is not permission). `tools/seed_local_catalog.py`
holds the same measurements, and `tools/tests/test_local_model_measurements_agree.py` keeps the two
equal. Idempotent, keyed by model name, and only for models the endpoint actually serves.
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

#: The two models the `verify` profile pulls. Small on purpose: the gateway is under test, not the
#: model, and a CI job must not pull gigabytes.
CHAT_MODEL = os.environ.get("AIRA_SEED_LOCAL_CHAT_MODEL", "qwen3:0.6b")
EMBED_MODEL = os.environ.get("AIRA_SEED_LOCAL_EMBED_MODEL", "all-minilm")

#: Loud enough to survive being pasted into a report. `FRD-123` FR-5.
FICTITIOUS = "[local, fictitious price]"

#: Thinking modes and levels **per model** — a capability belongs to a model, not a family or a
#: runtime. A model absent here is declared with no thinking at all. Levels are the vendor's own
#: words and travel untranslated (`ADR-0021`); this server refuses `minimal` by name
#: (`must be "high", "medium", "low", "max", or "none"`), so no model declares it.
THINKING_BY_MODEL: dict[str, dict[str, Any]] = {
    "qwen3:0.6b": {
        "modes": ["disabled"],
        "levels": ["low", "medium", "high", "max"],
        "default": {"mode": "disabled"},
    },
    # No `disabled`: `reasoning_effort: "none"` does not stop this model thinking, it returns the
    # thoughts as the answer. No `max`: the server takes the word, but it was not measured here.
    "qwen3:4b": {"levels": ["low", "medium", "high"]},
    # `qwen2.5-coder` is absent: it is not a reasoning model.
}

#: Models **seen** to emit a real tool call (`FRD-131`). Absent means `tools` is not declared, so a
#: tool request is refused by name rather than answered with prose a client parses as a call.
#: `qwen2.5-coder:7b` is absent: `ollama show` lists `tools` for it, and it returns the JSON as
#: prose.
TOOLS_BY_MODEL: frozenset[str] = frozenset(
    {"qwen3:0.6b", "qwen3:4b", "qwen2.5:1.5b", "qwen2.5:3b", "qwen2.5:7b"}
)


def _chat_capabilities() -> list[str]:
    """What the configured chat model is declared able to do, measured model by model.

    `prompt_caching` is **not** declared: this runtime reports no cached tokens, so the marker would
    promise something nothing honours and the console's share could only ever read 0 %.
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
            # Priced like a small cloud model, so report arithmetic is recognisable and a budget
            # set in the UI bites within a demonstration.
            "input_price_per_million": Decimal("0.100000"),
            "output_price_per_million": Decimal("0.400000"),
            # `structured_output` is measured too: this dialect takes a named `json_schema`, and
            # the model returns a document that satisfies one.
            "capabilities": _chat_capabilities(),
            # The context length (`ollama show`: 40960). Ollama has no separate output limit and
            # truncates at the window, so the window is the only honest ceiling for both — and an
            # agentic client asks for a 32 000-token output budget as a matter of course.
            "context_window": 40960,
            "max_output_tokens": 40960,
            "default_max_output_tokens": 512,
            # `attachments` stays undeclared: no local model here was measured reading one.
            "thinking": THINKING_BY_MODEL.get(CHAT_MODEL),
            "approved": True,
            # The predecessor's own chat id (`ADR-0010`, `docs/MIGRATION-KIRA.md`), so a KIRA
            # client migrates by changing a base URL. Must equal `tools/seed_local_catalog.py`'s
            # `CHAT_NUMERIC_ID`: both seeds write this row.
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
            # Batching is real here (one `input` array). Task types are not declared, because the
            # wire format has none and `FRD-113` refuses an undeclared one. 384 dimensions,
            # measured rather than read off the model card, and published so a client sizing a
            # vector store need not guess.
            "embedding": {"supports_batch": True, "dimensions": [384], "default": 384},
            "approved": True,
            "numeric_id": 9002,
        },
    ]


def _served_models(timeout: float = 5.0) -> set[str] | None:
    """Which models the local endpoint serves right now, or `None` when it cannot be asked.

    `None` means *do not declare*, not *declare everything*. Asking replaces waiting for the model
    pull to succeed: everything unrelated to models is seeded even when a download fails, and a
    model nobody pulled is still never catalogued (`FRD-130`). Uses the OpenAI-compatible listing,
    the dialect this catalog is written against (`FRD-123`), not Ollama's native API.
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
    # `or []`, not a `.get` default: while it serves nothing (mid-pull) the endpoint answers
    # `{"data": null}`, and a default only covers an absent key.
    return {_tagged(entry["id"]) for entry in payload.get("data") or [] if entry.get("id")}


def _tagged(model: str) -> str:
    """A model name with its tag made explicit: an absent tag means `:latest`, and the listing
    answers with the explicit form (`all-minilm:latest`)."""
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
    # Either configuration form counts: the single `AIRA_OLLAMA_URL`, or the named-server form
    # `AIRA_OPENAI_SERVERS` the demo stack uses (`FRD-123`).
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
            # Said out loud: the seed is the only place that knows why a model is missing from
            # the catalog and every request for it is refused.
            print(f"[seed] local_models: '{declaration['name']}' is not served here; skipping")
            continue
        with transaction.atomic():
            model, was_created = Model.objects.update_or_create(
                name=declaration["name"], defaults=declaration
            )
            # **Announced, not merely written**: only a catalogued, approved model may be served
            # (`FRD-307`), and the gateway learns of it only through the event. The viewset's own
            # `_payload`, so this seed cannot forget a field.
            emit("model.upserted", _payload(model))
        created += int(was_created)
    return {"local_models": created}
