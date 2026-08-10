"""Every adapter family measures its region against the policy, or it is a hole in one.

`FRD-115` says residency is **enforced, not intended**: a model in a region this deployment does
not permit is a startup failure. Three of the four families did that — Vertex, the OpenAI servers,
Foundry — and the Google AI Studio one did not. It declared `global` on every model, honestly, so
the region reached the audit row; nothing ever compared it to `AIRA_ALLOWED_REGIONS`.

**An enforced control that one path bypasses is worse than one that is missing everywhere**,
because the evidence then says the deployment is compliant. The same shape as `:embedContent`
skipping the pre-dispatch gate, and as the KIRA surface's inverted membership check: a rule stated
once and applied in all but one place.

Structural rather than case-by-case, for the reason the route guard in
`test_every_route_is_guarded.py` gives: the next adapter will be written by copying a neighbour,
and whichever neighbour is copied decides whether the rule comes with it.
"""

from __future__ import annotations

import ast
import pathlib

UPSTREAMS = pathlib.Path(__file__).resolve().parents[1] / "src" / "aira_gateway" / "upstreams"

#: Modules that build no adapter: the shared base, the canonical mappings, the mock, the dialects.
#: A dialect is a *shape*, not a place — it has no region to check, and requiring one of it would
#: be the rule applied where it means nothing.
NOT_A_BUILDER = {"base.py", "mock.py", "__init__.py"}


def _builders() -> dict[pathlib.Path, list[str]]:
    """Every `build_*_upstream(s)` function in the upstream layer, by file."""
    found: dict[pathlib.Path, list[str]] = {}
    for path in UPSTREAMS.rglob("*.py"):
        if path.name in NOT_A_BUILDER and path.parent == UPSTREAMS:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        # `build_*_upstream(s)` and nothing else. The first run of this guard flagged
        # `build_token_source`, which builds a **credential** — it has no region, and demanding one
        # of it would be the rule applied where it means nothing. A guard that fires on the wrong
        # thing gets narrowed once and then believed; one that is switched off gets removed.
        names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("build_")
            and node.name.endswith(("_upstream", "_upstreams"))
        ]
        if names:
            found[path] = names
    return found


def test_every_adapter_builder_checks_the_region_it_will_serve_from() -> None:
    unchecked = [
        f"{path.relative_to(UPSTREAMS)}: {', '.join(names)}"
        for path, names in _builders().items()
        if "check_region" not in path.read_text()
    ]

    assert not unchecked, (
        f"{unchecked} build an upstream without measuring its region against "
        "AIRA_ALLOWED_REGIONS. A deployment that claims EU residency and quietly leaves it is the "
        "failure FRD-115 exists to prevent — and the audit row will say it was compliant."
    )
