"""Reading a caller's body: bounded in depth, and refused if it cannot be stored or forwarded.

Both checks run before any control is spent. A body that fails later — inside the writer, or when
httpx encodes the upstream request — would already have consumed a rate-limit allowance and a
budget reservation, and would leave no audit row, because recording covers requests that reached
an upstream (`FRD-122`, `FRD-124`).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request

from aira_common.nesting import MAX_JSON_DEPTH, nests_deeper_than
from aira_gateway.api.gemini.errors import GeminiHTTPError


async def json_body(request: Request) -> Any:
    """The caller's body, parsed — refused when it nests deeper than anything on the path can walk.

    Every walk over a body recurses (decoder, redactor, writer), and the caller picks the depth;
    `aira_common.nesting` holds the bound. Every route reads its body through here —
    `test_a_body_cannot_be_nested_out_of_the_audit_trail.py` fails on a `request.json()` call.

    Raises `GeminiHTTPError` for a body that is too deep (both surfaces render it), and
    `ValueError`, like `request.json()`, for one that is not JSON: each surface words that refusal
    itself.
    """
    raw = await request.body()
    if nests_deeper_than(raw, MAX_JSON_DEPTH):
        raise GeminiHTTPError(
            400,
            f"The request body nests deeper than {MAX_JSON_DEPTH} levels. Nothing on this path "
            "can walk a structure that deep — including the writer that records the request — so "
            "it is refused here rather than half-processed.",
            "INVALID_ARGUMENT",
        )
    return json.loads(raw)


def ensure_body_is_encodable(body: Any) -> None:
    """Refuse text that cannot be written as UTF-8, and numbers JSON cannot represent.

    JSON may escape half a surrogate pair (`"\\ud800"`), which parses into a Python string no UTF-8
    encoder accepts. Python's parser also accepts `Infinity`/`NaN`, which RFC 8259 does not have
    and Postgres will not store in a `json` column. Either would fail far downstream as a 500 with
    no audit row, so it is a `400` here. `ensure_ascii=False` is what makes the encoder object.
    """
    try:
        json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise GeminiHTTPError(
            400,
            "The request body contains a character that is not valid UTF-8 — an unpaired "
            f"surrogate ({exc.object[exc.start : exc.end]!r}). JSON can escape one, and nothing "
            "can send it.",
            "INVALID_ARGUMENT",
        ) from exc
    except ValueError as exc:
        raise GeminiHTTPError(
            400,
            "The request body contains a number JSON cannot represent — `Infinity`, `-Infinity` "
            "or `NaN`. Python's parser accepts them and the standard does not, so nothing "
            "downstream can store or forward one.",
            "INVALID_ARGUMENT",
        ) from exc
    except TypeError as exc:  # pragma: no cover - a body that parsed will serialise
        raise GeminiHTTPError(
            400, "The request body is not representable as JSON.", "INVALID_ARGUMENT"
        ) from exc
