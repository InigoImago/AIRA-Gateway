"""A failure of this gateway's own mid-stream ends the stream as an upstream's failure does.

Once a stream's `200` is out, a failure can only travel inside it: Google's error element as the
stream's last, a KIRA stream without its `completed` event. An upstream that broke off ended that
way — and a failure of this gateway's own did not. An adapter tripping over a chunk it did not
expect, or a defect anywhere on the way, ran out of the response's body to the server, which cut
the connection: the caller's client reported a protocol error, a stream that stops mid-body, and
nothing it could show its user. The row was written, so the defect was in the books and invisible
to the caller.

Now it ends the stream with the error the surface would have sent as a status — `500`, `INTERNAL`
— the row records it as this gateway's (`500` / `internal_error`), what the vendor reported is
settled, and where it had reported nothing the vendor is asked to count, because this gateway
stopped reading, not the vendor answering. The failure is still everyone's to see: logged with its
traceback as an unhandled error, and marked on the request's trace.

Run on real sockets, because a cut connection is a thing only a socket shows: `TestClient` collects
the body whole and raises the exception into the test instead.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx
import pytest
import uvicorn
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from aira_gateway.api.serving import streaming
from aira_gateway.api.serving.streaming import StreamAccounting
from aira_gateway.app import create_app
from aira_gateway.audit import AuditTrail
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalMessage,
    CanonicalRequest,
    CanonicalUsage,
    Role,
)
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.pipeline.dispatch import Streaming
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

MODEL = "trips-1"

#: Each streamed verb as a client calls it, and how its stream is read back.
STREAMS = {
    "gemini-sse": (f"/v1beta/models/{MODEL}:streamGenerateContent?alt=sse", "gemini"),
    "gemini-array": (f"/v1beta/models/{MODEL}:streamGenerateContent", "gemini"),
    "kira": ("/kira/api/external/streaming-chat", "kira"),
}
BODIES = {
    "gemini": {"contents": [{"role": "user", "parts": [{"text": "hallo"}]}]},
    "kira": {"request": {"parts": [{"text": "hallo"}]}, "model_id": 1},
}


class _Trips:
    """An adapter that reads one chunk and trips over the next: a defect, not the vendor's error."""

    is_test_double = True

    def __init__(self, *, reported: bool = True) -> None:
        self.reported = reported
        self.counted: list[str] = []

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel(MODEL, "1", ("generateContent", "streamGenerateContent"))]

    async def generate(self, request: CanonicalRequest) -> Any:
        raise NotImplementedError

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        usage = CanonicalUsage(prompt_tokens=3, completion_tokens=2) if self.reported else None
        yield CanonicalChunk(text_delta="Die erste Hälfte", usage=usage)
        raise KeyError("candidates")

    async def count_tokens(self, request: CanonicalRequest, *, region: str = "") -> int:
        said = " ".join(message.text for message in request.messages)
        self.counted.append(said)
        return 11

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


@contextmanager
def _database(directory: Path) -> Iterator[None]:
    """A SQLite file, one connection per session, so the rows a served request wrote can be read
    while the server runs."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{directory / 'gateway.db'}", poolclass=NullPool
    )
    with mock.patch("aira_gateway.app.build_engine", return_value=engine):
        yield


async def _streamed(tmp_path: Path, case: str, provider: _Trips) -> tuple[Any, tuple[Any, ...]]:
    """One streamed request over a real socket: what the client read, and the request's row."""
    path, surface = STREAMS[case]
    with _database(tmp_path):
        app = create_app(
            GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0)
        )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical"))
    serving = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 — uvicorn offers no event to wait on
        await asyncio.sleep(0.01)
    app.state.providers = ProviderRegistry([provider])
    async with app.state.db_sessionmaker() as session:
        session.add(ModelRead(model=MODEL, numeric_id=1, approved=True, capabilities=["generate"]))
        await session.commit()
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            try:
                response = await client.post(path, json=BODIES[surface])
            except httpx.TransportError as exc:
                read: Any = f"the connection was cut: {type(exc).__name__}"
            else:
                read = (response.status_code, response.text)
        async with app.state.db_sessionmaker() as session:
            (row,) = (await session.execute(select(RequestLog))).scalars()
    finally:
        server.should_exit = True
        await serving
    return read, (
        row.status,
        row.outcome,
        row.prompt_tokens,
        row.completion_tokens,
        row.usage_basis,
    )


