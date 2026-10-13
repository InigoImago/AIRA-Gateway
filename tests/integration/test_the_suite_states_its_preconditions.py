"""What the live suite needs from a stack, stated where a reader and a parser can see it.

Hermetic, so deliberately without the `integration` mark: it reads this directory's source and runs
in `make test`, before any stack is started.
"""

from __future__ import annotations

import ast
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_every_tool_model_test_states_that_it_needs_one() -> None:
    """A test that reaches for `TOOL_MODEL` without the `tool_model` fixture fails on a stack that
    does not serve it — with a 404 that reads as a broken gateway — instead of skipping before it
    acts.

    Every file of the layer, not the one that defines the model: a copy of the constant in another
    file, with its own inline skip, escapes the check and skips on every stack that lacks the model.
    """
    offenders = []
    for path in sorted(HERE.glob("test_*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "TOOL_MODEL"
                for target in node.targets
            ):
                offenders.append(f"{path.name}: defines its own TOOL_MODEL")
            if not isinstance(node, ast.AsyncFunctionDef) or not node.name.startswith("test"):
                continue
            uses = any(
                isinstance(inner, ast.Name) and inner.id == "TOOL_MODEL" for inner in ast.walk(node)
            )
            if uses and "tool_model" not in {argument.arg for argument in node.args.args}:
                offenders.append(f"{path.name}: {node.name}")

    assert not offenders, (
        f"these ask for TOOL_MODEL without requesting the `tool_model` fixture: {offenders}"
    )
