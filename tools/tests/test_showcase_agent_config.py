"""The assistant's model menu is derived from what the gateway serves, not written down.

`tools/showcase_agent.py` used to put exactly one model in the OpenCode config — taken from an
environment variable — under a comment saying it listed *"only the one the demo actually serves"*.
The rule in the comment is the right rule. The code did not have it: the two coincided, so nothing
looked wrong until somebody released a second model to the use case, saw one entry in the menu and
had to ask whether that was intended.

A comment claiming a rule the code does not implement is this repository's most repeated defect,
and it is invisible for exactly as long as the constant happens to be correct.

**Both halves of the rule, because neither alone is the answer.** The catalog says whether a model
can return a function call; an entry without that is a model the whole assistant loop breaks on,
refused by name on the first turn. The *release* says whether this use case may call it at all
(`FRD-308`) — and that is **not in the model listing**, which describes the gateway rather than the
caller. So the listing decides the first half and a real request decides the second.

Hermetic: `config()` is handed a listing and returns a document. What it does with the network —
asking, and verifying by calling — is exercised by the showcase itself, where it was measured
producing two entries when a second model was made tool-capable and released, and one again when
that was undone.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _agent() -> Any:
    if str(ROOT / "tools") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools"))
    assert importlib.util.find_spec("showcase_agent"), "tools/showcase_agent.py is not importable"
    return importlib.import_module("showcase_agent")


def _entry(name: str, capabilities: list[str] | None, *, max_output: int | None = 40960) -> dict:
    return {
        "name": f"models/{name}",
        "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
        "airaCapabilities": capabilities,
        "airaMaxOutputTokens": max_output,
    }


def test_every_model_it_is_given_reaches_the_menu() -> None:
    """The property that was missing. Two usable models produce two entries — the constant this
    replaced produced one whatever it was handed."""
    document = _agent().config(
        [_entry("alpha", ["generate", "tools"]), _entry("beta", ["generate", "tools"])], "k"
    )

    assert set(document["provider"]["aira"]["models"]) == {"alpha", "beta"}


def test_the_default_model_is_one_of_the_offered_ones() -> None:
    """OpenCode selects `model` on start. Naming one that is not in the menu is a client that
    cannot begin."""
    document = _agent().config([_entry("alpha", ["generate", "tools"])], "k")

    assert document["model"] == "aira/alpha"
    assert document["model"].removeprefix("aira/") in document["provider"]["aira"]["models"]


def test_every_entry_declares_it_can_call_tools() -> None:
    """This provider exists for an assistant. An entry the client would not use for a tool turn is
    an entry with no reason to be there."""
    document = _agent().config([_entry("alpha", ["generate", "tools"])], "k")

    assert document["provider"]["aira"]["models"]["alpha"]["tool_call"] is True


def test_the_limits_come_from_the_catalog_rather_than_from_here() -> None:
    """They were hard-coded `32768` and `4096`. The second was the invented output cap that refused
    an agentic client's first request, and writing it here as well would have put the same guess in
    a second place — where correcting the catalog would not have reached it."""
    document = _agent().config([_entry("alpha", ["generate", "tools"], max_output=12345)], "k")

    assert document["provider"]["aira"]["models"]["alpha"]["limit"] == {
        "context": 12345,
        "output": 12345,
    }


def test_a_model_that_declares_no_cap_gets_no_invented_one() -> None:
    """Absence of information is not permission, and it is not a number either (`FRD-114` FR-7).
    A client sizing a request from a figure this file made up would be sized against nothing."""
    document = _agent().config([_entry("alpha", ["generate", "tools"], max_output=None)], "k")

    assert document["provider"]["aira"]["models"]["alpha"]["limit"] == {}


def test_the_key_reaches_the_config_and_the_base_url_is_the_gateway() -> None:
    """The guard's own footing: a document assembled without these is one nothing can authenticate
    with, and every assertion above would still pass."""
    document = _agent().config([_entry("alpha", ["generate", "tools"])], "the-key")

    options = document["provider"]["aira"]["options"]
    assert options["apiKey"] == "the-key"
    assert options["baseURL"].endswith("/v1beta")


@pytest.mark.parametrize(
    ("capabilities", "why"),
    [
        pytest.param(["generate"], "generation without tool calling", id="no-tools"),
        pytest.param(["embed"], "an embedding model", id="embed-only"),
        pytest.param([], "a model that declares nothing", id="nothing"),
        pytest.param(None, "a model the catalog has never described", id="undeclared"),
    ],
)
def test_a_model_that_cannot_do_tools_is_not_offered(capabilities: list | None, why: str) -> None:
    """`offers_tool_calling` is the filter, and it is asserted **without a socket** — which is the
    whole reason it is its own function.

    This first called `usable()`, which also verifies by making a request. With no server in a unit
    test that verification fails anyway, so the assertion held with the tools check *deleted*:
    `False` for the wrong reason reads exactly like `False` for the right one. Proved by removing
    the check and watching all eleven tests stay green.
    """
    entry = _entry("alpha", capabilities)

    assert not _agent().offers_tool_calling(entry), f"{why} was offered to an assistant"


def test_a_model_that_can_do_tools_and_generate_passes_the_declaration_half() -> None:
    """The control for the four cases above. Without it they could all pass against a predicate
    that answers `False` to everything — which is what a filter looks like when it is broken in the
    other direction."""
    assert _agent().offers_tool_calling(_entry("alpha", ["generate", "tools"]))


def test_a_model_that_cannot_generate_is_not_offered() -> None:
    """Declaring `tools` is not enough on its own: a client's turn is a generation, so a model that
    does not serve `generateContent` would fail on use however it is described."""
    entry = _entry("alpha", ["generate", "tools"])
    entry["supportedGenerationMethods"] = ["embedContent"]

    assert not _agent().offers_tool_calling(entry)
