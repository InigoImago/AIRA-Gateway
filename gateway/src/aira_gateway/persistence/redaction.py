"""Redaction hook applied to stored payloads (FRD-103).

The default is a no-op (store as much as possible). Real redaction rules (PII, secrets,
data-leak patterns) plug in here in later phases.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Redactor(Protocol):
    def redact(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class NoOpRedactor:
    """Passes payloads through unchanged."""

    def redact(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload
