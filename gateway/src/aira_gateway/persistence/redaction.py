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
#:
#: **Each must cost time linear in the text.** They run in the audit writer, on the event loop
#: every request shares, over a body of up to 8 MiB the caller chose — so a pattern that rescans the
#: rest of the text from every place it could start is a way to stop the instance. Where a shape
#: can start inside a long run of its own characters it is anchored to the start of the run, and a
#: body it consumes is matched possessively (``++``, ``*+``), which never gives characters back.
#: `test_redacting_hostile_text_takes_linear_time` holds every pattern here to that.
BUILTIN_PATTERNS: tuple[str, ...] = (
    # An AIRA key. Ours, and it grants use-case access.
    r"aira_[A-Za-z0-9]{4,16}_[A-Za-z0-9]{16,}",
    # Google API key (the `?key=` credential every Gemini client holds).
    r"AIza[0-9A-Za-z\-_]{20,}",
    # OpenAI-style secret key, including the project-scoped form.
    r"sk-[A-Za-z0-9\-_]{16,}",
    # An Authorization header a caller has pasted into a prompt, **with its value**: the scheme
    # word alone (`Bearer`, `Basic`) is not the secret, the rest of the value is. As a header line,
    # a key in code (`"Authorization": "…"`) or an assignment, and up to the end of the line or the
    # quote that closes it — so the address after a quoted `curl -H '…'` survives.
    r"(?i)authorization[\"']?[ \t]*+[:=][ \t]*+[\"']?[^\r\n\"']++",
    # A JWT: three base64url segments. Nothing else in a prompt looks like this. It starts where a
    # run of base64url characters starts (or after an escape such as a literal `\n`), because
    # `eyJ` repeated in one run would otherwise start a scan to its end from every occurrence.
    r"(?:(?<![A-Za-z0-9_-])|(?<=\\[nrt]))eyJ[A-Za-z0-9_-]++\.[A-Za-z0-9_-]++\.[A-Za-z0-9_-]++",
    # A PEM private key block, body and all — and a pasted one cut off before its END line, whose
    # body is the key material. The body is base64, which has no `-`; the legacy encrypted form's
    # two header lines, which do, are named. One class consumed possessively, never "anything up
    # to END", which would scan the rest of the text from every BEGIN that has none.
    r"-----BEGIN [A-Z ]{0,32}PRIVATE KEY-----\s*+(?:Proc-Type:[^\r\n]*+\s*+DEK-Info:[^\r\n]*+)?"
    r"[^-]*+(?:-----END [A-Z ]{0,32}PRIVATE KEY-----)?",
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
