"""A KIRA model id is written once, where the seed writes it.

`tools/seed_local_catalog.py` decides which integer the demo's chat model answers to, and it moved
that integer: *"`1004` is the predecessor's own chat id (`ADR-0010`) … every document and every
example said `1004`, and the one runnable command said something else."* `conftest.py` was given
`LOCAL_CHAT_MODEL_ID` in the same round, with the reason spelled out — *"Six tests carried `9001`
as a literal, and moving the demo onto the predecessor's own id would have left every one of them
addressing a model that no longer answers — reported as a `404` about a number, which reads as a
broken surface rather than as a stale test."*

Six were corrected by hand. **Twenty-one were not**, across seven files, and they were found on
2026-08-26 the way the paragraph predicted: a live-stack run in which every KIRA test failed
`422 MODEL_NOT_FOUND` while the surface was working perfectly — one hand-made call to the same
endpoint with the right id answered `200`. An hour spent reading a correct gateway as a broken one.

The correction was a search-and-replace, which is exactly the kind that leaves a twenty-second
copy behind. So the ban is mechanical: no test types a KIRA model id, in any file, ever. The two
the seed owns are imported; a deliberately-unknown id is what `_UNKNOWN_IS_THE_POINT` is for, and
it has to say so at the call site.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: **The integration layer only, and the boundary is the point.**
#:
#: A hermetic test writes its own `ModelRead` row and then calls the id it just chose — the number
#: is local to the test and self-consistent, so typing it there is not a copy of anything. The
#: integration layer calls a **shared, seeded** stack, where the number belongs to
#: `tools/seed_local_catalog.py` and a typed one is a second statement of somebody else's fact.
#:
#: Widening this to every layer was the first attempt and it reported thirteen files, all of them
#: correct — the wolf-crying check `LESSONS.md` §3 names, in a guard written against a real defect.
#: A rule has to be scoped to what it is actually about.
SEARCHED = (ROOT / "tests" / "integration",)

#: `"model_id": 1234` or `model_id=1234` — the wire field and the keyword, with a literal after it.
_TYPED = re.compile(r"""["']?model_id["']?\s*[:=]\s*(\d[\d_]*)""")

#: Ids a test may type because the point of the test *is* that nothing answers to them. Named here
#: rather than inferred, so adding one is a deliberate sentence rather than a number slipping past.
_UNKNOWN_IS_THE_POINT = frozenset(
    {
        "987_654",  # `test_dev_round_surfaces`: an id the catalog has never held
        "999_999",  # `test_kira_surface`: the same, in the surface's own file
        "9999999",  # `test_edge_cases`: a wrong value swept alongside the other wrong values
        "0",  # not a positive integer, which is a different refusal
        "1",  # ditto
    }
)


def _files() -> list[pathlib.Path]:
    return sorted(path for root in SEARCHED if root.exists() for path in root.rglob("test_*.py"))


def test_there_are_test_files_to_search() -> None:
    """A guard on the guard: an empty list would make the check below pass by reading nothing."""
    assert len(_files()) > 20


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_no_test_writes_a_kira_model_id_as_a_literal(path: pathlib.Path) -> None:
    typed = [
        (number, line)
        for line, text in enumerate(path.read_text().splitlines(), start=1)
        for number in _TYPED.findall(text)
        if number not in _UNKNOWN_IS_THE_POINT
    ]

    assert not typed, (
        f"{path.relative_to(ROOT)} names a KIRA model by a typed integer: "
        + ", ".join(f"{number} (line {line})" for number, line in typed)
        + ".\nThe seed owns those numbers (`tools/seed_local_catalog.py`); import "
        "`LOCAL_CHAT_MODEL_ID` or `EMBED_NUMERIC_ID` instead. A typed one survives the seed "
        "moving the model and then fails as `422 MODEL_NOT_FOUND`, which reads as a broken "
        "surface rather than as a stale test. If the id is *meant* to answer to nothing, add it "
        "to `_UNKNOWN_IS_THE_POINT` with a reason."
    )
