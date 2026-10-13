"""Measuring how deep a document goes, without parsing it (`aira_common.nesting`).

The two properties that matter are opposites, and only one of them is obvious. **A body over the
bound is caught** — that is what the module is for. **A body under it is not** — and that is the
half a sloppy implementation loses: a counter that does not understand strings refuses a prompt
containing source code, which is exactly the traffic a coding assistant sends
(`FRD-132`). Brackets inside a string are text.
"""

from __future__ import annotations

import itertools
import json
import random
import tracemalloc
from collections.abc import Callable

import pytest

from aira_common import nesting
from aira_common.nesting import MAX_JSON_DEPTH, SCAN_WINDOW, nests_deeper_than


def _nested(depth: int) -> bytes:
    """A document that nests exactly ``depth`` levels."""
    return (b'{"a":' * depth) + b"1" + (b"}" * depth)


@pytest.mark.parametrize(
    ("raw", "depth"),
    [
        (b"", 0),
        (b"1", 0),
        (b'"a string"', 0),
        (b'{"a": 1}', 1),
        (b"[[[]]]", 3),
        (b'[{"a":[{"b":[]}]}]', 5),
    ],
)
def test_a_document_is_measured_at_its_deepest_point(raw: bytes, depth: int) -> None:
    assert not nests_deeper_than(raw, depth)
    assert nests_deeper_than(raw, depth - 1) or depth == 0


@pytest.mark.parametrize(
    "raw",
    [
        # A prompt carrying source code — the traffic `FRD-132` measured a real coding assistant
        # sending. Every one of these brackets is text, and a counter that cannot tell would
        # refuse the request that matters most.
        json.dumps({"text": "if (a) { b([{c: [1, 2]}]); }" * 200}).encode(),
        # The bracket is inside a string that also contains an escaped quote…
        rb'{"a": "he said \"[[[[[\" and left"}',
        # …and one whose last character before the closing quote is an escaped backslash, which is
        # where a naive escape-skipper reads the closing quote as escaped and swallows the rest of
        # the document.
        rb'{"a": "ends with a backslash \\", "b": 1}',
        # A quote that is *itself* escaped inside an escaped backslash sequence.
        rb'{"a": "\\\"[[[[[", "b": 2}',
    ],
)
def test_brackets_inside_a_string_are_text(raw: bytes) -> None:
    assert not nests_deeper_than(raw, 4)
    assert json.loads(raw), "the fixture has to be a document, or this asserts about nonsense"


def test_a_string_ending_in_an_escaped_backslash_still_closes() -> None:
    """The order the two escape substitutions run in, asserted from the side that can see it.

    ``\\\\"`` is an escaped backslash **and then a closing quote**. Resolving the escaped *quote*
    first eats that closing quote instead, after which the rest of the document reads as being
    inside a string — so everything after it is counted as text and a body of any depth walks
    through. Written after the reversed order survived every other test in this file: they all
    assert a document is *shallow*, which is exactly what an under-counting scanner says about
    everything.
    """
    raw = rb'{"a": "ends with a backslash \\", "b": ' + _nested(MAX_JSON_DEPTH + 1) + b"}"
    assert json.loads(raw), "the fixture has to be a document"
    assert nests_deeper_than(raw, MAX_JSON_DEPTH)


def test_a_deep_document_is_caught_whatever_its_size() -> None:
    """The three shapes measured on 2026-09-08, each of which produced a 500 or a lost row."""
    assert nests_deeper_than(_nested(1_000), MAX_JSON_DEPTH)
    assert nests_deeper_than(_nested(100_000), MAX_JSON_DEPTH)
    assert nests_deeper_than(b"[" * 200_000, MAX_JSON_DEPTH)


def _wide(depth: int) -> bytes:
    """``depth`` levels, and more brackets than that — so the **scan** decides, not the shortcut.

    Written after breaking the comparison on purpose: `>` changed to `>=` survived the first
    version of the test below, because `_nested(64)` carries exactly 64 opening brackets and the
    cheap pre-check answered before the comparison ran. A guard is finished when it has been seen
    to fail (`LESSONS.md` §7) — and this one was measuring the shortcut and calling it the rule.
    """
    inner = b"[" * (depth - 1) + b"]" * (depth - 1)
    return b"[" + inner + b"," + inner + b"]"


@pytest.mark.parametrize("document", [_nested, _wide])
def test_the_bound_is_inclusive(document: Callable[[int], bytes]) -> None:
    """``limit`` levels are allowed; ``limit + 1`` is not — stated because an off-by-one here is
    the difference between a message naming 64 and a body of 64 being refused by it.

    Asked of both a document the shortcut can answer and one it cannot, because those are two
    code paths and only one of them has a comparison in it.
    """
    assert not nests_deeper_than(document(MAX_JSON_DEPTH), MAX_JSON_DEPTH)
    assert nests_deeper_than(document(MAX_JSON_DEPTH + 1), MAX_JSON_DEPTH)


