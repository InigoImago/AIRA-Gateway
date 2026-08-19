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


# == and a surface never loses evidence quietly ==================================================


def test_no_surface_swallows_a_failed_audit_write() -> None:
    """Both surfaces refuse to let the audit fail a correctly-refused request. Only one said so.

    `FRD-122` FR-7 is right: turning a 429 into a 500 misinforms the caller about what happened and
    invites the retry storm the limit exists to prevent. So the write is shielded — and the shield
    is where the evidence goes missing. The Gemini surface logs `audit_refusal_not_recorded` and
    has since the shield was written; the KIRA surface used `contextlib.suppress(Exception)`, which
    keeps FR-7 and drops the row with no row, no log line, and nothing for anybody reviewing the
    audit to notice. *A control with no trace cannot be reviewed* is this project's own phrase for
    this exact path, from the mutation that guards it.

    Two surfaces, one governance question, two answers — and the next surface would have copied
    whichever it read first. Asserted structurally rather than by behaviour because that is what
    makes it true of a surface nobody has written yet.
    """
    offenders: list[str] = []
    for surface in SURFACES:
        source = surface.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.With | ast.AsyncWith):
                continue
            for item in node.items:
                call = item.context_expr
                if not isinstance(call, ast.Call):
                    continue
                name = ast.unparse(call.func)
                if not name.endswith("suppress"):
                    continue
                # `suppress(CancelledError)` is shutdown, not evidence. What this forbids is a
                # blanket suppression around a recording call.
                caught = {ast.unparse(arg) for arg in call.args}
                if not caught & {"Exception", "BaseException"}:
                    continue
                body = ast.unparse(node)
                if "_record" in body or "record_request" in body or "audit" in body.lower():
                    offenders.append(f"{surface.parent.name}:{node.lineno}")

    assert not offenders, (
        "These swallow a failed audit write without saying so: " + ", ".join(offenders) + ".\n"
        "Shielding the request is right (`FRD-122` FR-7); losing the row in silence is not. "
        "Catch it and log `audit_refusal_not_recorded`, as both surfaces now do."
    )


def test_every_surface_reports_a_lost_audit_row_under_the_same_name() -> None:
    """One event name, or the search that looks for missing rows finds one surface's worth."""
    for surface in SURFACES:
        source = surface.read_text()
        if "_record" not in source and "record_request" not in source:
            continue
        assert "audit_refusal_not_recorded" in source, (
            f"{surface.parent.name} records refusals but has no way to report failing to. "
            "An operator searching for lost evidence would see the other surface only."
        )


# == and no surface lets a caller's text become a server error ====================================


def test_every_surface_refuses_a_body_it_cannot_encode() -> None:
    """A lone surrogate in the request text was a **500**, on both surfaces, with no audit row.

    JSON may escape half a surrogate pair — `"\\ud800"` parses into a Python string that no UTF-8
    encoder accepts. Nothing noticed until httpx built the upstream request, nine steps later: by
    then the rate limit was spent, the budget reserved and the pipeline run, and the recording
    sites did not fire because they cover a request that *reached* an upstream. Six characters
    bought a server error and left no trace.

    Asserted structurally, like the rest of this file, because the point is the surface that has
    not been written yet. `ensure_body_is_encodable` belongs to the shared layer; calling it is
    the surface's own parsing step, which is why it is a call site rather than part of
    `prepare_for_dispatch` — that runs after the controls this has to precede.
    """
    missing = [
        surface.parent.name
        for surface in SURFACES
        if "ensure_body_is_encodable" not in surface.read_text()
    ]

    assert not missing, (
        f"these surfaces never check that the body can be encoded: {missing}. "
        "A caller's text must not become a 500 — and must not spend a budget on the way."
    )
