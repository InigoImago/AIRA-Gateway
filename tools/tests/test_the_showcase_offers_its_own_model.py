"""The command `make showcase` prints names the model the demo actually has.

`showcase_try_it.py` exists for one reason, stated in its own docstring: *"What was missing was one
command that works."* It reads the running system rather than restating it, which is right — and it
took whichever chat model the KIRA catalog listed **first**.

On a machine with cloud credentials configured that is a cloud model. Measured on 2026-08-20,
`make showcase` printed `model_id: 9504` for `gemini-2.5-flash` — a model the demo never seeds,
never releases and never sends a request to. It answered `200` with an **empty** body: 24 output
tokens, every one of them spent thinking. A command that returns nothing is not one that works, and
nothing would have failed to say so.

Asserted on the *selection*, with the network stubbed. What this cannot check is that the printed
command answers, which is why the showcase runs it for real; what it can check is that the demo
offers its own model when it has one, which is the half that broke.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def try_it() -> Any:
    if str(ROOT / "tools") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools"))
    return importlib.import_module("showcase_try_it")


def _answer(monkeypatch: pytest.MonkeyPatch, module: Any, models: list[dict[str, Any]]) -> None:
    """Stand in for the catalog, at the transport rather than at the function under test."""

    class _Response:
        def read(self) -> bytes:
            return json.dumps(models).encode()

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *a, **k: _Response())


CLOUD = {"id": 9504, "name": "gemini-2.5-flash", "capabilities": ["CHAT"]}
LOCAL = {"id": 1004, "name": "qwen3:0.6b", "capabilities": ["CHAT"]}
EMBED = {"id": 9002, "name": "all-minilm", "capabilities": ["EMBED"]}


def test_the_demos_own_model_wins_even_when_it_is_not_first(
    try_it: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer(monkeypatch, try_it, [CLOUD, LOCAL, EMBED])

    assert try_it.chat_model("key") == ("qwen3:0.6b", 1004)


def test_a_catalog_without_it_still_gets_a_working_command(
    try_it: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pull failed, or somebody pointed the showcase at an installation of their own. The first
    chat model is then the best available answer — and printing nothing would be worse."""
    _answer(monkeypatch, try_it, [EMBED, CLOUD])

    assert try_it.chat_model("key") == ("gemini-2.5-flash", 9504)


def test_a_catalog_with_no_chat_model_says_nothing(
    try_it: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`None` rather than a guess: this block prints a runnable command or it prints nothing."""
    _answer(monkeypatch, try_it, [EMBED])

    assert try_it.chat_model("key") is None


def test_it_reads_the_same_variable_the_seed_reads(try_it: Any) -> None:
    """Two names for "the demo's chat model" would drift the first time somebody ran the demo with
    a different one — and the symptom would be a printed command for a model the demo does not
    serve, which is exactly the defect this file is about."""
    sys.path.insert(0, str(ROOT / "tools"))
    seed = importlib.import_module("seed_local_catalog")

    assert try_it.DEMO_CHAT_MODEL == seed.CHAT_MODEL
