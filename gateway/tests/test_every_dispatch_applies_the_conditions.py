"""Every verb that reaches a model applies the conditions a candidate has to meet.

**Why this is structural rather than a list of behaviour tests.** `ADR-0012` §3 says a chain must
not degrade a request silently, and `requirements.py` implements that as a set of conditions —
residency (`FRD-115`), approval (`FRD-307`), release (`FRD-308`), media types (`FRD-110`),
thinking, schemas, tools, sampling. Every one of them was applied in exactly one place:
`dispatch_with_fallback`. So a verb that does not use the chain applied **none of them**, and two
verbs do not use the chain:

    :streamGenerateContent   the chain returns a finished response, so a stream cannot use it
    :embedContent            there is nothing to fall back *to*, so nobody wrote one

Measured on 2026-08-11 against the hermetic app — each refused by name on `:generateContent`,
each **served with a 200** on both of the others:

    a model no Global Administrator approved (`FRD-307`)
    a model the use case was never released (`FRD-308`)

That is the `:embedContent` bypass of `FRD-405` for the third time, and the third time is what
makes it a class rather than a mistake. The behaviour is fixed in `serving.resolve_direct_target`;
this file is what stops a *fourth* verb being written without the question being asked.

**The guard that existed asked the wrong question about the same line.**
`test_every_model_call_is_accounted.py` already parses these call sites and already names
`api/gemini/routes.py:stream_generate` — and its justification vouches for attribution, billing
and recording, all of which were true. Nobody had asked whether the candidate was *allowed*. A
list is only as good as the property it is a list about, which is the reason this is a second file
and not a second sentence in that one.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "aira_gateway"

#: The methods that reach a model on a caller's behalf. Identical to
#: `test_every_model_call_is_accounted.BILLABLE`, and deliberately *not* imported from it: these
#: are two different claims about the same set, and coupling them would let a future narrowing of
#: one silently narrow the other.
DISPATCHING = frozenset({"generate", "stream_generate", "embed"})

#: The functions that apply the conditions. A dispatching call site has to be in the same function
#: as one of these, or on the list below with a reason.
#:
#: - `dispatch_with_fallback` asks `permits` per hop, which is how a chain refuses to substitute
#:   a candidate that would answer differently.
#: - `resolve_direct_target` asks it once, for the verbs that have no chain, and returns the
#:   adapter — so the check cannot be separated from getting something to call.
GATES = frozenset({"dispatch_with_fallback", "resolve_direct_target"})

#: Dispatching calls that legitimately apply no condition, and why.
#:
#: An entry is `"<module path>:<function>:<method>"`. The value is the justification — not
#: decoration: a call that reaches a model with no condition asked and no sentence explaining it
#: is the defect, not the list.
UNCONDITIONED: dict[str, str] = {
    "pipeline/classifiers.py:classify_text:generate": (
        "The pipeline's own classifier — the injection filter and the router. It is not the "
        "caller's request: it is the gateway deciding *about* that request, on a model an "
        "administrator named in the pipeline configuration, and the conditions are about what may "
        "serve the caller. It is still audited and billed as `pipeline:<step>` with `requests=0` "
        "(`FRD-125b`), which is the accountability question, and the model it may use is bounded "
        "by the release the pipeline serializer validates against (`FRD-308`)."
    ),
    "pipeline/classifiers.py:rewrite:generate": (
        "The pipeline's redactor (`FRD-309`), and the same argument as the classifier above: it is "
        "the gateway acting *on* the caller's request rather than serving it, on a model an "
        "administrator named in the pipeline configuration. It is audited and billed as "
        "`pipeline:pii_filter` with `requests=0` (`FRD-125b`), and the model it may use is bounded "
        "by the release, which the pipeline serializer validates every named model against "
        "(`FRD-308`) — `_models_named_in` reads `config.model`, so this step needed no separate "
        "rule and gets no separate hole."
    ),
    "api/incidents.py:_accepts:generate": (
        "The console's *Ask the model* button (`ADR-0021`), and it is a question **about the "
        "installation** rather than a request served for anybody: it names no use case, so there "
        "is no release, no residency claim and no budget that could apply to it. What it asks is "
        "precisely whether the provider accepts a word, which is the one thing the conditions "
        "cannot answer — a level is free text because no list here survives the vendors' next "
        "release. Gated to the two roles that may investigate an installation, bounded to "
        "`MAX_LEVELS_PER_CHECK` words, and capped at one output token per word: a word the model "
        "refuses is free, because the refusal comes before any generation."
    ),
    "pipeline/dispatch.py:dispatch_with_fallback:generate": (
        "The chain itself, which is where `permits` is asked. Named here because it is the "
        "definition rather than an exemption — the call is one line below the check."
    ),
}


Function = ast.FunctionDef | ast.AsyncFunctionDef


def _scopes(path: Path) -> list[tuple[str, list[Function]]]:
    """Every function in the file, paired with the chain of functions enclosing it.

    The chain is what makes this checkable at all. A dispatching call and the gate that permits it
    are **not** always in the same function: a stream's `provider.stream_generate(...)` lives in a
    `generate_chunks` closure while the gate runs one scope out, in `_stream_response`. Attributing
    a call to its innermost function and then looking outwards is the honest reading of "this call
    is inside something that asked" — and a walk that stopped at module level would have missed
    the closure entirely, reporting the hole's own location as clean.
    """
    scopes: list[tuple[str, list[Function]]] = []

    def visit(node: ast.AST, enclosing: list[Function]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, Function):
                chain = [child, *enclosing]
                scopes.append((child.name, chain))
                visit(child, chain)
            else:
                visit(child, enclosing)

    visit(ast.parse(path.read_text()), [])
    return scopes


def _own_calls(function: Function) -> list[ast.Call]:
    """The calls this function makes **itself** — not the ones its nested functions make.

    Without this the same physical call is reported twice, once per enclosing scope, and the two
    entries disagree about which function it is in.
    """
    calls: list[ast.Call] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, Function):
                continue
            if isinstance(child, ast.Call):
                calls.append(child)
            visit(child)

    visit(function)
    return calls


def _called_names(function: Function) -> set[str]:
    return {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in _own_calls(function)
        if isinstance(node.func, ast.Name | ast.Attribute)
    }


def _dispatch_sites() -> dict[str, list[Function]]:
    """Every dispatching call outside the adapters, as `<module>:<function>:<method>` → the chain
    of functions it sits inside.

    `upstreams/` is excluded because that **is** the adapter layer: a mapper calling its own
    transport is not a decision about which model may serve somebody.
    """
    found: dict[str, list[Function]] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE).as_posix()
        if relative.startswith("upstreams/"):
            continue
        for name, chain in _scopes(path):
            for node in _own_calls(chain[0]):
                if isinstance(node.func, ast.Attribute) and node.func.attr in DISPATCHING:
                    found[f"{relative}:{name}:{node.func.attr}"] = chain
    return found


def _gated(chain: list[Function]) -> bool:
    """Whether this call, or any scope enclosing it, asks the conditions.

    Scoped to the function chain rather than to the module: a module that gates one verb and not
    another is exactly the state this file was written about, and a module-level search would have
    called it clean.
    """
    return any(_called_names(function) & GATES for function in chain)


def test_there_are_dispatch_sites_to_check() -> None:
    """The guard's own failure mode, and this project has shipped two guards that could not fail —
    both silently green. A parser that finds nothing passes every assertion below."""
    sites = _dispatch_sites()

    assert len(sites) >= 4, f"the parser found only {sorted(sites)}"
    # And it must reach inside a closure, which is where the defect that prompted this file lived.
    assert any("generate_chunks" in site for site in sites), (
        "no nested function was found; the streamed dispatch lives in a closure, so a walk that "
        "misses those would report the hole's own location as clean"
    )


def test_every_dispatching_call_asks_the_conditions() -> None:
    sites = _dispatch_sites()
    ungated = sorted(
        site for site, chain in sites.items() if site not in UNCONDITIONED and not _gated(chain)
    )

    assert not ungated, (
        "these places reach a model without asking whether the candidate may serve the request:\n"
        "  " + "\n  ".join(ungated) + "\n\nResidency, approval, release, media types, thinking, "
        "schemas and tools are all one mechanism (`requirements.py`), and a verb that skips it "
        "skips all of them — silently, with a 200. Route the call through "
        "`dispatch_with_fallback` or `resolve_direct_target`, or add it to `UNCONDITIONED` with "
        "the reason it is not a caller's request."
    )


def test_the_exemptions_name_calls_that_exist() -> None:
    """An exemption that outlives its call site stops describing the system and starts excusing
    the next one: a name left free for a future call to reuse."""
    stale = sorted(set(UNCONDITIONED) - set(_dispatch_sites()))

    assert not stale, f"UNCONDITIONED names call sites that no longer exist: {stale}"


def test_both_gates_are_real_functions() -> None:
    """`GATES` is matched by name, so a rename would make every site read as ungated — noisy and
    safe — while a **deleted** gate would make them read as gated by a name nothing defines. That
    is the direction worth asserting."""
    serving = (SOURCE / "api" / "serving.py").read_text()
    dispatch = (SOURCE / "pipeline" / "dispatch.py").read_text()

    assert "async def resolve_direct_target(" in serving
    assert "async def dispatch_with_fallback(" in dispatch
