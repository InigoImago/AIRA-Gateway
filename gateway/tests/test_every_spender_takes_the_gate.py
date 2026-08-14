"""An endpoint that can make a model call takes the controls that stop one.

`pipeline:dryRun` did not. Its own module docstring names *"no budget, no rate limit"* as part of
the defect the file was rewritten for — and the rewrite restored the two controls that are about
**permission** (may this caller act on this use case, is this model released to it) and left the
two that are about **spending**. So a caller over budget, or going too fast, or stopped outright by
IT Security could make the gateway call real models there, as often as they liked: audited and
billed, and refused by nothing.

Found by asking of budgets and rate limits the question that had just been asked of membership —
*which paths does this rule actually reach* — rather than by reading the endpoint again.

**Asserted over every module, not over the one that was wrong.** Naming `pipeline.py` here would be
a guard about a file rather than about a category, and the category is what repeats: the next
endpoint that reaches a provider will be written by somebody who has read neither this file nor
that one. The two ways to take the gate are both accepted, because `prepare_for_dispatch` takes it
on a surface's behalf and demanding the literal call would fail the surfaces for being right.
"""

from __future__ import annotations

import ast
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "src" / "aira_gateway" / "api"

#: How a module gets hold of something that can call a model.
_REACHES_A_MODEL = frozenset({"providers_of", "pipeline_engine"})

#: Either of the two ways to take the controls that do not need to know the model. A surface calls
#: `prepare_for_dispatch`, which takes the gate for it; anything else calls the gate itself.
_TAKES_THE_GATE = frozenset({"guard_before_work", "prepare_for_dispatch"})

#: Modules that reach a provider and deliberately take no gate. Named with the reason, because a
#: silent skip list is how the next spender comes to be exempt by accident.
EXEMPT = {
    # `serving.py` **is** the layer: it defines `guard_before_work` and calls it.
    "serving.py",
    # Lists what is configured and served. Reads `upstream.models()` — an in-process property of an
    # adapter, no request to anybody, nothing spent.
    "providers.py",
    # `FRD-506`'s reachability check. Bounded by role rather than by use case because it describes
    # the *installation*, and it is **never a generation** (`FRD-117`): a self-deployed model must
    # not be woken and billed by the question "does this work". There is no use case to charge and
    # no budget to check.
    "incidents.py",
}


def _modules() -> list[Path]:
    return sorted(p for p in API.rglob("*.py") if p.name != "__init__.py")


def _names(tree: ast.AST) -> set[str]:
    return {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Name | ast.Attribute)
    }


def test_there_are_spenders_to_check() -> None:
    """A guard on the guard: a pattern that matches nothing passes the assertion below by checking
    nothing, and this repository has shipped two guards that could not fail."""
    reaching = {
        path.name for path in _modules() if _names(ast.parse(path.read_text())) & _REACHES_A_MODEL
    }

    assert reaching >= {"serving.py", "pipeline.py"}, sorted(reaching)


def test_everything_that_can_call_a_model_takes_the_early_gate() -> None:
    ungated: list[str] = []
    for path in _modules():
        if path.name in EXEMPT:
            continue
        names = _names(ast.parse(path.read_text()))
        if names & _REACHES_A_MODEL and not names & _TAKES_THE_GATE:
            ungated.append(str(path.relative_to(API)))

    assert not ungated, (
        "these endpoints can cause a model call and take none of the controls that stop one:\n  "
        + "\n  ".join(ungated)
        + "\n\n`guard_before_work` is the bundle — a suspension, the rate limits, and whether the "
        "budget is already exhausted. Call it before anything is spent, or call "
        "`prepare_for_dispatch`, which takes it. If the endpoint genuinely cannot spend, add it to "
        "EXEMPT **with the reason**."
    )
