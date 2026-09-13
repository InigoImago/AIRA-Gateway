"""The models a pipeline step asks about a request, one module per question (FRD-300/306/309).

    prompts     instructions, redaction markers, output allowances, and the classifier request
    injection   the injection filter's verdict — heuristic patterns or an LLM
    routing     the router's category
    redaction   the PII filter's rewrite, checked before it is trusted

Every LLM classifier returns the `ModelCall` it made together with its answer, so a step can bill
what it spent even when it goes on to block (`FRD-125`).
"""

from aira_gateway.pipeline.classifiers.injection import (
    BUILTIN_INJECTION_PATTERNS,
    MAX_CUSTOM_PATTERNS,
    MAX_PATTERN_LENGTH,
    MAX_SCANNED_CHARS,
    Classification,
    HeuristicInjectionClassifier,
    InjectionClassifier,
    LlmInjectionClassifier,
    Verdict,
)
from aira_gateway.pipeline.classifiers.prompts import (
    CLASSIFIER_OUTPUT_TOKENS,
    DEFAULT_INJECTION_INSTRUCTION,
    DEFAULT_REDACTION_INSTRUCTION,
    MAX_REDACTION_OUTPUT_TOKENS,
    REDACTION_CLOSE,
    REDACTION_OPEN,
    REDACTION_OUTPUT_HEADROOM,
    ROUTER_INSTRUCTION,
    THINKING_CLASSIFIER_OUTPUT_TOKENS,
    THINKING_OFF,
    classifier_request,
)
from aira_gateway.pipeline.classifiers.redaction import (
    LlmRedactor,
    Redaction,
    _redaction_allowance,
)
from aira_gateway.pipeline.classifiers.routing import LlmCategoryRouter, Routing

__all__ = [
    "BUILTIN_INJECTION_PATTERNS",
    "CLASSIFIER_OUTPUT_TOKENS",
    "DEFAULT_INJECTION_INSTRUCTION",
    "DEFAULT_REDACTION_INSTRUCTION",
    "MAX_CUSTOM_PATTERNS",
    "MAX_PATTERN_LENGTH",
    "MAX_REDACTION_OUTPUT_TOKENS",
    "MAX_SCANNED_CHARS",
    "REDACTION_CLOSE",
    "REDACTION_OPEN",
    "REDACTION_OUTPUT_HEADROOM",
    "ROUTER_INSTRUCTION",
    "THINKING_CLASSIFIER_OUTPUT_TOKENS",
    "THINKING_OFF",
    "Classification",
    "HeuristicInjectionClassifier",
    "InjectionClassifier",
    "LlmCategoryRouter",
    "LlmInjectionClassifier",
    "LlmRedactor",
    "Redaction",
    "Routing",
    "Verdict",
    "_redaction_allowance",
    "classifier_request",
]
