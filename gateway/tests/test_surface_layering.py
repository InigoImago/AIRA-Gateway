"""A surface parses; the layer decides (FRD-126).

`api/serving.py` has always said in its docstring that a surface owns "parsing its own wire format,
rendering its own error envelope, and its own routes" and nothing else. It shared the *steps* to
make that true — and both surfaces went on writing the *order* out by hand, six calls each, and in
the KIRA surface spread across four functions.

Order is where every guarantee of this layer lives:

    rate limit before the pipeline   or a refused request pays for a classifier call
    declaration after routing        or a cap is checked against a model that never serves it
    thinking after routing           or a budget is validated against the wrong model
    reservation last                 or it is made against the model the caller *named*

So the docstring is now a test. Same shape as the architecture assertion in `test_vertex.py`, and
for the same reason: a rule that only a reviewer enforces is a rule the third surface breaks.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SURFACES = sorted(
    (Path(__file__).resolve().parents[1] / "src" / "aira_gateway" / "api").glob("*/routes.py")
)

#: The steps of the pre-dispatch sequence. A surface calling one of these directly is a surface
#: assembling the order itself, which is the thing this file exists to prevent.
SEQUENCE_STEPS = frozenset(
    {
        "guard_before_work",
        "run_pipeline",
        "check_declaration",
        "enforce_pre_dispatch",
        "resolve_thinking",
        "validate_embedding",
        "check_not_empty",
    }
)


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_there_are_surfaces_to_check() -> None:
    """A guard on the guard: if the glob stops matching, every assertion below passes vacuously."""
    assert len(SURFACES) >= 2, [str(p) for p in SURFACES]


@pytest.mark.parametrize("surface", SURFACES, ids=lambda p: p.parent.name)
def test_a_surface_does_not_assemble_the_pre_dispatch_order(surface: Path) -> None:
    """It calls `prepare_for_dispatch`, which owns the order, or it calls nothing of the sort."""
    assembled = sorted(_called_names(surface) & SEQUENCE_STEPS)

    assert not assembled, (
        f"{surface.parent.name} calls {assembled} directly. Those are steps of one sequence whose "
        "*order* is the guarantee — use `prepare_for_dispatch`. Assembling it here is how this "
        "surface came to have no rate limiting at all when the take moved one function over."
    )


@pytest.mark.parametrize("surface", SURFACES, ids=lambda p: p.parent.name)
def test_a_surface_that_dispatches_prepares_through_the_shared_sequence(surface: Path) -> None:
    """The other half. A surface passing the test above by doing *nothing* would be no surface."""
    called = _called_names(surface)
    if "dispatch_with_fallback" not in called:
        pytest.skip(f"{surface.parent.name} dispatches nothing")

    assert "prepare_for_dispatch" in called
