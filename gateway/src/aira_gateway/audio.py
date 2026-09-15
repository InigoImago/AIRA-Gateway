"""What the audit trail keeps of spoken audio: a description, never the audio (`ADR-0026`).

A buffered answer is described by the writer like any inline datum
(`attachments.strip_attachments`). A stream arrives in pieces, so its description is built here as
they pass, in the same shape.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from aira_gateway.core.canonical import DataPart

#: Linear PCM names its sample rate in the media type (`audio/L16;codec=pcm;rate=24000`). Sixteen
#: bits are two bytes a sample, and a speech model answers in one channel.
_RATE = re.compile(r"rate=(\d+)")
_BYTES_PER_SAMPLE = {"audio/l16": 2}


def duration(media_type: str, size: int) -> float | None:
    """Seconds of one-channel linear PCM, where the media type states the rate; else ``None``."""
    per_sample = _BYTES_PER_SAMPLE.get(media_type.split(";", 1)[0].strip().lower())
    match = _RATE.search(media_type)
    if per_sample is None or match is None or int(match.group(1)) == 0:
        return None
    return round(size / (per_sample * int(match.group(1))), 3)


class AudioDigest:
    """A description built as audio arrives, in pieces."""

    def __init__(self) -> None:
        self._hash = hashlib.sha256()
        self.size = 0
        self.media_type = ""

    def add(self, part: DataPart) -> None:
        self.media_type = self.media_type or part.media_type
        self._hash.update(part.data)
        self.size += len(part.data)

    @property
    def empty(self) -> bool:
        return not self.media_type

    def describe(self) -> dict[str, Any]:
        """The shape `strip_attachments` stores for an inline datum, plus the duration."""
        described: dict[str, Any] = {
            "kind": "data",
            "media_type": self.media_type,
            "bytes": self.size,
            "sha256": self._hash.hexdigest(),
        }
        seconds = duration(self.media_type, self.size)
        if seconds is not None:
            described["seconds"] = seconds
        return described
