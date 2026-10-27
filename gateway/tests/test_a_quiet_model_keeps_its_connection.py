"""A streamed answer never leaves its connection silent for long (`ADR-0046`).

**What went wrong.** Claude, asked about a large context, answered nothing for minutes — it read the
context, it thought, it wrote a function call's arguments — and the callers' streams ended as `499`
after a fixed while. The gateway had sent them nothing at all: a stream's status waited for the
first chunk (`ADR-0032`), and for the Anthropic dialect the first chunk is the first *visible* word,
after every thought and every argument. A proxy that sees no byte for its idle timeout — nginx's is
sixty seconds — or a client that sees no headers for its own closes the connection, and the row
then said the caller went away. KIRA's keepalive (`FRD-146` FR-19) did not help: it started after
the first chunk, which is after the silence it was written for.

So the property is about the wire, and every case here watches the wire:

- the status goes out after `AIRA_STREAM_KEEPALIVE_SECONDS` although no model has said a word, and
  a quiet stream then sends what its surface's clients skip, before the first chunk and after it;
- the chain still falls back until a model has produced a chunk, and a refusal it ends in after
  the status went out is the stream's last element, recorded like any other refusal;
- a refusal that comes in time is still a status, and `0` switches all of it off.

**No stopwatch decides anything.** A quiet model speaks only when the test tells it to, and the test
tells it once it has *seen* the status and the keepalives go out. A gateway that holds its status
until the model speaks therefore never gets there, and the case ends at `STALL_SECONDS` saying what
it was waiting for — the same handshake `test_streams_actually_stream.py` uses, for the same reason.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.datastructures import State

from aira_gateway.api.serving import Opening, stream_failure_status
from aira_gateway.app import create_app
from aira_gateway.audit import AuditTrail
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import (
    CanonicalChunk,
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
)
from aira_gateway.db.models import ModelRead, RequestLog
from aira_gateway.explaining import MODEL_HEADER, TRAIL_STATE, withhold
from aira_gateway.middleware import RequestIdMiddleware
from aira_gateway.pipeline.config import Pipeline
from aira_gateway.pipeline.dispatch import NoCapableModel, Streaming
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import DialectUnsupported, ProviderRegistry, UpstreamError
from aira_gateway.upstreams.base import UpstreamModel as Model

#: How long a correct case may take before it is called stalled. A liveness bound, not a
#: measurement: nothing waits on a clock below except the keepalive interval itself.
STALL_SECONDS = 10.0

#: The interval the cases run with: short, so the handshake below completes at once.
QUICK = 0.05

QUIET, QUICK_MODEL = "quiet-1", "quick-1"
#: The predecessor addresses a model by number.
NUMBERS = {QUIET: 9101, QUICK_MODEL: 9102}

_USAGE = CanonicalUsage(prompt_tokens=7, completion_tokens=2)

GEMINI_SSE = f"/v1beta/models/{QUIET}:streamGenerateContent?alt=sse"
GEMINI_ARRAY = f"/v1beta/models/{QUIET}:streamGenerateContent"
KIRA = "/kira/api/external/streaming-chat"
GEMINI_BODY = {"contents": [{"role": "user", "parts": [{"text": "read the repository"}]}]}
KIRA_BODY = {"request": {"parts": [{"text": "read the repository"}]}, "model_id": NUMBERS[QUIET]}

#: What each surface sends while its stream is quiet, as its own clients skip it.
GEMINI_KEEPALIVE = b"\n"
KIRA_KEEPALIVE = b": ping\n\n"


class _Quiet:
    """A model that says nothing until told to — as one reading a large context, or thinking.

    ``pause_after_first`` says one word first and then falls quiet, as a model does between a
    sentence and the function call it goes on to write. ``fails`` is the status it fails with when
    it is told to speak, instead of answering.
    """

    is_test_double = True

    def __init__(
        self, name: str, *, fails: int | None = None, pause_after_first: bool = False
    ) -> None:
        self.name = name
        self.speak = asyncio.Event()
        self.asked = asyncio.Event()
        self.cancelled = False
        self._fails = fails
        self._pause_after_first = pause_after_first

    def models(self) -> list[Model]:
        return [Model(self.name, self.name, ("generateContent", "streamGenerateContent"), "acme")]

    async def generate(self, request: CanonicalRequest) -> CanonicalResponse:
        return CanonicalResponse(model=self.name, text="whole", usage=_USAGE)

    async def stream_generate(self, request: CanonicalRequest) -> AsyncIterator[CanonicalChunk]:
        self.asked.set()
        try:
            if self._pause_after_first:
                yield CanonicalChunk(text_delta=f"{self.name} says ")
            await self.speak.wait()
            if self._fails is not None:
                raise UpstreamError(f"{self.name} is unavailable", self._fails)
            yield CanonicalChunk(text_delta=f"{self.name} answers")
            yield CanonicalChunk(text_delta="", usage=_USAGE, finish_reason="stop")
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def embed(self, request: object) -> list[list[float]]:
        return [[0.0]]


class _Quick(_Quiet):
    """A model that answers at once."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.speak.set()


class _FixedStore:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    async def get(self, use_case: str | None) -> Pipeline:
        return self._pipeline


@dataclass
class _Wire:
    """What left the application, in the order it left."""

    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    bodies: list[bytes] = field(default_factory=list)

    def keepalives(self, keepalive: bytes) -> int:
        return sum(1 for body in self.bodies if body == keepalive)

    @property
    def text(self) -> str:
        return b"".join(self.bodies).decode()


def _app(*providers: _Quiet, chain: tuple[str, ...] = (), **settings: Any) -> Any:
    app = create_app(
        GatewaySettings(
            auth_required=False,
            require_use_case=False,
            log_queue_size=0,
            # Nothing read on after a caller left, unless a case is about that: a case that
            # stalls is cancelled like a caller who left, and reading a quiet model on would hold
            # it for the whole bound instead of failing at `STALL_SECONDS` with its message.
            **{"stream_keepalive_seconds": QUICK, "drain_left_streams_seconds": 0, **settings},
        )
    )
    registry = ProviderRegistry(list(providers))
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    app.state.pipeline_store = _FixedStore(Pipeline(fallback_models=chain))
    return app


@pytest.fixture
def running() -> Any:
    """A started application whose catalogue knows both models; the case installs the adapters."""

    def start(app: Any) -> TestClient:
        client = TestClient(app)
        client.__enter__()

        async def seed() -> None:
            async with app.state.db_sessionmaker() as session:
                for model, number in NUMBERS.items():
                    session.add(
                        ModelRead(
                            model=model, numeric_id=number, approved=True, capabilities=["generate"]
                        )
                    )
                await session.commit()

        client.portal.call(seed)  # type: ignore[attr-defined]
        return client

    clients: list[TestClient] = []

    def started(app: Any) -> TestClient:
        client = start(app)
        clients.append(client)
        return client

    yield started
    for client in clients:
        client.__exit__(None, None, None)


async def _drive(
    app: Any,
    url: str,
    body: dict[str, Any],
    *,
    until: Callable[[_Wire], bool],
    then: Callable[[], None],
    leave: asyncio.Event | None = None,
) -> _Wire:
    """Drive the application as uvicorn does and watch what leaves it.

    ``then`` runs once ``until`` holds for what has left so far — typically: let the model speak.
    A gateway that waits for the model before it sends anything never satisfies ``until``, so the
    model never speaks and the case stalls with a message, rather than passing slowly. ``leave``
    is the caller going away: the next `receive` after it is set is `http.disconnect`.
    """
    path, _, query = url.partition("?")
    payload = json.dumps(body).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode()),
        ],
        "client": ("127.0.0.1", 32768),
        "server": ("testserver", 80),
    }
    wire = _Wire()
    done = False
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
        if leave is None:
            await asyncio.sleep(3600)
        else:
            await leave.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        nonlocal done
        if message["type"] == "http.response.start":
            wire.status = int(message["status"])
            wire.headers = {
                name.decode().lower(): value.decode() for name, value in message["headers"]
            }
        elif message["type"] == "http.response.body" and message.get("body"):
            wire.bodies.append(bytes(message["body"]))
        if not done and until(wire):
            done = True
            then()

    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=STALL_SECONDS)
    except TimeoutError:
        raise AssertionError(
            f"stalled with status {wire.status} and {len(wire.bodies)} piece(s) on the wire: "
            f"{wire.bodies[:6]!r}. The model is waiting to be told to speak, which happens once "
            "the gateway has sent its status and kept the stream alive — a gateway that waits for "
            "the model first never gets there."
        ) from None
    return wire


