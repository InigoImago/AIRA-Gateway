"""Redaction applied to stored payloads (`FRD-103` hook, `FRD-406` content, 2026-08-08).

The hook has existed since Phase 1 and did nothing. That was deliberate and it was also the gap
`ADR-0007` left open: a stored prompt is a verbatim copy of whatever a caller sent, kept for as
long as the use case's retention says, readable by anyone who can read the table — and callers
paste credentials into prompts. "Here is our API key, write me a curl command" is not an exotic
input, it is a Tuesday.

**What is redacted, and what deliberately is not.** Only things that are never legitimate business
content and are catastrophic to keep: credential-shaped strings. Names, addresses, customer
numbers and everything else a prompt might contain are *the work* — a gateway that mangles them
produces a stored payload nobody can use for the debugging and evidence it exists for, and the
deployment then switches storage off entirely, which is strictly worse than storing it.

So the built-in set is narrow and each entry has an argument:

- an AIRA key (`aira_<prefix>_<secret>`) — ours, and it grants use-case access
- a Google API key (`AIza…`) and an OpenAI-style key (`sk-…`) — the upstream credentials a caller
  is most likely to be holding when they ask a question about them
- an `Authorization:` header value, wherever a caller has pasted one
- a JWT — three base64 segments, which nothing else looks like
- a PEM private key block

`AIRA_REDACT_PATTERNS` adds deployment-specific ones (an internal token format, a personnel
number). They are checked at construction: an invalid regex **stops the gateway** rather than
silently redacting nothing, and a nested quantifier is refused for the same ReDoS reason
`ADR-0007` refuses one in a pipeline config — this runs over caller-supplied text.

**Structure is preserved.** Redaction walks the JSON and rewrites *strings*, so a stored payload
stays the shape it was and remains readable next to the response it produced. A payload replaced
wholesale would be a payload nobody looks at twice.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

#: What replaces a match. Fixed-length and obviously not data, so a reader can tell "this was
#: removed" from "the caller wrote that" — and so a redacted value cannot be confused for a short
#: credential and tried.
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

#: A quantified group that is itself quantified — the shape that backtracks exponentially. Refused
#: rather than accepted, because these patterns run over caller-supplied text on the write path.
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*][^)]*\)\s*[+*]")


class RedactionMisconfigured(Exception):
    """A configured pattern that would not work, or would not stop working."""


@runtime_checkable
class Redactor(Protocol):
    def redact(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class NoOpRedactor:
    """Passes payloads through unchanged.

    Kept, and it is what tests use to assert on an unmodified payload. It is **not** the default
    the gateway runs with any more.
    """

    def redact(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload


class PatternRedactor:
    """Replaces credential-shaped strings anywhere in a stored payload."""

    def __init__(self, patterns: tuple[str, ...] = BUILTIN_PATTERNS) -> None:
        compiled: list[re.Pattern[str]] = []
        for pattern in patterns:
            if _NESTED_QUANTIFIER.search(pattern):
                raise RedactionMisconfigured(
                    f"Redaction pattern {pattern!r} nests a quantifier inside a quantified group; "
                    "that backtracks exponentially on caller-supplied text."
                )
            try:
                compiled.append(re.compile(pattern))
            except re.error as exc:
                # Loudly, at startup. A pattern that silently compiles to nothing is a redaction
                # rule that appears configured and removes nothing — an absent control wearing a
                # present one's badge, the same failure `FRD-125` fixed in the injection filter.
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
            # Keys are structure, not content: rewriting them would change the shape of a stored
            # payload and break every reader that indexes into it.
            return {key: self._walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._walk(item) for item in value]
        return value


def build_redactor(extra_patterns: str = "") -> Redactor:
    """The built-in credential patterns plus any the deployment adds (newline- or ``;``-separated).

    Additive, never replacing: a deployment naming its own token format must not thereby stop
    redacting Google keys, which is exactly what a replacing setting would do the first time
    somebody used it.
    """
    extra = tuple(
        piece.strip() for piece in extra_patterns.replace("\n", ";").split(";") if piece.strip()
    )
    return PatternRedactor(BUILTIN_PATTERNS + extra)
