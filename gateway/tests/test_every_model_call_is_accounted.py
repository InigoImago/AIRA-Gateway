"""Every model call belongs to a use case, is billed, and leaves a row.

Asked for directly on 2026-08-11: _"stell sicher, dass jeder Modellaufruf einem Use Case oder
einem Key von Use Case gehört. Es darf nicht nicht angerechnet werden und es darf keine
undokumentierten Requests geben."_

The reason this is a **structural** test rather than a list of behaviour tests is that the hole it
guards was not a wrong answer anywhere — it was a call site nobody had thought about. The dry-run
endpoint invoked whatever model a JSON body named, with no use case, no budget and no audit row,
for as long as it has existed; every behaviour test in this suite passed the whole time, because
none of them was about that endpoint. What was missing was somebody counting the places a model
can be reached.

So: this parses the source, finds every call that could reach a provider, and requires each to be
on a list somebody had to write. Adding a call site changes nothing; adding one *without* saying
how it is attributed and recorded is a diff that fails — which is the only difference that matters
between a convention and a rule (the same argument `test_every_route_is_guarded` makes about
authentication, and `test_every_upstream_is_region_checked` about residency).

The behaviour behind each entry is tested elsewhere; the list is what makes the **set** complete.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "aira_gateway"

#: The methods that reach a model and cost money. `ping` and `available_models` are deliberately
#: **not** here: they are GETs of a listing, chosen over a generation precisely so that a health
#: check cannot bill anybody or wake a scaled-to-zero endpoint (`FRD-117` §5.2).
BILLABLE = frozenset({"generate", "stream_generate", "embed"})

#: Every place outside the adapters where a model is invoked, and how each one is attributed,
#: billed and recorded.
#:
#: An entry is `"<module path>:<method>"`. The value is the justification a reader needs — not
#: decoration: if you cannot write one, the call site is the defect.
ACCOUNTED: dict[str, str] = {
    "api/gemini/routes.py:embed": (
        "The Gemini surface's `:embedContent`. Attribution comes from `require_attribution` on the "
        "router mount, and the call is inside `Accounting`, which reserves before and settles "
        "after — so the row and the budget booking happen on every exit including a refusal."
    ),
    "api/kira/routes.py:embed": (
        "The KIRA surface's `/embed`. That surface resolves its own attribution (`FRD-107` §5.3 — "
        "one membership, or a header, never an unattributed bucket) and shares this file's "
        "`Accounting`, which is what `test_a_kira_request_is_audited_exactly_like_a_gemini_one` "
        "compares row for row."
    ),
    "api/incidents/thinking_check.py:generate": (
        "The console's *Ask the model* and *Check reachability* buttons (`ADR-0021`, `FRD-609`), "
        "and they **are** accounted now — `_record_diagnostic` writes a row per probe with the "
        "real usage and the real price, under `Outcome.DIAGNOSTIC` (`FRD-610`).\n\n"
        "Named here rather than removed because the exemption this entry used to carry is worth "
        "keeping visible: it argued that the spend was tiny, bounded and role-gated, which was "
        "true and answered a different question. The owner's rule is that **every request is "
        "auditable and budgetable**, and a small amount nobody can see is not a small amount — it "
        "is an invisible one. What is still absent is a *budget*: allowances are anchored to a use "
        "case, and this call belongs to none by construction, so there is nothing to book it "
        "against until an installation-level allowance exists (`FRD-610` §4)."
    ),
    "pipeline/classifiers/injection.py:generate": (
        "The LLM injection classifier. It returns a `ModelCall`, which `record_pipeline_calls` "
        "turns into a `pipeline:<step>` row, booked as a request by the owner's decision "
        "(`book_side_call`): a step's call reaches a model and costs money. It takes no rate-limit "
        "token — the gate is taken once, for the caller's request (`FRD-125b`)."
    ),
    "pipeline/classifiers/routing.py:generate": (
        "The LLM router, on the same terms as the injection classifier: a `ModelCall` recorded as "
        "`pipeline:model_route`, booked as a request and taking no rate-limit token (`FRD-125b`)."
    ),
    "pipeline/classifiers/redaction.py:generate": (
        "The PII filter's redactor (`FRD-309`), on the same terms: a `ModelCall` recorded as "
        "`pipeline:pii_filter`, booked as a request and taking no rate-limit token (`FRD-125b`)."
    ),
    "pipeline/dispatch.py:generate": (
        "The dispatch chain itself. Reached only from the surfaces, inside `Accounting`, with the "
        "per-hop requirements applied to every candidate (`ADR-0012` §3)."
    ),
    "pipeline/dispatch.py:stream_generate": (
        "The same chain, opened for a stream (`FRD-302` FR-6) — **both** streaming surfaces reach "
        "their upstream through here now, which is why neither names a call site of its own any "
        "more.\n\n"
        "Where it differs from every other entry: the HTTP request is issued **before** the "
        "`Accounting` context is entered, because the first chunk is what proves a candidate can "
        "serve and the chain pulls it while a refusal can still be a status. What that does not "
        "move is the accounting — the chunk is handed to the surface unread, so its usage, like "
        "every later chunk's, is read inside `Accounting`, whose settle is `asyncio.shield`ed so a "
        "client dropping the socket mid-stream still books what was spent and still leaves a row. "
        "A candidate that failed before producing a chunk spent nothing and is recorded as a "
        "passed-over attempt on the row (FR-11)."
    ),
}


def _call_sites() -> dict[str, list[int]]:
    """Every billable call outside the adapters, by `<module>:<method>`.

    `upstreams/` is excluded because that **is** the adapter layer: the calls there are a mapper
    calling its own transport, not a decision to spend somebody's budget. Everything above it is
    a decision.
    """
    found: dict[str, list[int]] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        relative = path.relative_to(SOURCE).as_posix()
        if relative.startswith("upstreams/"):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in BILLABLE:
                found.setdefault(f"{relative}:{func.attr}", []).append(node.lineno)
    return found


def test_there_are_call_sites_to_check() -> None:
    """The guard's own failure mode: a parser that finds nothing passes every assertion below.
    This project has shipped a guard that could not fail twice, and both times it was silently
    green."""
    assert len(_call_sites()) >= 5


def test_every_model_call_is_attributed_billed_and_recorded() -> None:
    undocumented = sorted(set(_call_sites()) - set(ACCOUNTED))

    assert not undocumented, (
        "these places invoke a model and nothing says how the call is attributed, billed and "
        "recorded:\n  " + "\n  ".join(undocumented) + "\n\nEvery model call belongs to a use case "
        "and leaves a row (`ADR-0013`). Route it through `Accounting` or record it as a "
        "`ModelCall`, then add it to `ACCOUNTED` with the reason — a call site nobody can justify "
        "in a sentence is the defect, not the list."
    )


def test_the_list_names_only_call_sites_that_exist() -> None:
    """A list that outlives its call sites stops describing the system and starts excusing new
    ones: an entry for code that is gone is an exemption waiting for a future call to reuse the
    name."""
    stale = sorted(set(ACCOUNTED) - set(_call_sites()))

    assert not stale, f"ACCOUNTED names call sites that no longer exist: {stale}"


def test_a_health_probe_is_not_a_model_call() -> None:
    """`ping` is deliberately outside `BILLABLE`, and this states why rather than leaving it to be
    noticed: it is a **GET of a listing**, chosen over a generation so a readiness probe cannot
    bill anybody and cannot wake a scaled-to-zero endpoint on every check (`FRD-117` §5.2).

    The assertion is that no adapter's `ping` reaches a billable verb — if one ever did, the probe
    would become exactly the free, unattributed model call this file exists to rule out.
    """
    for path in sorted((SOURCE / "upstreams").rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef) or node.name != "ping":
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute):
                    assert inner.func.attr not in BILLABLE, (
                        f"{path.name}'s ping calls {inner.func.attr}() — a readiness probe that "
                        "generates bills somebody for the question 'are you there', and against a "
                        "self-deployed model it wakes a scaled-to-zero endpoint on every check."
                    )