def _sse_events(wire: _Wire) -> list[dict[str, Any]]:
    """Every `data:` event of an SSE body, parsed — the way a client that follows the format
    reads it, skipping what is not an event."""
    return [
        json.loads(line[len("data: ") :])
        for line in wire.text.splitlines()
        if line.startswith("data: ")
    ]


def _gemini_text(events: list[dict[str, Any]]) -> str:
    return "".join(
        part.get("text", "")
        for event in events
        for candidate in event.get("candidates") or []
        for part in (candidate.get("content") or {}).get("parts") or []
    )


async def _rows(app: Any) -> list[RequestLog]:
    async with app.state.db_sessionmaker() as session:
        return list((await session.execute(select(RequestLog))).scalars())


def _status_and(keepalive: bytes, count: int) -> Callable[[_Wire], bool]:
    """The status is out, and ``count`` keepalives with it — and nothing the model said."""
    return lambda wire: wire.status is not None and wire.keepalives(keepalive) >= count


# == the status goes out, and the stream is kept alive ==========================================


def test_a_gemini_stream_begins_before_the_model_says_anything(running: Any) -> None:
    quiet = _Quiet(QUIET)
    app = _app(quiet)
    client = running(app)

    wire = client.portal.call(  # type: ignore[attr-defined]
        lambda: _drive(
            app,
            GEMINI_SSE,
            GEMINI_BODY,
            until=_status_and(GEMINI_KEEPALIVE, 2),
            then=quiet.speak.set,
        )
    )

    assert wire.status == 200
    # Whatever went out before the model spoke was a bare line, which no SSE client dispatches.
    first_word = next(i for i, body in enumerate(wire.bodies) if body != GEMINI_KEEPALIVE)
    assert first_word >= 2 and wire.bodies[:first_word] == [GEMINI_KEEPALIVE] * first_word
    assert _gemini_text(_sse_events(wire)) == f"{QUIET} answers"
    # Nobody had answered when the headers went out, so they name nobody.
    assert "x-aira-model" not in wire.headers
    assert wire.headers.get("x-accel-buffering") == "no"
    [row] = client.portal.call(_rows, app)  # type: ignore[attr-defined]
    assert (row.status, row.outcome, row.model) == (200, "served", QUIET)
    assert (row.prompt_tokens, row.completion_tokens) == (7, 2)


def test_the_gemini_array_stream_stays_one_json_document(running: Any) -> None:
    """Without `alt=sse` the stream is one JSON array; the keepalive is whitespace inside it."""
    quiet = _Quiet(QUIET)
    app = _app(quiet)
    client = running(app)

    wire = client.portal.call(  # type: ignore[attr-defined]
        lambda: _drive(
            app,
            GEMINI_ARRAY,
            GEMINI_BODY,
            until=_status_and(GEMINI_KEEPALIVE, 2),
            then=quiet.speak.set,
        )
    )

    assert wire.status == 200
    assert wire.bodies[0] == b"["
    assert _gemini_text(json.loads(wire.text)) == f"{QUIET} answers"


def test_a_kira_stream_begins_before_the_model_says_anything(running: Any) -> None:
    """The keepalive `FRD-146` FR-19 promised — now from the moment the model is asked."""
    quiet = _Quiet(QUIET)
    app = _app(quiet)
    client = running(app)

    wire = client.portal.call(  # type: ignore[attr-defined]
        lambda: _drive(
            app, KIRA, KIRA_BODY, until=_status_and(KIRA_KEEPALIVE, 2), then=quiet.speak.set
        )
    )

    assert wire.status == 200
    events = _sse_events(wire)
    assert [event["status"] for event in events][-1] == "completed"
    assert "".join(e["data"] for e in events if e["status"] == "update") == f"{QUIET} answers"
    [row] = client.portal.call(_rows, app)  # type: ignore[attr-defined]
    assert (row.status, row.outcome, row.model) == (200, "served", QUIET)


