"""How deeply a caller's JSON nests, measured **before** anything parses it.

Every walk over a body recurses — the `json` decoder, `json.dumps`, `strip_attachments`, the
redactor, `storable`, the pydantic models — so a caller who chooses the depth chooses where a
`RecursionError` happens: a `500`, or a refusal that is never recorded. A caller's own value must
never become a server error or a missing record (`LESSONS.md` §1, `FRD-122`), so the depth is
bounded here, in one place both planes read, and an over-deep body is a named `400`.

**Counted without recursing and without decoding**, since a check that can blow the stack is not a
check. The bytes must be **UTF-8**, and both planes decode a body as UTF-8 before measuring it:
JSON's structural characters are ASCII and every UTF-8 continuation byte has the high bit set, so
the raw bytes can be scanned — where in UTF-16 or UTF-32 a character's bytes can include ``0x22``,
which this would read as a quote. The scan:

1. remove escaped backslashes, then escaped quotes — after which every remaining ``"`` opens or
   closes a string (order matters: ``\\\\"`` is an escaped backslash and *then* a closing quote);
2. drop every byte but the quotes and the brackets;
3. split on ``"`` and keep the parts outside any string — so brackets inside a text field count for
   nothing, and a caller may still send source code. A window at a time, carrying whether the next
   window starts inside a string: a split makes an object per segment, and the window, not the
   body, bounds how many exist at once;
4. map ``{``/``[`` to +1 and ``}``/``]`` to −1 and take the running maximum in C, carrying the
   depth from window to window and stopping at the first one past the limit.

**Linear in the body, in time and in memory** — at most two copies of it, plus a window. Not as
cheap as the parse it guards on every input: a body of nothing but brackets costs a C-level step
per bracket where `json.loads` refuses it at the second character, which is what the request-size
limit in front of this bounds.
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

#: How many bytes of the quotes and brackets are split at once. It bounds the objects a split makes,
#: and is large enough that the loop over windows costs nothing beside the work inside it.
SCAN_WINDOW = 1 << 16

_STRUCTURAL = b"{[}]"
#: +1 for an opening bracket, −1 (`0xff` read as a signed byte) for a closing one.
_STEPS = bytes.maketrans(_STRUCTURAL, b"\x01\x01\xff\xff")
_DROP = bytes(byte for byte in range(256) if byte not in _STRUCTURAL)
#: Every byte the scan has no use for once the escapes are gone: all but a quote and the brackets.
_UNREAD = bytes(byte for byte in range(256) if byte not in b'"' + _STRUCTURAL)


def nests_deeper_than(raw: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    """Whether ``raw`` — a JSON document, as UTF-8 bytes — nests deeper than ``limit``.

    A verdict rather than a depth, so the common case costs two C-level counts: a document with no
    more than ``limit`` opening brackets in total cannot nest deeper than ``limit``.

    Malformed input is measured rather than refused: a document that does not parse is about to be
    refused as unparseable anyway, and both answers are a `400`.
    """
    if raw.count(b"{") + raw.count(b"[") <= limit:
        return False
    cleaned = raw.replace(b"\\\\", b"").replace(b'\\"', b"")
    scanned = cleaned.translate(None, _UNREAD)
    depth = 0
    in_string = False
    counted = False
    for start in range(0, len(scanned), SCAN_WINDOW):
        segments = scanned[start : start + SCAN_WINDOW].split(b'"')
        steps = b"".join(segments[in_string::2]).translate(_STEPS, _DROP)
        # An odd number of quotes leaves the next window on the other side of a string boundary.
        in_string ^= len(segments) % 2 == 0
        if steps:
            counted = True
            if depth + max(itertools.accumulate(memoryview(steps).cast("b"))) > limit:
                return True
            depth += 2 * steps.count(1) - len(steps)
    # No prefix went past the limit. With no bracket to count, the document is at depth 0.
    return not counted and limit < 0
