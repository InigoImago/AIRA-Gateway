"""The console can declare every capability the gateway enforces, and no others.

`Capability` is a **closed vocabulary** (`ADR-0012`): flags say *whether*, never *how*, and
undeclared means unsupported. The Angular console restates that vocabulary as a TypeScript array,
because a checkbox list has to come from somewhere — and a restatement in a second language is the
shape this codebase keeps getting caught by. `FRD-206` (the console answering questions only the
server can answer), `FRD-602` (a second entry point that forgot the scope rule) and `ADR-0015` (a
membership rule written twice, so one copy was inverted) are all the same defect.

Here it had already happened twice without anybody noticing, and it is **silent in the worst
direction**: `tools` shipped with `FRD-131` and `prompt_caching` with `FRD-133`, and neither was
in the console's list. A Global Administrator could price a model for prompt caching and had no
way to declare that it caches — the capability with no way in, which announces itself through
nothing at all, because absence of a checkbox looks exactly like a design decision.

So the two lists are compared **in both directions**, the same answer this repository has reached
three times before (`aira.rate-limits`, `aira.anomaly-rules`, `use_case_group.granted`): a value the
gateway enforces that the console cannot set is a feature nobody can reach, and a value the console
offers that the gateway does not know is a promise nothing keeps.
"""

from __future__ import annotations

import pathlib
import re

from aira_common.models import Capability

MODELS_TS = (
    pathlib.Path(__file__).resolve().parents[2]
    / "management"
    / "frontend"
    / "src"
    / "app"
    / "core"
    / "api"
    / "models.ts"
)


def _console_capabilities() -> set[str]:
    """The values in `CAPABILITIES` in the SPA's model definitions.

    Parsed rather than imported, for the reason `test_declared_dependencies` gives: no Python
    environment can run TypeScript, and a test that needs one is a test that gets skipped.
    """
    source = MODELS_TS.read_text()
    match = re.search(r"export const CAPABILITIES[^=]*=\s*\[(.*?)\]", source, re.DOTALL)
    assert match, "CAPABILITIES is no longer an array literal in models.ts"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_console_can_declare_every_capability_the_gateway_enforces() -> None:
    missing = {member.value for member in Capability} - _console_capabilities()

    assert not missing, (
        f"the gateway enforces {sorted(missing)} and the console offers no way to declare it — a "
        "model can be priced for a capability it can never be given, and nothing anywhere fails"
    )


def test_the_console_offers_nothing_the_gateway_does_not_know() -> None:
    """The other direction, which fails differently: a checkbox the gateway ignores is a
    declaration an administrator believes they made."""
    unknown = _console_capabilities() - {member.value for member in Capability}

    assert not unknown, f"the console offers {sorted(unknown)}, which no model can be judged by"