def test_a_gemini_stream_that_falls_quiet_after_its_first_word_is_kept_alive(
    running: Any,
) -> None:
    """Between a sentence and the function call a model goes on to write, nothing is said either —
    the KIRA surface sent its comments there already, the Gemini one sent nothing."""
    quiet = _Quiet(QUIET, pause_after_first=True)
    app = _app(quiet)
    client = running(app)

    def said_and_then_kept_alive(wire: _Wire) -> bool:
        spoke = next((i for i, body in enumerate(wire.bodies) if body.startswith(b"data:")), None)
        return spoke is not None and wire.bodies[spoke + 1 :].count(GEMINI_KEEPALIVE) >= 2

    wire = client.portal.call(  # type: ignore[attr-defined]
        lambda: _drive(
            app, GEMINI_SSE, GEMINI_BODY, until=said_and_then_kept_alive, then=quiet.speak.set
        )
    )

    assert _gemini_text(_sse_events(wire)) == f"{QUIET} says {QUIET} answers"


# == the chain goes on, and its ending is recorded ===============================================


@pytest.mark.parametrize(
    ("url", "body", "keepalive"),
    [(GEMINI_SSE, GEMINI_BODY, GEMINI_KEEPALIVE), (KIRA, KIRA_BODY, KIRA_KEEPALIVE)],
    ids=["gemini", "kira"],
)
def test_the_chain_still_falls_back_after_the_status_went_out(
    running: Any, url: str, body: dict[str, Any], keepalive: bytes
) -> None:
    """Nothing of any model had reached the caller, so the next candidate may still answer."""
    quiet = _Quiet(QUIET, fails=503)
    quick = _Quick(QUICK_MODEL)
    app = _app(quiet, quick, chain=(QUICK_MODEL,))
    client = running(app)

    wire = client.portal.call(  # type: ignore[attr-defined]
        lambda: _drive(app, url, body, until=_status_and(keepalive, 2), then=quiet.speak.set)
    )

    assert wire.status == 200
    assert f"{QUICK_MODEL} answers" in wire.text
    if url != KIRA:
        # Google's field for the model that produced a chunk: the fallback, on every chunk.
        assert {event.get("modelVersion") for event in _sse_events(wire)} == {QUICK_MODEL}
    [row] = client.portal.call(_rows, app)  # type: ignore[attr-defined]
    assert (row.status, row.outcome) == (200, "served")
    # The model that answered — the fallback — not the one asked first.
    assert (row.model, row.model_selection) == (QUICK_MODEL, "fallback:1")
    assert {"step": "dispatch", "action": "failed", "to": QUIET} in [
        {key: decision.get(key) for key in ("step", "action", "to")}
        for decision in row.pipeline_decisions or []
    ]


@pytest.mark.parametrize(
    ("url", "body", "keepalive"),
    [(GEMINI_SSE, GEMINI_BODY, GEMINI_KEEPALIVE), (KIRA, KIRA_BODY, KIRA_KEEPALIVE)],
    ids=["gemini", "kira"],
)
def test_a_chain_that_runs_out_after_the_status_ends_the_stream_and_is_recorded(
    running: Any, url: str, body: dict[str, Any], keepalive: bytes
) -> None:
    """The refusal arrives after the `200`: on Gemini Google's error element ends the stream, which
    its SDKs raise; on KIRA the stream ends without `completed`, as the predecessor's does. The row
    is the refusal's, with the upstream's status and the candidate that failed."""
    quiet = _Quiet(QUIET, fails=503)
    app = _app(quiet)
    client = running(app)

    wire = client.portal.call(  # type: ignore[attr-defined]
        lambda: _drive(app, url, body, until=_status_and(keepalive, 2), then=quiet.speak.set)
    )

    assert wire.status == 200
    events = _sse_events(wire)
    if url == KIRA:
        assert "completed" not in [event.get("status") for event in events]
    else:
        error = events[-1]["error"]
        assert (error["code"], error["status"]) == (503, "UNAVAILABLE")
        assert QUIET in error["message"]
    [row] = client.portal.call(_rows, app)  # type: ignore[attr-defined]
    assert (row.status, row.outcome) == (503, "upstream_error")
    assert QUIET in (row.error or "")
    assert {"step": "dispatch", "action": "failed", "to": QUIET} in [
        {key: decision.get(key) for key in ("step", "action", "to")}
        for decision in row.pipeline_decisions or []
    ]


