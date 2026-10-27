"""Model state a dialect hands to the caller and reads back on the turn that continues it.

A function call's signature is opaque to every client and comes back on that call: Google's own
thinking models put theirs there, and the Gemini clients hand it back unread — measured with
OpenCode 1 and 2, Google's SDK, and the AI SDK from its second major. A dialect whose vendor needs
more than the caller's history to continue a turn seals that state into the signature of the
turn's first call, and the gateway stays without state of its own between requests.

Sealed, not merely encoded: a dialect opens only what it sealed itself, so a conversation that moves
to another dialect carries the envelope along unread, and no vendor is ever sent another's state as
if it were its own signature. Base64 with padding, because Google's SDKs decode the field into bytes
and encode it again, and only the canonical form survives that unchanged.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

#: What marks a signature as sealed here rather than as a model's own.
_MAGIC = b"aira:carried:1\n"


def seal(dialect: str, state: Any) -> str:
    """``state`` as a signature only ``dialect`` will open."""
    document = json.dumps({"dialect": dialect, "state": state}, separators=(",", ":"))
    return base64.b64encode(_MAGIC + document.encode("utf-8")).decode("ascii")


def opened(signature: str | None, dialect: str) -> Any | None:
    """The state ``dialect`` sealed into ``signature``, or ``None`` for anything else."""
    raw = _raw(signature)
    if raw is None:
        return None
    try:
        document = json.loads(raw[len(_MAGIC) :].decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        return None
    if not isinstance(document, dict) or document.get("dialect") != dialect:
        return None
    return document.get("state")


def is_sealed(signature: str | None) -> bool:
    """Whether ``signature`` is one of this gateway's envelopes — never a vendor's to read."""
    return _raw(signature) is not None


def _raw(signature: str | None) -> bytes | None:
    if not signature:
        return None
    try:
        raw = base64.b64decode(signature, validate=True)
    except binascii.Error, ValueError:
        return None
    return raw if raw.startswith(_MAGIC) else None
