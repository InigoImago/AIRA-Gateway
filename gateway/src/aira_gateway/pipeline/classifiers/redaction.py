"""The PII filter's redactor: a trusted model rewrites personal data out of the text (FRD-309).

**The failure this is shaped around is a plausible answer, not an error.** A redactor can answer
with a summary, a translation, a refusal, a preamble or nothing — and each, applied, changes what
the caller asked while the request succeeds with a 200. So a rewrite is checked before it is
trusted, and an unusable one is a failure rather than a redaction.
"""

from __future__ import annotations

from dataclasses import dataclass

from aira_gateway.audit import ModelCall
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role, Thinking
from aira_gateway.pipeline.classifiers.prompts import (
    CLASSIFIER_OUTPUT_TOKENS,
    DEFAULT_REDACTION_INSTRUCTION,
    MAX_REDACTION_OUTPUT_TOKENS,
    REDACTION_CLOSE,
    REDACTION_OPEN,
    REDACTION_OUTPUT_HEADROOM,
    THINKING_OFF,
)
from aira_gateway.telemetry import model_call_span
from aira_gateway.upstreams.base import Upstream, UpstreamError


@dataclass(frozen=True, slots=True)
class Redaction:
    """What a redactor made of the text, and what asking cost."""

    #: The rewritten text, or ``None`` where the redactor cannot be trusted to have produced one.
    #: ``None`` is **not** "nothing to change" — that is the text coming back equal.
    text: str | None
    call: ModelCall | None = None
    #: Why there is no text, for the audit row and the dry run's screen.
    failure: str | None = None


class LlmRedactor:
    """Asks a trusted model to rewrite the text with personal data removed."""

    #: A rewrite shorter than this fraction of the original is a summary, not a thorough redaction.
    #: Placeholders are shorter than what they replace, so some shrinkage is expected.
    MIN_KEPT = 0.34
    #: Below this many characters only an **empty** result is a failure. On a short prompt a ratio
    #: refuses correct redactions (`"say ok"` → `"ok"`) and a summary is indistinguishable from a
    #: rewrite anyway. `MIN_KEPT` is not lowered instead: it is right where it applies.
    MIN_LENGTH_FOR_RATIO = 40

    def __init__(
        self,
        provider: Upstream,
        model: str,
        instruction: str | None = None,
        thinking: Thinking | None = THINKING_OFF,
    ) -> None:
        self._provider = provider
        self._model = model
        self._instruction = instruction or DEFAULT_REDACTION_INSTRUCTION
        self._thinking = thinking

    async def rewrite(self, text: str) -> Redaction:
        if not text.strip():
            # Nothing to redact, so nothing to pay for.
            return Redaction(text)
        try:
            with model_call_span(self._model, purpose="pipeline"):
                response = await self._provider.generate(
                    CanonicalRequest(
                        model=self._model,
                        messages=[
                            CanonicalMessage(role=Role.SYSTEM, text=self._instruction),
                            CanonicalMessage(
                                role=Role.USER,
                                text=f"{REDACTION_OPEN}\n{text}\n{REDACTION_CLOSE}",
                            ),
                        ],
                        max_output_tokens=_redaction_allowance(text),
                        temperature=0.0,
                        thinking=self._thinking,
                    )
                )
        except UpstreamError as exc:
            return Redaction(None, failure=f"the redactor could not be reached ({exc.message})")

        call = ModelCall(step="pii_filter", model=self._model, usage=response.usage)
        # Stripped even though the instruction forbids echoing them: models differ, and the markers
        # must not be sent upstream as text the caller never wrote.
        rewritten = _without_markers(response.text)
        if not rewritten:
            return Redaction(None, call, "the redactor returned nothing")
        given = text.strip()
        if len(given) >= self.MIN_LENGTH_FOR_RATIO and len(rewritten) < len(given) * self.MIN_KEPT:
            # A length this far off is a summary or a refusal; applying it would send the model a
            # different question than the caller asked.
            return Redaction(None, call, "the redactor returned far less text than it was given")
        return Redaction(rewritten, call)


def _without_markers(reply: str) -> str:
    """The rewrite, with the delimiters removed wherever the model echoed them."""
    text = reply.strip()
    if text.startswith(REDACTION_OPEN):
        text = text[len(REDACTION_OPEN) :]
    if text.endswith(REDACTION_CLOSE):
        text = text[: -len(REDACTION_CLOSE)]
    return text.strip()


def _redaction_allowance(text: str) -> int:
    """Room for a rewrite of roughly this length, plus headroom, up to a ceiling.

    Four characters per token is deliberately generous: over-estimating costs a slightly larger
    allowance, under-estimating silently truncates the prompt.
    """
    wanted = max(CLASSIFIER_OUTPUT_TOKENS, len(text) // 4 + REDACTION_OUTPUT_HEADROOM)
    return min(wanted, MAX_REDACTION_OUTPUT_TOKENS)
