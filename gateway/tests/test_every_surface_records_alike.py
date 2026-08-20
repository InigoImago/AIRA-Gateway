"""Every audit row is assembled from the same fields, whichever surface produced it.

**Why this is structural rather than a behaviour test.** `api/serving.py` extracted *"everything
below the surface"* — the pre-dispatch gate, the pipeline, the dispatch chain, the audit writer —
and that boundary is right. What it left to each surface is what sits *at* the surface: parsing,
the error envelope, and the two places a row is written for a request the shared path never
finished. Those forked, quietly, in the direction that produces a plausible row rather than an
error:

- **`api` was a defaulted parameter.** `record_request(..., api: str = "gemini")` meant a call site
  that forgot it produced a Gemini row — right on one surface, wrong on the other, silent on both.
  Measured 2026-08-13: a KIRA request whose pipeline ran an LLM filter left its classifier row
  under `api='gemini'`, so a use case's *governance* spend (`FRD-125b`) was reported against a
  surface it had never used.
- **`tool_calls` was recorded only on the served path.** A request that offered functions and was
  then refused recorded nothing about them, on either surface — so "somebody keeps trying to use
  tools here", which is a `FRD-122` question, had no answer.

Neither is an error anywhere. Both produce a row that looks complete and says something untrue,
which is why a behaviour test per surface did not catch them: each surface's row was *internally*
consistent.

So the check is on the **call sites**, mechanically: every place that writes an audit row is
compared against the shared one, and a field that only some of them pass has to be named here with
the reason. A discriminator each caller restates is one a caller eventually restates wrongly —
which is the whole reason `api` now travels on the `AuditTrail`, set once by the surface that owns
the request.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from aira_gateway.audit import AuditTrail
from aira_gateway.persistence import recorder

SOURCE = Path(__file__).resolve().parents[1] / "src" / "aira_gateway"

#: The site every other one is compared against: the shared success path, which both surfaces and
#: every verb reach through `accounting`.
REFERENCE = "api/serving.py:_settle_and_record"

#: Fields a site may legitimately omit, and why. **Not decoration** — an omission with no entry
#: here is the defect, and an entry that stops being true is one somebody has to notice.
ALLOWED_OMISSIONS: dict[str, dict[str, str]] = {
    "api/serving.py:record_pipeline_calls": {
        "model_selection": "a classifier call is not routed and falls back to nothing",
        "pipeline_decisions": "the decisions belong to the caller's row, not to the step's own",
        "tool_calls": "a classifier is asked for a word, never for a function",
    },
    "api/gemini/routes.py:_write_refusal": {
        "cost_nanos": "nothing was served, so nothing is priced — and unpriced is not zero",
    },
    "api/kira/routes.py:_record": {},
    "api/incidents.py:_record_diagnostic": {
        # An administrator asking a model about itself (`FRD-610`). Four fields are not omitted so
        # much as **inapplicable**, and each for its own reason rather than as a group:
        "model_selection": (
            "nothing routed: the model is named by the person pressing the button, so there is no "
            "selection to record and 'direct' would be a claim about a chain that never ran"
        ),
        "pipeline_decisions": (
            "no pipeline runs — a diagnostic belongs to no use case, and a pipeline is a use "
            "case's configuration"
        ),
        "requested_model": (
            "identical to `model` by construction: a check names one model and reaches that one"
        ),
        "tool_calls": "a probe declares no functions and the answer is one word",
    },
}


def _record_sites() -> dict[str, set[str]]:
    """Every `record_request(...)` call, as `<module>:<function>` → the keywords it passes."""
    sites: dict[str, set[str]] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "record_request":
                    key = f"{path.relative_to(SOURCE).as_posix()}:{function.name}"
                    sites[key] = {kw.arg for kw in node.keywords if kw.arg}
    return sites


def test_the_parser_finds_the_sites_it_is_built_around() -> None:
    """The guard's own failure mode. A parser that matches nothing passes every assertion below,
    and this repository has shipped two guards that could not fail — both silently green."""
    sites = _record_sites()

    assert REFERENCE in sites, f"the reference site is gone; found {sorted(sites)}"
    assert len(sites) >= 4, sorted(sites)


def test_every_recording_site_is_accounted_for() -> None:
    """A new one — a third surface, a new verb, a new kind of row — has to be looked at rather than
    inheriting whatever the defaults happen to be."""
    unknown = sorted(set(_record_sites()) - {REFERENCE} - set(ALLOWED_OMISSIONS))

    assert not unknown, (
        f"these write audit rows and nothing here says what they may leave out: {unknown}. "
        "Add them with the fields they omit and the reason, or pass everything the shared path "
        "does."
    )


def test_no_site_quietly_omits_a_field_the_shared_path_records() -> None:
    sites = _record_sites()
    reference = sites[REFERENCE]
    missing: list[str] = []
    for site, passed in sorted(sites.items()):
        if site == REFERENCE:
            continue
        for field in sorted(reference - passed):
            if field not in ALLOWED_OMISSIONS.get(site, {}):
                missing.append(f"{site} omits {field!r}")

    assert not missing, (
        "these leave a field out of the audit row that the shared path records:\n  "
        + "\n  ".join(missing)
        + "\n\nA row assembled from fewer fields is not an error — it is a row that looks complete "
        "and says less, which is how a KIRA request's classifier spend came to be filed under the "
        "Gemini surface. Pass it, or name it in ALLOWED_OMISSIONS with the reason."
    )


def test_an_exemption_names_a_field_that_is_actually_omitted() -> None:
    """The other direction. An exemption that stops being true reads as coverage of a decision
    nobody made any more — and it is the half nobody notices, because adding a field never fails."""
    sites = _record_sites()
    stale: list[str] = []
    for site, omissions in ALLOWED_OMISSIONS.items():
        passed = sites.get(site, set())
        for field in sorted(omissions):
            if field in passed:
                stale.append(f"{site} passes {field!r}, which is listed as omitted")

    assert not stale, "\n  ".join(stale)


def test_the_surface_is_carried_rather_than_defaulted() -> None:
    """`api` had a default, and a default on a discriminator makes one branch right by accident.

    Asserted on the signature, because that is where the accident lived: as long as `record_request`
    supplies a surface for a caller that did not, a forgotten argument produces a plausible row
    instead of a failure. The value now travels on the `AuditTrail`, which the surface sets once.
    """
    default = inspect.signature(recorder.record_request).parameters["api"].default

    assert default is inspect.Parameter.empty or default == "", (
        f"`record_request` defaults `api` to {default!r}. A recording site that forgets it then "
        "files the row under that surface — right for one of them and silently wrong for every "
        "other. Carry it on the trail instead."
    )


def test_a_surface_cannot_inherit_one() -> None:
    """And the same question one level up, which is where the next one would be inherited.

    Removing the default from `record_request` alone would leave `AuditTrail(api="gemini")` — so a
    third surface constructing a trail without naming itself files every row it writes, refusals
    included, under a surface it is not. The accident moved rather than closed.
    """
    with pytest.raises(TypeError):
        AuditTrail(operation="chat")  # type: ignore[call-arg]
