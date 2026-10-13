"""Canonical ⇄ the OpenAI wire format (`FRD-123`).

The dialect that Azure OpenAI, Model Garden's self-deployed models and Ollama all speak. Pure
functions, no I/O, and **platform-free**: how a platform addresses a model is its `Routes`
(`ADR-0011`).

    request    canonical → `/v1/chat/completions` and `/v1/embeddings` bodies
    response   answers, stream chunks, streamed tool calls and vectors → canonical

Every difference from the Gemini dialect is a mapping this package owns:

    roles          system/user/model     → system/user/assistant (a flat list, no split-out system)
    output cap     maxOutputTokens       → max_tokens, or the platform's own field (`Routes`)
    attachments    inlineData            → an image_url part carrying a data: URI, **images only**
    usage          usageMetadata.*       → usage.prompt_tokens / completion_tokens; a block
                                           holding no count is no report
    schema         responseSchema        → response_format.json_schema, strict: named, closed,
                                           every property required
    thinking       a token budget        → **reasoning_effort, a level word with no budget**
    reasoning      thought parts         → `reasoning` or `reasoning_content`, two spellings
    stream         one final chunk       → a `[DONE]` sentinel, and usage only if asked for
"""

from aira_gateway.upstreams.openai.mapping.request import (
    DEFAULT_OUTPUT_CAP_FIELD,
    SAMPLING,
    SCHEMA_NAME,
    UNSUPPORTED_STRICT_FIELDS,
    canonical_to_openai,
    canonical_to_openai_embedding,
    schema_refusal,
    strict_schema,
)
from aira_gateway.upstreams.openai.mapping.response import (
    REASONING_FIELDS,
    USAGE_COUNT_FIELDS,
    StreamAssembler,
    StreamedToolCalls,
    _usage_of,
    answer_of,
    embedding_values,
    finish_reason,
    openai_chunk_to_canonical,
    openai_to_canonical,
    reasoning_of,
    reports_usage,
    tool_calls_of,
)

__all__ = [
    "DEFAULT_OUTPUT_CAP_FIELD",
    "REASONING_FIELDS",
    "SAMPLING",
    "SCHEMA_NAME",
    "UNSUPPORTED_STRICT_FIELDS",
    "USAGE_COUNT_FIELDS",
    "StreamAssembler",
    "StreamedToolCalls",
    "_usage_of",
    "answer_of",
    "canonical_to_openai",
    "canonical_to_openai_embedding",
    "embedding_values",
    "finish_reason",
    "openai_chunk_to_canonical",
    "openai_to_canonical",
    "reasoning_of",
    "reports_usage",
    "schema_refusal",
    "strict_schema",
    "tool_calls_of",
]
