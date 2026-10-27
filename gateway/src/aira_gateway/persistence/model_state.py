"""What an audit row leaves out of a payload besides attachments: the model's own state.

A signature on a part is what a model needs to continue a turn — Google's own, or the state this
gateway seals into one for another vendor (`FRD-152`) — and not part of what was said. The caller
receives it and hands it back with every later request; the row keeps no copy, which would be
opaque to every reader and, for a Claude turn, would hold its reasoning a second time, encoded.
"""

from __future__ import annotations

from typing import Any

#: The field a part's signature travels in, as each surface spells it: Google's wire, and the KIRA
#: surface's opaque metadata. Applied to requests and answers alike.
SIGNATURE_KEYS = frozenset({"thoughtSignature", "thought_signature"})


def without_model_state(payload: Any) -> Any:
    """``payload`` with every signature left out, at any depth."""
    if isinstance(payload, list):
        return [without_model_state(item) for item in payload]
    if not isinstance(payload, dict):
        return payload
    return {
        key: without_model_state(value)
        for key, value in payload.items()
        if key not in SIGNATURE_KEYS
    }