@pytest.mark.parametrize(
    ("url", "body", "status"),
    [(GEMINI_SSE, GEMINI_BODY, 503), (KIRA, KIRA_BODY, 502)],
    ids=["gemini", "kira"],
)
def test_a_refusal_that_comes_in_time_is_still_a_status(
    running: Any, url: str, body: dict[str, Any], status: int
) -> None:
    """What `ADR-0032` gained stays: a stream nothing could serve answers with a status, as long
    as the chain decided before the status had to go out."""
    quiet = _Quiet(QUIET, fails=503)
    quiet.speak.set()
    # An interval no failure in this case comes near, so the status is the chain's to decide.
    app = _app(quiet, stream_keepalive_seconds=5)
    client = running(app)

    wire = client.portal.call(  # type: ignore[attr-defined]
        lambda: _drive(app, url, body, until=lambda wire: False, then=lambda: None)
    )

    assert wire.status == status


def test_a_caller_who_leaves_before_the_first_word_is_read_to_the_end(running: Any) -> None:
    """The caller left while the model was still quiet: the chain is not dropped with them — the
    answer is read to its end for the vendor's own figure (`ADR-0041`), and the model that opened
    it is the one the row names."""
    quiet = _Quiet(QUIET)
    app = _app(quiet, drain_left_streams_seconds=5)
    client = running(app)
    leave = asyncio.Event()

    def go_away() -> None:
        leave.set()
        # Told to speak only after the caller has gone: nothing of it reaches anybody.
        asyncio.get_running_loop().call_soon(quiet.speak.set)

    wire = client.portal.call(  # type: ignore[attr-defined]
        lambda: _drive(
            app,
            GEMINI_SSE,
            GEMINI_BODY,
            until=_status_and(GEMINI_KEEPALIVE, 1),
            then=go_away,
            leave=leave,
        )
    )

    assert not _sse_events(wire)
    [row] = client.portal.call(_rows, app)  # type: ignore[attr-defined]
    assert (row.status, row.outcome, row.model) == (499, "client_gone", QUIET)
    assert (row.prompt_tokens, row.completion_tokens) == (7, 2)


def test_a_caller_who_leaves_with_nothing_read_on_stops_the_chain(running: Any) -> None:
    """With nothing read on (`AIRA_DRAIN_LEFT_STREAMS_SECONDS=0`) the chain still looking is
    stopped with its caller, not left running for a reader that will never come."""
    quiet = _Quiet(QUIET)
    app = _app(quiet, drain_left_streams_seconds=0)
    client = running(app)
    leave = asyncio.Event()

    client.portal.call(  # type: ignore[attr-defined]
        lambda: _drive(
            app,
            GEMINI_SSE,
            GEMINI_BODY,
            until=_status_and(GEMINI_KEEPALIVE, 1),
            then=leave.set,
            leave=leave,
        )
    )

    assert quiet.cancelled
    [row] = client.portal.call(_rows, app)  # type: ignore[attr-defined]
    assert (row.status, row.outcome) == (499, "client_gone")
    assert row.completion_tokens is None


# == switched off ==================================================================================


@pytest.mark.parametrize(("url", "body"), [(GEMINI_SSE, GEMINI_BODY), (KIRA, KIRA_BODY)])
def test_zero_waits_for_the_first_word_and_sends_nothing_meanwhile(
    running: Any, url: str, body: dict[str, Any]
) -> None:
    """`AIRA_STREAM_KEEPALIVE_SECONDS=0` is the behaviour before `ADR-0046`, for an installation
    whose clients cannot take a keepalive: no status before the model speaks, and silence."""
    quiet = _Quiet(QUIET)
    app = _app(quiet, stream_keepalive_seconds=0)
    client = running(app)
    seen_before_speaking: list[int | None] = []

    async def speak_once_asked() -> None:
        await quiet.asked.wait()
        # Ample time for a status or a keepalive at the interval the other cases run with.
        await asyncio.sleep(QUICK * 6)
        seen_before_speaking.append(wire_status())
        quiet.speak.set()

    wire_holder: list[_Wire] = []

    def wire_status() -> int | None:
        return wire_holder[0].status if wire_holder else None

    async def scenario() -> _Wire:
        speaker = asyncio.ensure_future(speak_once_asked())

        def watching(wire: _Wire) -> bool:
            if not wire_holder:
                wire_holder.append(wire)
            return False

        wire = await _drive(app, url, body, until=watching, then=lambda: None)
        await speaker
        return wire

    wire = client.portal.call(scenario)  # type: ignore[attr-defined]

    assert seen_before_speaking == [None], "the status went out before the model said anything"
    assert wire.status == 200
    assert GEMINI_KEEPALIVE not in wire.bodies and KIRA_KEEPALIVE not in wire.bodies


