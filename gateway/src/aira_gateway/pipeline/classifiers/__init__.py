"""The models a pipeline step asks about a request, one module per question (FRD-300/306/309).

    prompts     instructions, redaction markers, output allowances, and the classifier request
    injection   the injection filter's verdict — heuristic patterns or an LLM
    routing     the router's category
    redaction   the PII filter's rewrite, checked before it is trusted

Every LLM classifier returns the `ModelCall` it made together with its answer, so a step can bill
what it spent even when it goes on to block (`FRD-125`).
"""

from aira_common.patterns import MAX_PATTERN_LENGTH
from aira_gateway.pipeline.classifiers.injection import (
    BUILTIN_INJECTION_PATTERNS,
    MAX_CUSTOM_PATTERNS,
    MAX_SCANNED_CHARS,
    Classification,
    HeuristicInjectionClassifier,
    InjectionClassifier,
    LlmInjectionClassifier,
    Verdict,
)
from aira_gateway.pipeline.classifiers.prompts import (
    CLASSIFIER_OUTPUT_TOKENS,
    DATA_CLOSE,
    DATA_OPEN,
    DEFAULT_INJECTION_INSTRUCTION,
    DEFAULT_REDACTION_INSTRUCTION,
    MAX_REDACTION_OUTPUT_TOKENS,
    REDACTION_OUTPUT_HEADROOM,
    ROUTER_INSTRUCTION,
    THINKING_CLASSIFIER_OUTPUT_TOKENS,
    THINKING_OFF,
    DataBoundary,
    classifier_request,
)
from aira_gateway.pipeline.classifiers.redaction import LlmRedactor, Redaction
from aira_gateway.pipeline.classifiers.routing import LlmCategoryRouter, Routing

__all__ = [
    "BUILTIN_INJECTION_PATTERNS",
    "CLASSIFIER_OUTPUT_TOKENS",
    "DATA_CLOSE",
    "DATA_OPEN",
    "DEFAULT_INJECTION_INSTRUCTION",
    "DEFAULT_REDACTION_INSTRUCTION",
    "MAX_CUSTOM_PATTERNS",
    "MAX_PATTERN_LENGTH",
    "MAX_REDACTION_OUTPUT_TOKENS",
    "MAX_SCANNED_CHARS",
    "REDACTION_OUTPUT_HEADROOM",
    "ROUTER_INSTRUCTION",
    "THINKING_CLASSIFIER_OUTPUT_TOKENS",
    "THINKING_OFF",
    "Classification",
    "DataBoundary",
    "HeuristicInjectionClassifier",
    "InjectionClassifier",
    "LlmCategoryRouter",
    "LlmInjectionClassifier",
    "LlmRedactor",
    "Redaction",
    "Routing",
    "Verdict",
    "classifier_request",
]
