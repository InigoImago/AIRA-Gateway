"""The two seeds that declare the local models state the same measurements.

There are two of them because they write to two different places. `local_models.py` is a Management
seed contribution — Django rows, prices as `Decimal`, distributed to the gateway over Kafka.
`tools/seed_local_catalog.py` writes the gateway's read-model directly with SQL, prices as integer
nano-units, for a walk-through that does not want the whole event path. Both are legitimate; the
shapes genuinely differ.

What does **not** differ is the evidence. `THINKING_BY_MODEL` and `TOOLS_BY_MODEL` record what was
observed by sending requests to a running model on 2026-08-08, and a measurement has one value.

**This pair has drifted twice, and both times the fix was to copy the correction across by hand.**

- `minimal` was declared as a thinking mode for a server that refuses it *by name*. Corrected in
  the Management seed on 2026-08-06 and left wrong in `tools/` until 2026-08-08.
- `tools` was measured and added to `tools/seed_local_catalog.py`, and **not** to the Management
  one. The consequence was silent and total: `make showcase` declared the chat model without the
  capability, so a coding assistant pointed at the demo was refused by name and every explanation
  pointed at the client. The whole `FRD-131` feature was unreachable from the demo that exists to
  show it.

Neither file can import the other — the Management image copies `libs/` and `management/backend/`
and not `tools/`, and this data is local-Ollama trivia that has no business in a library both
production planes import. So the answer is the one this repository has arrived at five times now
(`aira.rate-limits`, `aira.anomaly-rules`, `use_case_group.granted`, the capability vocabulary, the
realm roles): compare the two statements **in both directions**, and fail on the first divergence.

Both directions matter and they fail differently. A measurement in `tools/` and not in Management
is the `tools` case above — a demo that refuses the thing it demonstrates. A measurement in
Management and not in `tools/` is the mirror: the gateway's read-model, seeded by hand for a live
walk-through, describes a model the console says something else about.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest

from aira_common.money import to_nanos

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def seeds() -> tuple[Any, Any]:
    """The two modules, imported rather than parsed.

    `local_models` reaches Django models at import, which the suite has configured already; the
    `tools/` script guards its own work behind ``__main__``, so importing it runs nothing.
    """
    if str(ROOT / "tools") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools"))
    management = importlib.import_module("aira_management.apps.seed.contributions.local_models")
    walkthrough = importlib.import_module("seed_local_catalog")
    return management, walkthrough


def _declaration(rows: list[dict[str, Any]], key: str, name: str) -> dict[str, Any]:
    """The row for ``name``, whichever of the two spellings of "which model" the file uses."""
    matching = [row for row in rows if row[key] == name]
    assert len(matching) == 1, f"expected exactly one declaration of {name!r}, got {len(matching)}"
    return matching[0]


def test_both_seeds_declare_the_same_models(seeds: tuple[Any, Any]) -> None:
    """The guard's own footing. Every assertion below looks up rows by model name, so two files
    naming different models would make them compare nothing and pass."""
    management, walkthrough = seeds

    assert management.CHAT_MODEL == walkthrough.CHAT_MODEL
    assert management.EMBED_MODEL == walkthrough.EMBED_MODEL
    assert {row["name"] for row in management._declarations()} == {
        row["model"] for row in walkthrough.DECLARATIONS
    }


def test_the_measured_thinking_modes_are_one_measurement(seeds: tuple[Any, Any]) -> None:
    """A model's thinking modes are what a run showed, so the two tables are the same table.

    The failure this prevents is not an error anywhere. A mode declared that the server refuses by
    name produces a 400 the caller cannot map back to anything they sent; a mode *not* declared
    that the model has produces an answer that is quietly worse. Both are silent, and both were
    shipped from exactly this pair of files.
    """
    management, walkthrough = seeds

    assert management.THINKING_BY_MODEL == walkthrough.THINKING_BY_MODEL, (
        "the two seeds disagree about which thinking modes were measured; "
        f"Management has {sorted(management.THINKING_BY_MODEL)}, "
        f"tools/ has {sorted(walkthrough.THINKING_BY_MODEL)}"
    )


def test_the_models_seen_to_call_tools_are_one_measurement(seeds: tuple[Any, Any]) -> None:
    """`TOOLS_BY_MODEL` holds what was *seen*, never what a vendor's flag claims — `ollama show`
    lists `tools` for `qwen2.5-coder:7b`, which returns the JSON as prose. A list of evidence is
    the one kind of list that cannot be reconstructed from the other file."""
    management, walkthrough = seeds

    assert management.TOOLS_BY_MODEL == walkthrough.TOOLS_BY_MODEL, (
        "only one of the two seeds knows which models were seen to emit a real tool call; "
        f"Management-only: {sorted(management.TOOLS_BY_MODEL - walkthrough.TOOLS_BY_MODEL)}, "
        f"tools/-only: {sorted(walkthrough.TOOLS_BY_MODEL - management.TOOLS_BY_MODEL)}"
    )


@pytest.mark.parametrize("model", ["chat", "embed"])
def test_the_two_seeds_price_and_bound_each_model_identically(
    seeds: tuple[Any, Any], model: str
) -> None:
    """Everything else the two files say about the same model.

    The prices are the interesting pair: Management states them as `Decimal` currency per million
    tokens and `tools/` as integer nano-units, so they cannot be compared by reading — which is
    precisely the kind of difference that survives a review. `to_nanos` is the conversion the
    product itself uses (`FRD-403`: money is never a float), so the comparison is the one the
    running system would make.

    The KIRA numeric id is here for a sharper reason than tidiness: it names a *role* — "the local
    chat model" — and a caller's configuration holds the number. Two files disagreeing about it
    means a request naming the id reaches a different model depending on which seed last ran.
    """
    management, walkthrough = seeds

    name = management.CHAT_MODEL if model == "chat" else management.EMBED_MODEL
    theirs = _declaration(management._declarations(), "name", name)
    ours = _declaration(walkthrough.DECLARATIONS, "model", name)

    assert to_nanos(theirs["input_price_per_million"]) == ours["input_price_per_million_nanos"]
    assert to_nanos(theirs["output_price_per_million"]) == ours["output_price_per_million_nanos"]
    assert theirs["numeric_id"] == ours["numeric_id"]
    for field in ("publisher", "platform", "hosting", "approved"):
        assert theirs[field] == ours[field], f"{name}: the two seeds disagree about {field}"
    for field in ("max_output_tokens", "default_max_output_tokens", "embedding"):
        assert theirs.get(field) == ours.get(field), f"{name}: the two seeds disagree about {field}"
