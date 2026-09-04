"""No upstream model call without a mark saying which model, and why (`FRD-619`).

**The rule.** Every place this gateway asks a provider for something — `generate`,
`stream_generate`, `embed` — must be inside `telemetry.model_call_span` (or, for a stream,
`telemetry.model_call_chunks`). What that buys is on the *client span* the instrumentation
produces: the caller's identity and the model's name, on the record the delivery channel forwards
as a model access. Without it that record is a method, a status and a URL, which on an
OpenAI-compatible upstream does not even name the model — the model travels in the body.

**Why it is a test and not a convention.** There are seven such call sites, spread across dispatch,
two streaming surfaces, two embedding surfaces, three pipeline classifiers and the incident probe.
This project has now paid twice for the shape *a fact applied at each site is missing from one of
them* — `set_attribution` (`FRD-105`) and `prepare_for_dispatch` (`FRD-126`) both exist because of
it. Seven sites and a convention is six sites and a convention.

Unlike those two, the act **cannot** be extracted into one function: `dispatch_with_fallback` needs
it per attempt, a stream needs it around the iteration rather than the call, and a classifier is a
different purpose. So the sites stay, and this fails when an eighth appears without the mark.

It reads source, like `test_surface_layering.py`. A behavioural equivalent would have to drive
every one of the seven paths with a live upstream to notice a missing mark on one of them, which
is the kind of test that gets marked `slow` and then gets skipped.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aira_gateway.telemetry import MODEL_CALL_PURPOSES

SOURCE = Path(__file__).resolve().parents[2] / "gateway" / "src" / "aira_gateway"

#: An upstream asked to do something. `available_models` is not here: it enumerates a catalogue and
#: reaches no model, so labelling it a model access would inflate the very count this exists to
#: make countable.
INVOCATION = re.compile(r"\b\w+\.(generate|stream_generate|embed)\(")

#: How far above a call site the mark may be. Generous enough for a `try:` and a comment, tight
#: enough that it has to be the enclosing block rather than something earlier in the function.
LOOKBACK = 8

#: Where the marks themselves are defined — the mark cannot mark itself.
EXEMPT_FILES = {"telemetry.py"}

#: **The adapters are the callee, not the caller.** `upstreams/` is where `generate` is
#: *implemented*; the calls that match in there are an adapter reaching its own helper
#: (`self._routes.embed(...)`) or one of its own methods (`mock.py`'s `stream_generate` delegating
#: to `generate`), inside a block a caller has already marked. Marking them again would report a
#: model access twice for one call.
#:
#: It is also the only layer that *could not* fill the mark in honestly: an adapter knows the model
#: and never the purpose, and `aira.model_call.purpose` is the field this exists to make groupable.
EXEMPT_DIRECTORIES = {"upstreams"}


def _call_sites() -> list[tuple[Path, int, str]]:
    sites: list[tuple[Path, int, str]] = []
    for path in sorted(SOURCE.rglob("*.py")):
        if path.name in EXEMPT_FILES or EXEMPT_DIRECTORIES & set(path.parts):
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            code = line.split("#", 1)[0]
            if INVOCATION.search(code) and "def " not in code:
                sites.append(
                    (path, number, "\n".join(lines[max(0, number - 1 - LOOKBACK) : number]))
                )
    return sites


def test_there_are_call_sites_to_check() -> None:
    """The regex is the test. A rename upstream that made it match nothing would turn every
    assertion below into a vacuous pass — the failure mode `LESSONS.md` §5 names."""
    assert len(_call_sites()) >= 7, (
        "no upstream invocations found; the pattern has stopped matching what it is about"
    )


@pytest.mark.parametrize(
    ("path", "line", "context"),
    [pytest.param(*site, id=f"{site[0].name}:{site[1]}") for site in _call_sites()],
)
def test_the_call_is_inside_a_mark(path: Path, line: int, context: str) -> None:
    assert "model_call_span" in context or "model_call_chunks" in context, (
        f"{path.relative_to(SOURCE.parent.parent)}:{line} calls a model without a mark, so the "
        "record the delivery channel forwards for it will name no model and no caller "
        "(FRD-619) — wrap it in `model_call_span`, or `model_call_chunks` for a stream"
    )


def test_the_purposes_are_the_declared_ones() -> None:
    """A purpose spelled two ways is a grouping that silently splits in two."""
    used = {
        match.group(1)
        for path in SOURCE.rglob("*.py")
        for match in re.finditer(r'purpose="([^"]+)"', path.read_text(encoding="utf-8"))
    }
    assert used <= MODEL_CALL_PURPOSES, f"undeclared purposes in use: {used - MODEL_CALL_PURPOSES}"