def _events(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data:")
    ]


@pytest.mark.parametrize("case", sorted(STREAMS))
async def test_a_defect_after_the_first_chunk_ends_the_stream_as_a_failure(
    tmp_path: Path, case: str
) -> None:
    read, row = await _streamed(tmp_path, case, _Trips())

    assert not isinstance(read, str), read
    status, text = read
    assert status == 200
    if case == "gemini-sse":
        *answer, last = _events(text)
        assert answer[0]["candidates"][0]["content"]["parts"][0]["text"] == "Die erste Hälfte"
        assert (last["error"]["code"], last["error"]["status"]) == (500, "INTERNAL")
    elif case == "gemini-array":
        *answer, last = json.loads(text)
        assert answer[0]["candidates"][0]["content"]["parts"][0]["text"] == "Die erste Hälfte"
        assert (last["error"]["code"], last["error"]["status"]) == (500, "INTERNAL")
    else:
        events = _events(text)
        assert [(event["status"], event["data"]) for event in events] == [
            ("update", "Die erste Hälfte")
        ], "no `completed`: the answer is not whole"
    # This gateway's failure, with what the vendor had reported settled as the floor it is.
    assert row == (500, "internal_error", 3, 2, "partial")


async def test_a_defect_before_the_vendor_reported_is_counted_at_the_vendor(
    tmp_path: Path,
) -> None:
    """This gateway stopped reading; the vendor did not stop answering, and bills for it. With
    nothing reported, the request and the answer received are counted there, as for a caller who
    left — or the request is booked as free."""
    provider = _Trips(reported=False)

    read, row = await _streamed(tmp_path, "gemini-sse", provider)

    assert not isinstance(read, str), read
    assert provider.counted == ["hallo", "Die erste Hälfte"]
    assert row == (500, "internal_error", 11, 11, "counted")


class _Log:
    """What the module's logger is asked to write, by event."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, dict[str, Any]]] = []

    def error(self, event: str, **fields: Any) -> None:
        self.lines.append((event, fields))


def test_a_defect_mid_stream_is_logged_and_on_the_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Handled, the defect no longer reaches the server, which logged it with its traceback, nor the
    tracing middleware, which marked the request's span. So it says both itself."""
    log = _Log()
    monkeypatch.setattr(streaming, "_log", log)
    exporter = InMemorySpanExporter()
    tracing = TracerProvider()
    tracing.add_span_processor(SimpleSpanProcessor(exporter))
    sent = CanonicalRequest(model=MODEL, messages=[CanonicalMessage(role=Role.USER, text="hi")])
    stream = StreamAccounting(
        # Only what the failure reads of the request: where it was sent, and its state.
        SimpleNamespace(
            url=SimpleNamespace(path="/stream"), method="POST", state=SimpleNamespace()
        ),  # type: ignore[arg-type]
        AuditTrail(operation="streamGenerateContent", api="gemini"),
        SimpleNamespace(model=MODEL, canonical=None, notices=()),  # type: ignore[arg-type]
        Streaming(model=MODEL, candidate_index=0, chunks=_nothing(), sent=sent),
        api="gemini",
        operation="streamGenerateContent",
        started=0.0,
    )
    try:
        raise KeyError("candidates")
    except KeyError as defect:
        with tracing.get_tracer("test").start_as_current_span("POST"):
            stream.broke_off(defect)

    (span,) = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert [event.name for event in span.events] == ["exception"]
    ((event, fields),) = log.lines
    assert (event, fields["error_type"]) == ("unhandled_error", "KeyError")
    assert isinstance(fields["exc_info"], KeyError), "logged with its traceback"


async def _nothing() -> AsyncIterator[CanonicalChunk]:
    return
    yield  # pragma: no cover - makes this an async generator
