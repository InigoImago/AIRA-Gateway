"""Both planes read a pipeline for the models it names, and they have to read all of them.

The question is asked twice, once per plane, and the two answers guard different doors:

- `aira_management.apps.pipelines.serializers._models_named_in` refuses a pipeline that **saves**
  a model the use case was never released (`FRD-308`), at the moment somebody can still fix it;
- `aira_gateway.api.pipeline.models_named_in` refuses one that a **dry run** would call — the one
  endpoint that spends tokens without dispatching a request, and the one whose predecessor let a
  caller name any model at all with no release check, no budget and no audit row.

Neither can be replaced by the other, and each says so: *"Two implementations for one question is
a smell — and the alternative is worse here: this list is a validation concern in Management's own
vocabulary, and the shared library would have to carry the pipeline schema to hold it. Both are
one screenful, both are tested, and **the pair is named in each so neither is edited alone**."*

That last clause was the whole guard, and `LESSONS.md` §1 has a name for it: *a paragraph
explaining why a copy is dangerous is evidence that the copy needs a test, not a substitute for
one.* Nothing compared them. A fourth place a model can be written, added to one side only, is a
governance hole in whichever direction it was forgotten — Management saving a pipeline the gateway
will refuse at dispatch, or a dry run calling a model the use case may not.

So they are compared here, in both of the ways they can drift: what they **answer** for a document
that names a model everywhere one can be named, and which config keys they **look at** — the second
being the half that can see a site only one of them has learned.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for path in (
    ROOT / "gateway" / "src",
    ROOT / "libs" / "src",
    ROOT / "management" / "backend" / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from aira_management.apps.pipelines.serializers import _models_named_in  # noqa: E402

from aira_gateway.api.pipeline import models_named_in  # noqa: E402

GATEWAY_SOURCE = ROOT / "gateway" / "src" / "aira_gateway" / "api" / "pipeline.py"
MANAGEMENT_SOURCE = (
    ROOT
    / "management"
    / "backend"
    / "src"
    / "aira_management"
    / "apps"
    / "pipelines"
    / "serializers.py"
)

#: A pipeline naming a model in **every** place one can be named, so a reader who adds a fourth
#: place has somewhere obvious to extend — and so the agreement below is about a full document
#: rather than about the easy case.
EVERYWHERE = {
    "steps": [
        {"type": "injection_filter", "config": {"mode": "llm", "model": "filter-model"}},
        {"type": "pii_filter", "config": {"model": "redactor-model"}},
        {
            "type": "model_route",
            "config": {
                "model": "router-model",
                "default_model": "default-model",
                "categories": [
                    {"name": "code", "model": "code-model"},
                    {"name": "prose"},
                ],
            },
        },
    ],
    "fallback_models": ["fallback-a", "fallback-b"],
}

#: The shapes that arrive from a hand-written body on the dry run, where the `pipeline` field is
#: an unvalidated object by design. Both sides have to survive them and agree about them.
AWKWARD: list[dict] = [
    {},
    {"steps": []},
    {"steps": [{"type": "injection_filter"}]},
    {"steps": [{"type": "injection_filter", "config": {}}]},
    {"steps": [{"type": "model_route", "config": {"categories": [None, "text", 7]}}]},
    {"steps": [{"type": "model_route", "config": {"model": "", "default_model": None}}]},
    {"fallback_models": ["a", "", None, "a"]},
    {"steps": [{"type": "x", "config": {"model": "dup"}}], "fallback_models": ["dup"]},
]


def _agree(document: dict) -> None:
    gateway = models_named_in(document)
    management = _models_named_in(
        document.get("steps") or [], document.get("fallback_models") or []
    )
    assert gateway == management, (
        f"the two planes read a different set of models out of {document!r}\n"
        f"  gateway:    {gateway}\n"
        f"  management: {management}\n"
        "One of them would let a model through the door the other closes."
    )


def test_they_agree_about_a_pipeline_that_names_a_model_everywhere_it_can() -> None:
    _agree(EVERYWHERE)
    # And the answer is not empty, or the comparison above would hold for two broken readers.
    assert set(models_named_in(EVERYWHERE)) == {
        "filter-model",
        "redactor-model",
        "router-model",
        "default-model",
        "code-model",
        "fallback-a",
        "fallback-b",
    }


@pytest.mark.parametrize("document", AWKWARD, ids=range(len(AWKWARD)))
def test_they_agree_about_the_shapes_a_hand_written_body_can_carry(document: dict) -> None:
    _agree(document)


def _config_keys(source: pathlib.Path, function: str) -> set[str]:
    """Every string literal the function names, other than its own docstring.

    Read from the source rather than exercised, because this is the half that has to notice a site
    **neither** test document uses: a value comparison cannot see a key that one implementation has
    learned and the other has not, and that is exactly the drift the pair's own docstrings warn
    about.

    Every literal, not only the arguments to `.get(...)` — the first version scanned those and
    reported a difference that was not one, because Management writes
    `for key in ("model", "default_model")` where the gateway writes two calls. A scan keyed on
    *how* a key is reached is a scan that answers about the spelling of a loop; the keys are the
    question.
    """
    tree = ast.parse(source.read_text(), filename=str(source))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != function:
            continue
        body = node.body[1:] if ast.get_docstring(node) is not None else node.body
        return {
            inner.value
            for statement in body
            for inner in ast.walk(statement)
            if isinstance(inner, ast.Constant) and isinstance(inner.value, str)
        }
    raise AssertionError(
        f"{function} is no longer a function in {source} — move this check with it"
    )


def test_they_look_at_the_same_places_in_a_step() -> None:
    """The structural half. `steps` and `fallback_models` are parameters on Management's side and
    read off the document on the gateway's, so those two are excluded by name and everything else
    has to match — a fourth key learned by one side fails here."""
    outer = {"steps", "fallback_models"}
    gateway = _config_keys(GATEWAY_SOURCE, "models_named_in") - outer
    management = _config_keys(MANAGEMENT_SOURCE, "_models_named_in") - outer

    assert gateway, "the scan found no keys at all, which would make this pass by finding nothing"
    assert gateway == management, (
        "the two planes look in different places for a model a pipeline names:\n"
        f"  only the gateway reads:    {sorted(gateway - management)}\n"
        f"  only Management reads:     {sorted(management - gateway)}\n"
        "Whichever side is missing one is the door a model can be named through unchecked."
    )
