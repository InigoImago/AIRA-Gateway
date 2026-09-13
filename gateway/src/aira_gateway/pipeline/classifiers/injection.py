"""The injection filter's classifiers: operator-extensible patterns, or an LLM (FRD-300/306).

**A verdict has three values, not two.** An LLM classifier that cannot get an answer — an upstream
error, an empty reply, a reasoning model that spent its allowance thinking — has not found the text
clean. ``UNDETERMINED`` is never folded into ``CLEAN``; what happens next is the step's decision
(`FRD-125`), and by default it blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from aira_common.logging import get_logger
from aira_common.patterns import catastrophic_reason
from aira_gateway.audit import ModelCall
from aira_gateway.core.canonical import Thinking
from aira_gateway.pipeline.classifiers.prompts import (
    DEFAULT_INJECTION_INSTRUCTION,
    THINKING_OFF,
    classifier_request,
)
from aira_gateway.telemetry import model_call_span
from aira_gateway.upstreams.base import Upstream, UpstreamError

_log = get_logger("aira_gateway.pipeline")

#: The built-in phrasings, exposed so the console can show operators what the heuristic catches.
BUILTIN_INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts)",
    r"disregard\s+(the\s+)?(previous|above|system|all)",
    r"forget\s+(all|everything|previous|your)",
    r"you\s+are\s+now\b",
    r"reveal\s+(your\s+)?(the\s+)?(system\s+)?prompt",
    r"developer\s+mode",
    r"jailbreak",
)

#: Bounds on the work one scan can cost (`ADR-0007`). Operator patterns are attacker-adjacent input
#: for a shared process; all three limits are far above any legitimate configuration.
MAX_CUSTOM_PATTERNS = 64
MAX_PATTERN_LENGTH = 256
MAX_SCANNED_CHARS = 20_000


class Verdict(StrEnum):
    """What a classifier concluded — including that it could not conclude anything."""

    INJECTION = "injection"
    CLEAN = "clean"
    #: Asked and no usable answer: an upstream failure, an empty reply, or a reply that is neither
    #: of the two words it was told to use. **Not** clean.
    UNDETERMINED = "undetermined"


@dataclass(frozen=True, slots=True)
class Classification:
    """A verdict and the model call it cost (``None`` when no model was asked).

    They travel together because "what did this step spend" and "what did it decide" are one event,
    and holding them apart is how one of them ends up unrecorded (`FRD-125`).
    """

    verdict: Verdict
    call: ModelCall | None = None
    #: The model's verbatim reply, for the dry run's screen — **never persisted** (`FRD-122` §5.3).
    #: It is what tells "neither word", "both words" and "empty" apart under one *undetermined*.
    reply: str = ""


@runtime_checkable
class InjectionClassifier(Protocol):
    async def classify_text(self, text: str) -> Classification: ...


def _compile(pattern: str) -> re.Pattern[str]:
    """Compile a pattern; fall back to a literal match if it is not valid regex."""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


class HeuristicInjectionClassifier:
    """Pattern-based detection: built-in phrasings plus any operator-supplied patterns."""

    def __init__(self, extra_patterns: tuple[str, ...] = (), *, use_builtins: bool = True) -> None:
        patterns = list(BUILTIN_INJECTION_PATTERNS) if use_builtins else []
        extras = [p for p in extra_patterns if p and len(p) <= MAX_PATTERN_LENGTH]
        # Asked here too, not only where the pattern was authored (`ADR-0018`): the read-model is
        # reachable from Kafka, a seed or a direct write, and `re` has no timeout. A catastrophic
        # pattern is dropped with a log line naming the rule's own reason, like the bounds above —
        # one pattern fewer is degraded, a use case that cannot serve is down.
        safe: list[str] = []
        for pattern in extras[:MAX_CUSTOM_PATTERNS]:
            reason = catastrophic_reason(pattern)
            if reason is not None:
                _log.warning("injection_pattern_rejected", pattern=pattern, reason=reason)
                continue
            safe.append(pattern)
        patterns += safe
        self._compiled = [_compile(pattern) for pattern in patterns]

    async def classify_text(self, text: str) -> Classification:
        """Never ``UNDETERMINED`` and never a model call — which is why it remains the default."""
        scanned = text[:MAX_SCANNED_CHARS]
        if any(pattern.search(scanned) for pattern in self._compiled):
            return Classification(Verdict.INJECTION)
        return Classification(Verdict.CLEAN)

    async def verdict(self, text: str) -> Verdict:
        """The verdict alone, for callers that have nothing to bill."""
        return (await self.classify_text(text)).verdict


class LlmInjectionClassifier:
    """Asks a provider to label the text, and says so when it did not get an answer."""

    def __init__(
        self,
        provider: Upstream,
        model: str,
        instruction: str | None = None,
        thinking: Thinking | None = THINKING_OFF,
    ) -> None:
        self._provider = provider
        self._model = model
        self._instruction = instruction or DEFAULT_INJECTION_INSTRUCTION
        self._thinking = thinking

    async def classify_text(self, text: str) -> Classification:
        try:
            with model_call_span(self._model, purpose="pipeline"):
                response = await self._provider.generate(
                    classifier_request(self._model, self._instruction, text, self._thinking)
                )
        except UpstreamError:
            # No call to report: nothing was served, and no vendor here bills a failed call.
            return Classification(Verdict.UNDETERMINED)
        call = ModelCall(step="injection_filter", model=self._model, usage=response.usage)
        return Classification(self._verdict_of(response.text), call, response.text)

    async def verdict(self, text: str) -> Verdict:
        return (await self.classify_text(text)).verdict

    @staticmethod
    def _verdict_of(text: str) -> Verdict:
        """The one word the instruction asked for, or ``UNDETERMINED``.

        **The answer is one word**, not a substring or a whole-word search: `"SAFE" in "UNSAFE"` is
        true, and `\\bSAFE\\b` matches "not safe" — both read a refusal as clean. Tolerant of what a
        compliant model adds around the word (case, whitespace, punctuation, quotes, markdown
        emphasis) and of nothing else; preamble, a sentence or an empty reply is undetermined, and
        undetermined blocks unless the operator chose otherwise.
        """
        answer = text.strip().strip("*_`\"'“”‘’.!?:;,-— \t\r\n").upper()
        if answer == "INJECTION":
            return Verdict.INJECTION
        if answer == "SAFE":
            return Verdict.CLEAN
        return Verdict.UNDETERMINED
