"""Where the reference comes from: the copy this release ships, or the live list (`FRD-508`).

**The shipped copy is the default**, because an installation must work without reaching the
internet and must not start doing so because it was upgraded. `tools/model_reference.py` refreshes
it before a release. An installation that wants today's list sets `AIRA_MODEL_REFERENCE_URL`
(`https://models.dev/api.json`); Management then asks it at most once per `REFRESH_SECONDS` and
answers from the shipped copy, saying why, whenever the live list cannot be had.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from aira_management.apps.catalog.reference.normalize import drafts

#: The copy shipped with this release, already in drafts form.
SNAPSHOT_PATH = Path(__file__).with_name("models.json")

#: How long a live list is used before it is asked again. The list changes a few times a week.
REFRESH_SECONDS = 24 * 3600

#: How long one fetch may take. The console waits for it.
FETCH_TIMEOUT_SECONDS = 10.0

#: The largest live answer read: the whole list is a few megabytes.
MAX_LIVE_BYTES = 32 * 1024 * 1024

#: How long a failed fetch is not repeated. Without a pause, every console visit would wait out the
#: whole timeout again while the list is unreachable.
RETRY_AFTER_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class Reference:
    """The drafts, and where and when they come from."""

    source: str
    as_of: str
    entries: list[dict[str, Any]]
    #: Why the live list was not used, where one was configured and could not be had.
    live_error: str = ""
    currency: str = "USD"
    fetched_at: float = field(default=0.0, compare=False)


_lock = threading.Lock()
_live: Reference | None = None
#: When the last fetch failed, and why, for the pause above.
_failed: tuple[float, str] | None = None


def shipped() -> Reference:
    """The copy this release carries."""
    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    return Reference(source=data["source"], as_of=data["as_of"], entries=data["entries"])


def _fetch(url: str, client: httpx.Client | None = None) -> Reference:
    owned = client is None
    http = client or httpx.Client(timeout=FETCH_TIMEOUT_SECONDS, verify=True)
    try:
        response = http.get(url)
        response.raise_for_status()
        if len(response.content) > MAX_LIVE_BYTES:
            raise ValueError("the answer is larger than a model list")
        entries = drafts(response.json())
    finally:
        if owned:
            http.close()
    if not entries:
        raise ValueError("the answer holds no model this gateway can serve")
    return Reference(
        source=url,
        as_of=datetime.now(UTC).date().isoformat(),
        entries=entries,
        fetched_at=time.monotonic(),
    )


def _shipped_because(reason: str) -> Reference:
    fallback = shipped()
    return Reference(
        source=fallback.source,
        as_of=fallback.as_of,
        entries=fallback.entries,
        live_error=reason,
    )


def current(url: str, *, client: httpx.Client | None = None) -> Reference:
    """The reference to answer with: live where configured and reachable, shipped otherwise."""
    global _live, _failed
    if not url:
        return shipped()
    with _lock:
        now = time.monotonic()
        held = _live
        if held is not None and held.source == url and now - held.fetched_at < REFRESH_SECONDS:
            return held
        if _failed is not None and now - _failed[0] < RETRY_AFTER_SECONDS:
            return _shipped_because(_failed[1])
        try:
            _live = _fetch(url, client)
        except (httpx.HTTPError, ValueError) as exc:
            # The type, never the text: an HTTP client's message carries the address it called.
            reason = (
                f"{url} could not be read ({type(exc).__name__}); this is the copy shipped with "
                "this release."
            )
            _failed = (now, reason)
            return _shipped_because(reason)
        _failed = None
        return _live


def forget() -> None:
    """Drop the held live list, so the next question asks again. For tests and a changed URL."""
    global _live, _failed
    with _lock:
        _live = None
        _failed = None
