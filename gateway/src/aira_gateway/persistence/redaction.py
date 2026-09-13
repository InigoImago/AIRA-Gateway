"""Redaction applied to stored payloads (`FRD-103` hook, `FRD-406` content).

Callers paste credentials into prompts, and a stored prompt is a verbatim copy kept for the use
case's retention period. **Only credential-shaped strings are redacted**: never legitimate business
content, and catastrophic to keep. Names, addresses and customer numbers are *the work* — a gateway
that mangled them would store payloads nobody can use, and storage would be switched off instead.

`AIRA_REDACT_PATTERNS` adds deployment-specific patterns, checked at construction: an invalid regex
**stops the gateway** rather than redacting nothing, and a catastrophically backtracking one is
refused by the shared rule in `aira_common.patterns`, because this runs over caller-supplied text.

Redaction rewrites strings and keeps the payload's structure, so it stays readable.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

from aira_common.patterns import catastrophic_reason

#: What replaces a match. Obviously not data, so "this was removed" is distinguishable from "the
#: caller wrote that", and a redacted value cannot be mistaken for a short credential.
PLACEHOLDER = "[REDACTED]"

#: Credential shapes, each one something that is never legitimate business content.
BUILTIN_PATTERNS: tuple[str, ...] = (
    # An AIRA key. Ours, and it grants use-case access.
    r"aira_[A-Za-z0-9]{4,16}_[A-Za-z0-9]{16,}",
    # Google API key (the `?key=` credential every Gemini client holds).
    r"AIza[0-9A-Za-z\-_]{20,}",
    # OpenAI-style secret key, including the project-scoped form.
    r"sk-[A-Za-z0-9\-_]{16,}",
    # An Authorization header value a caller has pasted into a prompt.
    r"(?i)authorization\s*:\s*\S+",
    # A JWT: three base64url segments. Nothing else in a prompt looks like this.
    r"eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+",
    # A PEM private key block, body and all.
    r"(?s)-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
)


class RedactionMisconfigured(Exception):
    """A configured pattern that would not work, or would not stop working."""


@runtime_checkable
class Redactor(Protocol):
    def redact(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class NoOpRedactor:
    """Passes payloads through unchanged. What tests use; not the gateway's default."""

    def redact(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


class PatternRedactor:
    """Replaces credential-shaped strings anywhere in a stored payload."""

    def __init__(self, patterns: tuple[str, ...] = BUILTIN_PATTERNS) -> None:
        compiled: list[re.Pattern[str]] = []
        for pattern in patterns:
            reason = catastrophic_reason(pattern)
            if reason is not None:
                raise RedactionMisconfigured(
                    f"Redaction pattern {pattern!r} backtracks catastrophically on "
                    f"caller-supplied text: {reason}."
                )
            try:
                compiled.append(re.compile(pattern))
            except re.error as exc:
                # Loudly, at startup: a rule that appears configured and removes nothing is an
                # absent control that looks present.
                raise RedactionMisconfigured(
                    f"Redaction pattern {pattern!r} is not a valid regular expression: {exc}"
                ) from exc
        self._patterns = tuple(compiled)

    def redact(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._walk(payload)
        return result if isinstance(result, dict) else payload

    def redact_text(self, text: str) -> str:
        for pattern in self._patterns:
            text = pattern.sub(PLACEHOLDER, text)
        return text

    def _walk(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            # Keys are structure, not content: rewriting them would break every reader.
            return {key: self._walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._walk(item) for item in value]
        return value


def build_redactor(extra_patterns: str = "") -> Redactor:
    """The built-in credential patterns plus any the deployment adds (newline- or ``;``-separated).

    Additive, never replacing: naming an internal token format must not stop the built-in ones.
    """
    extra = tuple(
        piece.strip() for piece in extra_patterns.replace("\n", ";").split(";") if piece.strip()
    )
    return PatternRedactor(BUILTIN_PATTERNS + extra)
