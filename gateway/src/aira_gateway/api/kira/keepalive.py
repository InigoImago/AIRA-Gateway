"""What a quiet KIRA stream sends to keep its connection (`FRD-146` FR-19, `ADR-0046`).

The predecessor sends an SSE **comment** every fifteen seconds: a client ignores it (it is not an
event, so no `update` without text reaches anybody), and the connection stays open. When it is sent
is the shared layer's (`api.serving.keepalive`); this is only what this surface's clients skip.
"""

from __future__ import annotations

from aira_gateway.api.serving.keepalive import STREAM_HEADERS, TICK, with_pings

#: The comment itself: a line starting with a colon, which SSE defines as ignored.
PING = ": ping\n\n"

__all__ = ["PING", "STREAM_HEADERS", "TICK", "with_pings"]