def test_a_shallow_document_never_pays_for_the_scan() -> None:
    """The cheap answer has to be the right one, or the early exit is a second implementation.

    A document with no more than ``limit`` opening brackets cannot nest deeper than ``limit``,
    which is what lets the common case cost two `bytes.count` calls. Asserted against the same
    fixtures the full scan measures, so the two paths cannot disagree.
    """
    for raw in (b'{"a": [1, 2, 3]}', b"[]", b'{"a": {"b": {"c": 1}}}'):
        assert not nests_deeper_than(raw, MAX_JSON_DEPTH)


def test_a_document_that_does_not_parse_is_measured_rather_than_refused() -> None:
    """An unterminated string swallows what follows it — it *is* inside the string — and the
    document is about to be refused as unparseable anyway. Pinned because the alternative reading
    (count the brackets after an unterminated quote) would refuse a body for the wrong reason."""
    assert not nests_deeper_than(b'{"unterminated": "' + b"[" * 500, MAX_JSON_DEPTH)


def test_the_scan_holds_memory_in_proportion_to_the_body() -> None:
    """A split makes a bytes object per quote-delimited segment and a list entry for each, so a body
    of nothing but quotes cost forty times its size — 336 MB for the gateway's 8 MiB limit, on the
    event loop, for a body `json.loads` refuses in two milliseconds. Bounded, not merely smaller."""
    raw = b'"' * 2_000_000 + b"[" * (MAX_JSON_DEPTH + 6)

    tracemalloc.start()
    try:
        assert nests_deeper_than(raw, MAX_JSON_DEPTH)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 2 * len(raw), f"the scan held {peak / len(raw):.1f} times the body"


def test_a_string_longer_than_a_window_is_text_from_end_to_end() -> None:
    """The scan reads a window at a time, and whether it is inside a string carries across — a
    string of brackets longer than a window is still one string."""
    raw = b'{"code": "' + b"[" * (3 * SCAN_WINDOW) + b'", "b": 1}'
    assert json.loads(raw), "the fixture has to be a document"

    assert not nests_deeper_than(raw, MAX_JSON_DEPTH)


def test_a_depth_that_crosses_a_window_is_counted_whole() -> None:
    """…and so does the depth: a structure that opens in one window and deepens in the next is
    measured as one structure, not as two shallow halves."""
    # `[` and `[],` pairs bring the deep part to just before the boundary; `_nested` adds three
    # scanned bytes (`{` and the two quotes of `"a"`) per level.
    pairs = (SCAN_WINDOW - 3 * (MAX_JSON_DEPTH // 2)) // 2
    raw = b"[" + b"[]," * pairs + _nested(MAX_JSON_DEPTH) + b"]"
    assert json.loads(raw), "the fixture has to be a document"

    assert nests_deeper_than(raw, MAX_JSON_DEPTH)
    assert not nests_deeper_than(raw, MAX_JSON_DEPTH + 1)


def _one_pass(raw: bytes, limit: int) -> bool:
    """The rule over the whole body at once — the answer the windowed scan has to give for every
    input. Kept here as the oracle: it is the shape whose memory the window exists to bound."""
    if raw.count(b"{") + raw.count(b"[") <= limit:
        return False
    cleaned = raw.replace(b"\\\\", b"").replace(b'\\"', b"")
    outside = b"".join(cleaned.split(b'"')[0::2])
    steps = outside.translate(
        bytes.maketrans(b"{[}]", b"\x01\x01\xff\xff"),
        bytes(byte for byte in range(256) if byte not in b"{[}]"),
    )
    return max(itertools.accumulate(memoryview(steps).cast("b")), default=0) > limit


@pytest.mark.parametrize("window", [1, 2, 3, 5, 8, 64])
def test_every_window_answers_as_one_pass_would(
    monkeypatch: pytest.MonkeyPatch, window: int
) -> None:
    """Escapes, quotes and brackets in every arrangement, split at every possible place: the window
    is a bound on memory and must never be visible in a verdict."""
    monkeypatch.setattr(nesting, "SCAN_WINDOW", window)
    rng = random.Random(window)
    alphabet = b'{}[]""\\\\a,'
    for _ in range(3000):
        raw = bytes(rng.choice(alphabet) for _ in range(rng.randrange(0, 60)))
        for limit in (-1, 0, 1, 2, 3, 5):
            assert nests_deeper_than(raw, limit) is _one_pass(raw, limit), (raw, limit)


def test_a_multibyte_body_is_scanned_as_it_arrived() -> None:
    """UTF-8 continuation bytes all have the high bit set, so no character of any language can be
    mistaken for a bracket — which is what makes scanning the bytes safe and a decode unnecessary.
    """
    raw = json.dumps({"prompt": "Grüße 日本語 🎉 [[[[["}).encode("utf-8")
    assert not nests_deeper_than(raw, 2)
