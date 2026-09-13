"""How deeply a caller's JSON nests, measured **before** anything parses it.

Every walk over a body recurses — the `json` decoder, `json.dumps`, `strip_attachments`, the
redactor, `storable`, the pydantic models — so a caller who chooses the depth chooses where a
`RecursionError` happens: a `500`, or a refusal that is never recorded. A caller's own value must
never become a server error or a missing record (`LESSONS.md` §1, `FRD-122`), so the depth is
bounded here, in one place both planes read, and an over-deep body is a named `400`.

**Counted without recursing and without decoding**, since a check that can blow the stack is not a
check. JSON's structural characters are ASCII and every UTF-8 continuation byte has the high bit
set, so the raw bytes can be scanned:

1. remove escaped backslashes, then escaped quotes — after which every remaining ``"`` opens or
   closes a string (order matters: ``\\\\"`` is an escaped backslash and *then* a closing quote);
2. split on ``"`` and keep the even segments, the parts outside any string — so brackets inside a
   text field count for nothing, and a caller may still send source code;
3. map ``{``/``[`` to +1 and ``}``/``]`` to −1, drop everything else, and run the sum in C.

Four C-level passes, costing the same order as parsing — which is what this makes safe.
"""

from __future__ import annotations

import itertools

#: How deep a request body may nest.
#:
#: Shared rather than a setting per plane, because a body the gateway accepts may be sent to the
#: control plane's endpoints too. Derived: the deepest structure anything here defines is a
#: `responseSchema` at 8 levels inside its wrapper, 14 (`FRD-112` FR-3); 64 is four times that,
#: and far below the interpreter's 1000 frames where a recursive walk fails — so every later walk
#: survives a body this accepts.
MAX_JSON_DEPTH = 64

_STRUCTURAL = b"{[}]"
#: +1 for an opening bracket, −1 (`0xff` read as a signed byte) for a closing one.
_STEPS = bytes.maketrans(_STRUCTURAL, b"\x01\x01\xff\xff")
_DROP = bytes(byte for byte in range(256) if byte not in _STRUCTURAL)


def nests_deeper_than(raw: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """Whether ``raw`` — a JSON document, as bytes — nests deeper than ``limit``.

    A verdict rather than a depth, so the common case costs two C-level counts: a document with no
    more than ``limit`` opening brackets in total cannot nest deeper than ``limit``.

    Malformed input is measured rather than refused: a document that does not parse is about to be
    refused as unparseable anyway, and both answers are a `400`.
    """
    if raw.count(b"{") + raw.count(b"[") <= limit:
        return False
    cleaned = raw.replace(b"\\\\", b"").replace(b'\\"', b"")
    outside = b"".join(cleaned.split(b'"')[0::2])
    steps = outside.translate(_STEPS, _DROP)
    return max(itertools.accumulate(memoryview(steps).cast("b")), default=0) > limit