# == the parts =====================================================================================


@pytest.mark.parametrize(
    "refusal",
    [NoCapableModel([]), DialectUnsupported("no field for it")],
    ids=["no-capable-model", "dialect"],
)
def test_a_chain_that_had_nothing_to_offer_is_recorded_as_the_400_it_would_have_been(
    refusal: Exception,
) -> None:
    """After the status went out the caller is sent no status, so the row records the one both
    surfaces answer the same refusal with when it comes in time — not a 502 nobody received."""
    assert stream_failure_status(refusal) == 400


def test_a_withheld_header_is_not_sent_even_where_the_trail_would_name_a_model() -> None:
    """The middleware adds `X-AIRA-Model` from the trail; a route that withheld it wins."""
    trail = AuditTrail(operation="streamGenerateContent", api="gemini", requested_model=QUIET)
    trail.routed_to(QUICK_MODEL)

    async def app(scope: Any, receive: Any, send: Any) -> None:
        scope["state"][TRAIL_STATE] = trail
        withhold(State(scope["state"]), MODEL_HEADER)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    asyncio.run(RequestIdMiddleware(app)({"type": "http", "state": {}}, receive, send))

    names = {name.decode() for name, _ in sent[0]["headers"]}
    assert "x-aira-request-id" in names
    assert MODEL_HEADER not in names


async def _streamed(model: str, *texts: str) -> Streaming:
    async def chunks() -> AsyncIterator[CanonicalChunk]:
        for text in texts:
            yield CanonicalChunk(text_delta=text)

    return Streaming(model, 0, chunks())


async def test_an_opening_still_looking_is_stopped_when_it_is_closed() -> None:
    """Closed before the chain decided — a caller gone, nothing read on — the chain stops rather
    than go on calling models for nobody."""
    stopped = asyncio.Event()

    async def chain() -> Streaming:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("unreachable")  # pragma: no cover

    opening = Opening(chain())
    assert await opening.within(QUICK) is None
    await asyncio.wait_for(opening.aclose(), timeout=STALL_SECONDS)

    assert stopped.is_set()


async def test_an_opening_reads_like_the_stream_it_opens_and_says_who_opened_it() -> None:
    opened: list[str] = []
    gate = asyncio.Event()

    async def chain() -> Streaming:
        await gate.wait()
        return await _streamed(QUICK_MODEL, "a", "b")

    opening = Opening(chain())
    opening.on_open = lambda streamed: opened.append(streamed.model)
    assert await opening.within(QUICK) is None
    gate.set()

    assert [chunk.text_delta async for chunk in opening] == ["a", "b"]
    assert opened == [QUICK_MODEL]


async def test_an_opening_raises_the_refusal_its_chain_ended_in() -> None:
    gate = asyncio.Event()

    async def chain() -> Streaming:
        await gate.wait()
        raise UpstreamError("nobody answered", 503)

    opening = Opening(chain())
    assert await opening.within(QUICK) is None
    gate.set()

    with pytest.raises(UpstreamError):
        await anext(opening)
    await opening.aclose()  # a refusal already read is not raised again


async def test_an_opening_that_decided_in_time_is_what_the_chain_decided() -> None:
    async def refused() -> Streaming:
        raise UpstreamError("nobody answered", 503)

    with pytest.raises(UpstreamError):
        await Opening(refused()).within(STALL_SECONDS)

    async def answered() -> Streaming:
        return await _streamed(QUICK_MODEL, "a")

    streamed = await Opening(answered()).within(STALL_SECONDS)
    assert streamed is not None and streamed.model == QUICK_MODEL
