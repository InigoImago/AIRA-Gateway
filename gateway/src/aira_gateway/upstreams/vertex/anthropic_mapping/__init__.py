"""Canonical ⇄ the Anthropic Messages API, as Vertex serves it (`FRD-119`).

Pure functions, no I/O, tested without HTTP — the same shape as `gemini_mapping`.

    request    canonical → a Messages body: roles, system prompt, thinking, tools, caching
    schema     our response schema in the shape this API requires, and what it cannot express
    response   answers and the streamed event sequence → canonical

Every difference from the Gemini dialect is a mapping this package owns:

    roles          user/model            → user/assistant
    system prompt  a content             → a top-level parameter (concatenated when several)
    output cap     optional              → **required**
    usage          usageMetadata.*       → usage.input_tokens / output_tokens, plus cache counts
    thinking       a budget or a level   → a budget only; thinking blocks returned only on request
    stop reason    finishReason          → stop_reason, with `max_tokens` meaning truncation
"""

from aira_gateway.upstreams.vertex.anthropic_mapping.request import (
    ANTHROPIC_VERSION,
    SAMPLING,
    canonical_to_anthropic,
)
from aira_gateway.upstreams.vertex.anthropic_mapping.response import (
    SCHEMA_UNSATISFIED,
    StreamAssembler,
    answer_text,
    anthropic_to_canonical,
    finish_reason,
    reasoning_text,
    structured_document,
    tool_calls_of,
    usage_of,
)
from aira_gateway.upstreams.vertex.anthropic_mapping.schema import (
    UNSUPPORTED_SCHEMA_FIELDS,
    schema_for_anthropic,
    schema_refusal,
)

__all__ = [
    "ANTHROPIC_VERSION",
    "SAMPLING",
    "SCHEMA_UNSATISFIED",
    "UNSUPPORTED_SCHEMA_FIELDS",
    "StreamAssembler",
    "answer_text",
    "anthropic_to_canonical",
    "canonical_to_anthropic",
    "finish_reason",
    "reasoning_text",
    "schema_for_anthropic",
    "schema_refusal",
    "structured_document",
    "tool_calls_of",
    "usage_of",
]
