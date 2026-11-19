"""A line separator inside an answer is part of the answer, not the end of a stream event.

**What this is about.** Every stream an upstream sends is read line by line, and the line reader
httpx offers breaks lines as `str.splitlines()` does: at CR and LF, and also at U+0085, U+2028,
U+2029 and four control characters. JSON leaves those unescaped inside a string, and the
serialisers most OpenAI-compatible servers use write them raw — vLLM, TGI, any FastAPI server. Text
copied out of a PDF or a Word document carries U+2028 often enough. Such an answer arrived as half
an event that was not JSON: a 502 for the caller — and for Claude, which is read as a stream even
for a caller that did not stream, the same answer asked for and billed in every region first.

The SSE format ends a line at CR, LF or CRLF, and nowhere else. Every test here drives a real
adapter over an `httpx.MockTransport`, or the reader itself.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from aira_common.tokens import StaticTokenSource
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.upstreams.exchange import sse_lines
from aira_gateway.upstreams.openai.adapter import OpenAIAdapter
from aira_gateway.upstreams.openai.transport import OpenAITransport
from aira_gateway.upstreams.vertex.adapters import VertexAnthropicAdapter, VertexGeminiAdapter
from aira_gateway.upstreams.vertex.transport import VertexTransport

#: The characters `str.splitlines()` ends a line at and JSON leaves unescaped in a string. The other
#: four it ends a line at are control characters, which JSON escapes.
SEPARATORS = {"U+2028": "\u2028", "U+2029": "\u2029", "U+0085": "\x85"}

REGIONS = ["europe-west1", "europe-west4"]


def _request(model: str, regions: list[str] | None = None) -> CanonicalRequest:
    return CanonicalRequest(
        model=model,
        messages=[CanonicalMessage(role=Role.USER, text="repeat it")],
        addressing={"regions": regions} if regions else {},
    )


def _vertex(transport: httpx.MockTransport) -> VertexTransport:
    return VertexTransport(
        project="p",
        tokens=StaticTokenSource("t"),
        client=httpx.AsyncClient(transport=transport),
        allowed_regions=tuple(REGIONS),
    )


def _sse(body: str, contacted: list[str] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if contacted is not None:
            contacted.append(request.url.host.split("-aiplatform")[0])
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    return httpx.MockTransport(handler)


def _openai_stream(text: str) -> str:
    """What a pydantic-based server writes: raw UTF-8, escaping only what JSON requires."""
    chunk = '{"choices":[{"index":0,"delta":{"content":"' + text + '"}}]}'
    end = '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}'
    return f"data: {chunk}\n\ndata: {end}\n\ndata: [DONE]\n\n"


def _claude_stream(text: str) -> str:
    events = [
        (
            "message_start",
            '{"type":"message_start","message":{"id":"m","type":"message","role":"assistant",'
            '"content":[],"usage":{"input_tokens":10,"output_tokens":1}}}',
        ),
        (
            "content_block_start",
            '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
        ),
        (
            "content_block_delta",
            '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"'
            + text
            + '"}}',
        ),
        ("content_block_stop", '{"type":"content_block_stop","index":0}'),
        (
            "message_delta",
            '{"type":"message_delta","delta":{"stop_reason":"end_turn"},'
            '"usage":{"output_tokens":5}}',
        ),
        ("message_stop", '{"type":"message_stop"}'),
    ]
    return "".join(f"event: {name}\ndata: {data}\n\n" for name, data in events)


@pytest.mark.parametrize("separator", SEPARATORS.values(), ids=SEPARATORS.keys())
async def test_an_openai_compatible_stream_delivers_the_separator(separator: str) -> None:
    text = f"first{separator}second"
    client = httpx.AsyncClient(base_url="http://vllm.test", transport=_sse(_openai_stream(text)))
    adapter = OpenAIAdapter(OpenAITransport(client=client), ["qwen3"])

    chunks = [chunk async for chunk in adapter.stream_generate(_request("qwen3"))]

    assert "".join(chunk.text_delta for chunk in chunks) == text


@pytest.mark.parametrize("separator", SEPARATORS.values(), ids=SEPARATORS.keys())
async def test_a_gemini_stream_delivers_the_separator(separator: str) -> None:
    text = f"first{separator}second"
    body = (
        'data: {"candidates":[{"content":{"parts":[{"text":"' + text + '"}]},'
        '"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":3,'
        '"candidatesTokenCount":3}}\n\n'
    )
    adapter = VertexGeminiAdapter(_vertex(_sse(body)), [])

    chunks = [chunk async for chunk in adapter.stream_generate(_request("gemini-x", REGIONS))]

    assert "".join(chunk.text_delta for chunk in chunks) == text


@pytest.mark.parametrize("separator", SEPARATORS.values(), ids=SEPARATORS.keys())
async def test_a_claude_answer_with_the_separator_is_asked_for_once(separator: str) -> None:
    """The costly half: the non-streamed Claude answer is read as a stream, and a 502 is a reason
    to ask the next region — so the same answer was requested, and paid for, in every region
    before the caller got a 502 for an answer the first region had given in full."""
    text = f"first{separator}second"
    contacted: list[str] = []
    adapter = VertexAnthropicAdapter(
        _vertex(_sse(_claude_stream(text), contacted)), [], default_max_tokens=1024
    )

    answer = await adapter.generate(_request("claude-sonnet-4-5@20250929", REGIONS))

    assert answer.text == text
    assert contacted == ["europe-west1"], "the same answer was requested again in another region"


#: Every character `str.splitlines()` ends a line at besides CR and LF, between letters.
_OTHERS = "a\u2028b\u2029c\x85d\x0be\x0cf\x1cg\x1dh\x1ei"


class _Reads(httpx.AsyncByteStream):
    """A body arriving in exactly the pieces given, as a network delivers it."""

    def __init__(self, *pieces: bytes) -> None:
        self._pieces = pieces

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for piece in self._pieces:
            yield piece


async def _lines(*pieces: bytes) -> list[str]:
    return [line async for line in sse_lines(httpx.Response(200, stream=_Reads(*pieces)))]


@pytest.mark.parametrize(
    ("pieces", "lines"),
    [
        # Each of the three endings SSE allows, and a mixture.
        ((b"a\nb\n",), ["a", "b"]),
        ((b"a\r\nb\r\n",), ["a", "b"]),
        ((b"a\rb\r",), ["a", "b"]),
        ((b"a\r\n\nb\r\r\nc",), ["a", "", "b", "", "c"]),
        # A CRLF split across two reads is one ending, not a line and an empty one.
        ((b"a\r", b"\nb\n"), ["a", "b"]),
        ((b"a\r", b"\n", b"\nb"), ["a", "", "b"]),
        # A line split across reads is one line.
        ((b"da", b"ta: 1", b"\n"), ["data: 1"]),
        # A character whose bytes arrive in two reads is one character.
        ((b"data: \xe2\x80", b"\xa8x\n"), ["data: \u2028x"]),
        # Everything else `str.splitlines()` breaks at stays inside the line.
        ((f"{_OTHERS}\n".encode(),), [_OTHERS]),
    ],
    ids=[
        "lf",
        "crlf",
        "cr",
        "mixed",
        "crlf-across-reads",
        "cr-then-lf-then-lf",
        "line-across-reads",
        "character-across-reads",
        "no-other-ending",
    ],
)
async def test_a_line_ends_at_cr_lf_or_crlf_and_nowhere_else(
    pieces: tuple[bytes, ...], lines: list[str]
) -> None:
    assert await _lines(*pieces) == lines
