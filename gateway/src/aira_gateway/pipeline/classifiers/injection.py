"""The injection filter's classifiers: operator-extensible patterns, or an LLM (FRD-300/306).

**A verdict has three values, not two.** An LLM classifier that cannot get an answer — an upstream
error, an empty reply, a reasoning model that spent its allowance thinking — has not found the text
clean. ``UNDETERMINED`` is never folded into ``CLEAN``; what happens next is the step's decision
(`FRD-125`), and by default it blocks.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from aira_common.logging import get_logger
from aira_common.patterns import MAX_PATTERN_LENGTH, MAX_PATTERNS, catastrophic_reason
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
#: for a shared process. The count and the length are the pattern rule's own, so Management refuses
#: exactly what this filter would drop (`aira_common.patterns`).
MAX_CUSTOM_PATTERNS = MAX_PATTERNS

#: How much of a text one scan reads: its **first and last half** of this when it is longer. Not
#: the whole text, because one unbounded repeat in a pattern costs a search time in the square of
#: what it reads — `[a-z]+!` took 2.35 s over 20 000 letters — and every pattern is searched on the
#: event loop. Both ends rather than the head alone: a head-only window passes anything placed after
#: enough filler. **What lies between the two halves is not scanned** — a stated reach limit
#: (`docs/REQUEST-LIFECYCLE.md`), logged and shown in the dry run whenever a text exceeds it.
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
    #: Whether the model was **reached** — ``False`` only where the call itself failed. This is
    #: what a step's chain moves on for (`FRD-302` FR-8): a model that answered has answered, even
    #: when the answer is *undetermined*, and a chain that tried the next one would be a filter
    #: shopping for a verdict. A field rather than an inference from a missing `ModelCall`: the
    #: heuristic classifier has no call either, and reading that as "not reached" would send it
    #: down a chain it never asked for.
    reached: bool = True
    #: How many characters of the text were left unscanned — the middle of a text longer than
    #: `MAX_SCANNED_CHARS`. Zero when all of it was read.
    unscanned: int = 0


@runtime_checkable
class InjectionClassifier(Protocol):
    async def classify_text(self, text: str) -> Classification: ...


def _compile(pattern: str) -> re.Pattern[str]:
    """Compile a pattern; fall back to a literal match if it is not valid regex."""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return re.compile(re.escape(pattern), re.IGNORECASE)


def _scanned(text: str) -> tuple[str, ...]:
    """The parts of ``text`` a scan reads: all of it, or its first and last halves of the bound.

    Each part is searched on its own: joined, a match could straddle the gap and find text the
    caller never wrote.
    """
    if len(text) <= MAX_SCANNED_CHARS:
        return (text,)
    half = MAX_SCANNED_CHARS // 2
    return text[:half], text[-half:]


@functools.lru_cache(maxsize=1024)
def _dropped(pattern: str, why: str) -> None:
    """Say once per process that a configured pattern is not used, and why.

    The classifier is built for every request, and a dropped pattern is a filter that shows as
    configured while matching less than it says — worth one line, not one per request.
    """
    _log.warning(
        "injection_pattern_rejected",
        pattern=pattern[:MAX_PATTERN_LENGTH],
        length=len(pattern),
        reason=why,
    )


class HeuristicInjectionClassifier:
    """Pattern-based detection: built-in phrasings plus any operator-supplied patterns."""

    def __init__(self, extra_patterns: tuple[str, ...] = (), *, use_builtins: bool = True) -> None:
        patterns = list(BUILTIN_INJECTION_PATTERNS) if use_builtins else []
        # Asked here too, not only where the pattern was authored (`ADR-0018`): the read-model is
        # reachable from Kafka, a seed or a direct write, and `re` has no timeout. A pattern past a
        # bound, or one that can backtrack catastrophically, is dropped with a log line naming why
        # — one pattern fewer is degraded, a use case that cannot serve is down.
        safe: list[str] = []
        for pattern in (p for p in extra_patterns if p):
            if len(pattern) > MAX_PATTERN_LENGTH:
                _dropped(pattern, f"longer than {MAX_PATTERN_LENGTH} characters")
            elif len(safe) >= MAX_CUSTOM_PATTERNS:
                _dropped(pattern, f"more than {MAX_CUSTOM_PATTERNS} custom patterns")
            elif (reason := catastrophic_reason(pattern)) is not None:
                _dropped(pattern, reason)
            else:
                safe.append(pattern)
        patterns += safe
        self._compiled = [_compile(pattern) for pattern in patterns]

    async def classify_text(self, text: str) -> Classification:
        """Never ``UNDETERMINED`` and never a model call — which is why it remains the default."""
        parts = _scanned(text)
        unscanned = len(text) - sum(len(part) for part in parts)
        if unscanned:
            # Not silent: the reach limit is stated, and every text it applies to says so.
            _log.info("injection_scan_partial", length=len(text), unscanned=unscanned)
        if any(pattern.search(part) for part in parts for pattern in self._compiled):
            return Classification(Verdict.INJECTION, unscanned=unscanned)
        return Classification(Verdict.CLEAN, unscanned=unscanned)

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
            # No call to report: nothing was served, and no vendor here bills a failed call. The
            # verdict is still undetermined — a step whose chain is exhausted decides on it.
            return Classification(Verdict.UNDETERMINED, reached=False)
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
