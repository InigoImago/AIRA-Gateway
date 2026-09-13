"""Canonical ⇄ the OpenAI wire format (`FRD-123`).

The dialect that Azure OpenAI, Model Garden's self-deployed models and Ollama all speak. Pure
functions, no I/O, and **platform-free**: how a platform addresses a model is its `Routes`
(`ADR-0011`).

    request    canonical → `/v1/chat/completions` and `/v1/embeddings` bodies
    response   answers, stream chunks, streamed tool calls and vectors → canonical

Every difference from the Gemini dialect is a mapping this package owns:

    roles          system/user/model     → system/user/assistant (a flat list, no split-out system)
    output cap     maxOutputTokens       → max_tokens
    attachments    inlineData            → an image_url part carrying a data: URI, **images only**
    usage          usageMetadata.*       → usage.prompt_tokens / completion_tokens
    schema         responseSchema        → response_format.json_schema, with a required name
    thinking       a token budget        → **reasoning_effort, a level word with no budget**
    stream         one final chunk       → a `[DONE]` sentinel, and usage only if asked for
"""

from aira_gateway.upstreams.openai.mapping.request import (
    SAMPLING,
    SCHEMA_NAME,
    canonical_to_openai,
    canonical_to_openai_embedding,
)
from aira_gateway.upstreams.openai.mapping.response import (
    REASONING_FIELD,
    StreamedToolCalls,
    _usage_of,
    answer_of,
    embedding_values,
    finish_reason,
    openai_chunk_to_canonical,
    openai_to_canonical,
    parse_sse_line,
    reasoning_of,
    tool_calls_of,
)

__all__ = [
    "REASONING_FIELD",
    "SAMPLING",
    "SCHEMA_NAME",
    "StreamedToolCalls",
    "_usage_of",
    "answer_of",
    "canonical_to_openai",
    "canonical_to_openai_embedding",
    "embedding_values",
    "finish_reason",
    "openai_chunk_to_canonical",
    "openai_to_canonical",
    "parse_sse_line",
    "reasoning_of",
    "tool_calls_of",
]
