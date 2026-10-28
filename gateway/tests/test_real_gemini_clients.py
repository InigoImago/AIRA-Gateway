"""Real Gemini clients, of every generation still in use, against the gateway (`FRD-143`).

The AI SDK's Google provider is what OpenCode and most TypeScript agents use, and its four majors
send four different requests: the first declares tools in Google's OpenAPI subset, the fourth in
JSON Schema, with `responseJsonSchema` for structured output. Each is run here as an application
runs it — `tools/clients/run.mjs`, the provider with the `ai` major it ships for — against the app
on a real socket, through a tool call and its result, a structured answer and a stream. And the
`google-genai` SDK, Google's own, with its JSON Schema fields.

What the gateway received is recorded, so a passing client that happened to send the old form does
not stand in for the new one.

And **OpenCode itself**, both generations in use (`FRD-144`): OpenCode 1 runs the AI SDK, OpenCode
2 its own Gemini runtime, and neither is what a test of the gateway would think to send. Each runs a
coding turn headless, and each runs its terminal interface in `tmux` with the budget plugin
installed, read back from the screen.

The Node clients need `npm ci` in `tools/clients` (`make test-clients`), the interface tests `tmux`.
Without them they are skipped — except where `AIRA_REQUIRE_REAL_CLIENTS` is set, as in CI, which
makes a skip a failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
import uvicorn
from google import genai
from google.genai import types
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from aira_gateway.api.gemini.routes import KEEPALIVE as SKIPPED_LINE
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import watch_connections
from aira_gateway.db.models import BudgetRead, ModelRead, RequestLog, UseCaseRead
from aira_gateway.upstreams.exchange import refused
from aira_gateway.upstreams.mock import MOCK_SIGNATURE
from aira_gateway.upstreams.vertex.transport import NAME as VERTEX

ROOT = Path(__file__).resolve().parents[2]
CLIENTS = ROOT / "tools" / "clients"
BUDGET_PLUGIN = ROOT / "tools" / "opencode" / "plugins" / "aira-budget" / "tui.tsx"
ARCHIVE_TOOL = ROOT / "tools" / "opencode_plugin_archive.py"

#: The OpenCode binaries `tools/clients` installs, by generation: `opencode-ai` is OpenCode 1,
#: `@opencode/cli` OpenCode 2 — two packages, because the second is a rewrite.
OPENCODE = {
    "1": CLIENTS / "node_modules" / "opencode-ai" / "bin" / "opencode.exe",
    "2": CLIENTS / "node_modules" / "@opencode" / "cli" / "bin" / "opencode.exe",
}

#: How the catalogue declares the model these clients talk to: two level words, no default.
THINKING: dict[str, Any] = {"modes": [], "levels": ["low", "high"]}

#: The variants the console writes for that declaration (`opencode-config.ts`, `FRD-151`).
CONSOLE_VARIANTS = {
    "low": {"thinkingConfig": {"thinkingLevel": "low"}},
    "high": {"thinkingConfig": {"thinkingLevel": "high"}},
}

#: What the mock streams as its reasoning wherever it is thinking and the use case returns it.
MOCK_THOUGHT = "[mock thought]"

#: The same model, thinking whenever nothing is asked — so every call it makes is signed.
THINKS_UNASKED = {**THINKING, "default": {"mode": "high"}}

#: What `run.mjs` should see a provider major send: the vocabulary of its tool declarations, and
#: of its structured output.
SENDS = {
    "1": ("parameters", "responseSchema"),
    "2": ("parameters", "responseSchema"),
    "3": ("parameters", "responseSchema"),
    "4": ("parametersJsonSchema", "responseJsonSchema"),
}


class _Recorder:
    """The app, the JSON body of every request it was sent, and how many keepalives it sent back."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.bodies: list[dict[str, Any]] = []
        #: Bare lines a Gemini stream sent while its model was quiet (`ADR-0046`): a client that
        #: passes a quiet case without having been sent one has not been tested against them.
        self.keepalives = 0

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        messages: list[Any] = []
        while True:
            message = await receive()
            messages.append(message)
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        with contextlib.suppress(ValueError):
            self.bodies.append(json.loads(b"".join(chunks) or b"{}"))

        async def replay() -> Any:
            # The body again, then the real channel: a stream listens on it for the client
            # leaving, and an invented disconnect would end every stream after its first chunk.
            return messages.pop(0) if messages else await receive()

        async def counting(message: Any) -> None:
            if message.get("type") == "http.response.body" and message.get("body") == KEEPALIVE:
                self.keepalives += 1
            await send(message)

        await self.app(scope, replay, counting)


def _on_a_file(path: Path) -> AsyncEngine:
    """The database for clients that send at once: a SQLite file, one connection per session.

    The hermetic suite's in-memory database is a single connection every session shares, which
    holds while requests come one at a time, as `TestClient` sends them. Real clients send several:
    OpenCode 2 asks for a title while its turn streams, and the budget plugin reads beside both. On
    one shared connection a session's commit fails on another's unfinished statement, which ends a
    stream without its last chunk, and a session's rollback discards another's audit row. On a
    file every session has its own connection and transaction, as on Postgres, and SQLite takes
    the writes one at a time.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        poolclass=NullPool,
        connect_args={"timeout": 30},
        hide_parameters=True,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _readers_beside_a_writer(connection: Any, _record: object) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return watch_connections(engine)


#: What the Gemini surface sends while its stream is quiet, as it goes on the wire.
KEEPALIVE = SKIPPED_LINE.encode()

#: How long the quiet model below says nothing — before its first chunk and between two — and the
#: keepalive interval the gateway is served with beside it: several keepalives in every pause.
QUIET_SECONDS = 0.6
KEPT_ALIVE_EVERY = 0.15


def _quietly(stream: Callable[..., Any]) -> Callable[..., Any]:
    """The mock's stream, with a silence before its first chunk and between every two — as a model
    reading a large context, thinking, or writing a call's arguments is silent (`ADR-0046`)."""

    async def quiet(self: Any, request: Any) -> Any:
        async for chunk in stream(self, request):
            await asyncio.sleep(QUIET_SECONDS)
            yield chunk

    return quiet


#: Google's own answer for a model it does not serve where it is asked: retired, or never there.
NOT_SERVED = {"error": {"code": 404, "message": "Model not found.", "status": "NOT_FOUND"}}


def _not_served() -> Exception:
    """The provider's `404`, judged by the gateway's own exchange, as a real answer is."""
    asked = httpx.Request("POST", "https://europe-west1-aiplatform.googleapis.com/v1/x")
    return refused(httpx.Response(404, json=NOT_SERVED, request=asked), VERTEX)


async def _generate_not_served(self: Any, request: Any) -> Any:
    raise _not_served()


async def _stream_not_served(self: Any, request: Any) -> Any:
    for chunk in ():  # an async generator, as the stream it stands in for
        yield chunk
    raise _not_served()


@contextmanager
def _served(
    *,
    daily_requests: int | None = None,
    quiet: bool = False,
    thinking: dict[str, Any] | None = None,
    gone: bool = False,
) -> Iterator[tuple[str, _Recorder]]:
    """The app on a real socket, with a model and a use case — and, given ``daily_requests``, a
    budget on that use case that is enforced, for what a budget display has to show. ``quiet``
    makes the model pause before every chunk and the gateway keep its streams alive meanwhile.
    ``thinking`` replaces the model's thinking declaration. ``gone`` makes its provider answer
    as for a model it no longer serves."""
    from aira_gateway.upstreams.mock import MockProvider

    pausing = (
        mock.patch.object(MockProvider, "stream_generate", _quietly(MockProvider.stream_generate))
        if quiet
        else contextlib.nullcontext()
    )
    retired = (
        mock.patch.multiple(
            MockProvider, generate=_generate_not_served, stream_generate=_stream_not_served
        )
        if gone
        else contextlib.nullcontext()
    )
    with (
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory,
        pausing,
        retired,
    ):
        engine = _on_a_file(Path(directory) / "gateway.db")
        with mock.patch("aira_gateway.app.build_engine", return_value=engine):
            app = create_app(
                GatewaySettings(
                    auth_required=False,
                    enforce_budgets=daily_requests is not None,
                    log_queue_size=0,
                    **({"stream_keepalive_seconds": KEPT_ALIVE_EVERY} if quiet else {}),
                )
            )
        recorder = _Recorder(app)
        config = uvicorn.Config(recorder, host="127.0.0.1", port=0, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:  # noqa: ASYNC110 — a thread, not a coroutine
            threading.Event().wait(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]

        async def _seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(
                    ModelRead(
                        model="mock-1",
                        capabilities=["generate", "tools", "structured_output", "thinking"],
                        thinking=thinking or THINKING,
                    )
                )
                session.add(UseCaseRead(slug="uc", name="uc", tools_enabled=True))
                if daily_requests is not None:
                    session.add(
                        BudgetRead(
                            use_case="uc",
                            scope="use_case",
                            period="day",
                            limit_requests=daily_requests,
                        )
                    )
                await session.commit()

        asyncio.run(_seed())
        try:
            yield f"http://127.0.0.1:{port}", recorder
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def _clients_installed() -> bool:
    return shutil.which("node") is not None and (CLIENTS / "node_modules").is_dir()


@pytest.fixture
def node_clients() -> None:
    if _clients_installed():
        return
    if os.environ.get("AIRA_REQUIRE_REAL_CLIENTS"):
        pytest.fail("the real clients are required here and not installed: make test-clients")
    pytest.skip("the AI SDK clients are not installed (npm ci in tools/clients)")


def _declared_with(body: dict[str, Any]) -> set[str]:
    tools = body.get("tools") or []
    return {
        key
        for tool in ([tools] if isinstance(tools, dict) else tools)
        for declaration in tool.get("functionDeclarations") or []
        for key in declaration
        if key in ("parameters", "parametersJsonSchema")
    }


def _answered_with(body: dict[str, Any]) -> set[str]:
    config = body.get("generationConfig") or {}
    return {key for key in ("responseSchema", "responseJsonSchema") if key in config}


#: Requests sent together below, half streamed and half whole: enough that, on one shared
#: connection, some commit meets another session's unfinished statement every time.
AT_ONCE = 40


def test_the_served_gateway_answers_and_records_requests_sent_at_once() -> None:
    """What every OpenCode test below relies on: requests that arrive together are each answered,
    a stream to its last chunk, and each recorded once."""
    body = {"contents": [{"role": "user", "parts": [{"text": "read hello.py"}]}]}
    with _served() as (base, recorder):
        models = f"{base}/uc/uc/v1beta/models"

        async def streamed(client: httpx.AsyncClient) -> int:
            url = f"{models}/mock-1:streamGenerateContent?alt=sse"
            async with client.stream("POST", url, json=body) as response:
                await response.aread()
            return response.status_code

        async def whole(client: httpx.AsyncClient) -> int:
            return (await client.post(f"{models}/mock-1:generateContent", json=body)).status_code

        async def together() -> list[int | BaseException]:
            async with httpx.AsyncClient(timeout=60) as client:
                calls = [streamed(client) if n % 2 else whole(client) for n in range(AT_ONCE)]
                return await asyncio.gather(*calls, return_exceptions=True)

        async def recorded() -> int:
            async with recorder.app.state.db_sessionmaker() as session:
                return await session.scalar(select(func.count()).select_from(RequestLog)) or 0

        answered = asyncio.run(together())
        rows = asyncio.run(recorded())
    assert answered == [200] * AT_ONCE
    assert rows == AT_ONCE


@pytest.mark.parametrize("major", sorted(SENDS))
def test_the_ai_sdk_google_provider_works_in_every_major(node_clients: None, major: str) -> None:
    with _served() as (base, recorder):
        completed = subprocess.run(  # noqa: S603 — fixed arguments, our own script
            ["node", "run.mjs", major, f"{base}/v1beta", "uc"],  # noqa: S607
            cwd=CLIENTS,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    results = json.loads(completed.stdout.strip().splitlines()[-1])

    assert all(outcome["ok"] for outcome in results.values()), results
    tool = results["tool"]
    assert tool["steps"] == 2, "the call, then the answer after its result"
    assert [sorted(call) for call in tool["executed"]] == [["limit", "path"]]
    assert "acted on the tool result" in tool["text"]
    assert set(results["object"]["object"]) == {"title", "pages"}
    assert results["stream"]["text"]

    declared = set().union(*(_declared_with(body) for body in recorder.bodies))
    answered = set().union(*(_answered_with(body) for body in recorder.bodies))
    tools_field, schema_field = SENDS[major]
    assert declared == {tools_field}, f"provider {major} declared its tools with {declared}"
    assert answered == {schema_field}, f"provider {major} asked for structure with {answered}"


def _signatures_handed_back(recorder: _Recorder) -> list[Any]:
    """The signature on every function call a request carried back in its history."""
    return [
        part.get("thoughtSignature")
        for body in recorder.bodies
        for content in body.get("contents") or []
        for part in content.get("parts") or []
        if "functionCall" in part
    ]


@pytest.mark.parametrize("major", sorted(SENDS))
def test_the_ai_sdk_hands_a_function_calls_signature_back(node_clients: None, major: str) -> None:
    """A thinking model signs its call and validates the signature on the turn that continues it;
    this gateway carries a Claude turn's thinking blocks in the same field (`FRD-152`). The first
    major predates signatures and drops it — such a turn goes on without the model's thinking."""
    with _served(thinking=THINKS_UNASKED) as (base, recorder):
        completed = subprocess.run(  # noqa: S603 — fixed arguments, our own script
            ["node", "run.mjs", major, f"{base}/v1beta", "uc"],  # noqa: S607
            cwd=CLIENTS,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    handed_back = _signatures_handed_back(recorder)
    assert handed_back, "no call came back"
    assert set(handed_back) == ({None} if major == "1" else {MOCK_SIGNATURE}), handed_back


@pytest.mark.parametrize("major", sorted(SENDS))
def test_the_ai_sdk_reads_a_stream_kept_alive_while_its_model_is_quiet(
    node_clients: None, major: str
) -> None:
    """A model that says nothing for a while — before its first word and between two — and a
    gateway that keeps the stream alive meanwhile with bare lines (`ADR-0046`). The AI SDK reads
    SSE by the specification, which dispatches nothing for a blank line; each major is run, because
    each major shipped its own parser."""
    with _served(quiet=True) as (base, recorder):
        completed = subprocess.run(  # noqa: S603 — fixed arguments, our own script
            ["node", "run.mjs", major, f"{base}/v1beta", "uc"],  # noqa: S607
            cwd=CLIENTS,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    results = json.loads(completed.stdout.strip().splitlines()[-1])

    assert all(outcome["ok"] for outcome in results.values()), results
    assert results["stream"]["text"].strip()
    assert recorder.keepalives >= 2, "the stream was never quiet long enough to be kept alive"


def test_google_genai_reads_a_stream_kept_alive_while_its_model_is_quiet() -> None:
    """Google's own SDK hands every line of a stream that is not `data:` to its JSON parser — so
    the keepalive is a bare line, which it skips, and not an SSE comment, which it would raise on
    (`ADR-0046`). Both of its readers: the synchronous one and the asynchronous one."""
    with _served(quiet=True) as (base, recorder):
        client = genai.Client(
            api_key="not-checked-here",
            http_options=types.HttpOptions(
                base_url=base, headers={"x-aira-use-case": "uc"}, api_version="v1beta"
            ),
        )
        read = "".join(
            chunk.text or ""
            for chunk in client.models.generate_content_stream(model="mock-1", contents="say hello")
        )

        async def read_async() -> str:
            stream = await client.aio.models.generate_content_stream(
                model="mock-1", contents="say hello"
            )
            return "".join([chunk.text or "" async for chunk in stream])

        read_aloud = asyncio.run(read_async())

    assert read.strip() and read == read_aloud
    assert recorder.keepalives >= 4, "the streams were never quiet long enough to be kept alive"


def test_google_genais_chat_hands_a_function_calls_signature_back() -> None:
    """Google's SDK keeps the answer whole in the chat's history — the signature as bytes, written
    back as base64 — and sends it with the function's result (`FRD-152`)."""
    with _served(thinking=THINKS_UNASKED) as (base, recorder):
        client = genai.Client(
            api_key="not-checked-here",
            http_options=types.HttpOptions(
                base_url=base, headers={"x-aira-use-case": "uc"}, api_version="v1beta"
            ),
        )
        declared = types.FunctionDeclaration(
            name="read_file",
            description="Read a file.",
            parameters_json_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        )
        chat = client.chats.create(
            model="mock-1",
            config=types.GenerateContentConfig(
                tools=[types.Tool(function_declarations=[declared])],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        (call, *_) = chat.send_message("read hello.py").function_calls or []
        answered = chat.send_message(
            types.Part.from_function_response(name=call.name, response={"content": "hi"})
        )
    assert "acted on the tool result" in (answered.text or "")
    assert _signatures_handed_back(recorder) == [MOCK_SIGNATURE]


def test_google_genai_sends_json_schema_and_a_thinking_level() -> None:
    """Google's own SDK, with the fields current applications set."""
    with _served() as (base, recorder):
        client = genai.Client(
            api_key="not-checked-here",
            http_options=types.HttpOptions(
                base_url=base, headers={"x-aira-use-case": "uc"}, api_version="v1beta"
            ),
        )
        called = client.models.generate_content(
            model="mock-1",
            contents="read hello.py",
            config=types.GenerateContentConfig(
                tools=[
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name="read_file",
                                description="Read a file.",
                                parameters_json_schema={
                                    "type": "object",
                                    "properties": {"path": {"type": "string"}},
                                    "required": ["path"],
                                },
                            )
                        ]
                    )
                ],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )
        structured = client.models.generate_content(
            model="mock-1",
            contents="describe a book",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema={
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            ),
        )
        thought = client.models.generate_content(
            model="mock-1",
            contents="think hard",
            config=types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.HIGH)
            ),
        )

    assert called.function_calls and called.function_calls[0].name == "read_file"
    assert json.loads(structured.text or "{}") == {"title": "describe a book"}
    assert thought.text
    sent = recorder.bodies
    # The SDK writes the declaration's field in snake_case, and the answer's in camelCase.
    assert "parameters_json_schema" in json.dumps(sent[0])
    assert "responseJsonSchema" in json.dumps(sent[1])
    assert sent[2]["generationConfig"]["thinkingConfig"] in (
        {"thinkingLevel": "HIGH"},
        {"thinking_level": "HIGH"},
    )


# == OpenCode, both generations (`FRD-144`) ======================================================


@pytest.fixture
def opencode(node_clients: None) -> None:
    missing = [generation for generation, binary in OPENCODE.items() if not binary.exists()]
    if missing:
        pytest.fail(f"OpenCode {missing} not installed by tools/clients: run make test-clients")


@pytest.fixture(scope="module")
def opencode_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One package cache for every OpenCode run here: OpenCode 1 installs its AI SDK provider on
    first use, and a fresh cache per test would download it every time."""
    return tmp_path_factory.mktemp("opencode-cache")


def _project(
    root: Path,
    base: str,
    *,
    configuration: str,
    plugin: str | None = None,
    reasoning: bool = False,
    variants: dict[str, Any] | None = None,
) -> Path:
    """A git project with one file, an AIRA provider, and optionally the budget plugin.

    ``configuration`` is ``"v1"`` — the shape the AIRA console generates, `provider`, `npm` and
    `options`, which OpenCode 2 still reads — or ``"v2"``, OpenCode 2's own `providers`, `package`
    and `settings`. ``variants`` are the thinking settings the console offers in the first.
    """
    project = root / "project"
    (project / ".opencode").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)  # noqa: S603, S607
    (project / "hello.py").write_text('print("hello")\n')
    model = {"name": "mock-1 via AIRA (EUR)", "limit": {"context": 128000, "output": 8192}}
    # Only where it is true, which is how the console writes it.
    thinks = {"reasoning": True} if reasoning else {}
    if variants is not None:
        thinks["variants"] = variants
    if configuration == "v1":
        provider = {
            "provider": {
                "aira": {
                    "npm": "@ai-sdk/google",
                    "name": "AIRA",
                    "options": {"baseURL": f"{base}/uc/uc/v1beta", "apiKey": "{env:AIRA_API_KEY}"},
                    "models": {"mock-1": {**model, "tool_call": True, **thinks}},
                }
            }
        }
    else:
        provider = {
            "providers": {
                "aira": {
                    "name": "AIRA",
                    "env": ["AIRA_API_KEY"],
                    "package": "@opencode/ai/providers/google",
                    "settings": {"baseURL": f"{base}/uc/uc/v1beta"},
                    "models": {"mock-1": {**model, "capabilities": {"tools": True}}},
                }
            }
        }
    config = {"$schema": "https://opencode.ai/config.json", "model": "aira/mock-1", **provider}
    (project / "opencode.json").write_text(json.dumps(config, indent=2))
    if plugin is not None:
        # Where each generation finds the one file: OpenCode 2 discovers the directory, OpenCode 1
        # is told in `tui.json`.
        target = project / ".opencode" / "plugins" / "aira-budget"
        target.mkdir(parents=True)
        shutil.copy(BUDGET_PLUGIN, target / "tui.tsx")
        if plugin == "1":
            (project / ".opencode" / "tui.json").write_text(
                json.dumps({"plugin": ["./plugins/aira-budget/tui.tsx"]})
            )
    return project


def _environment(root: Path, project: Path, cache: Path) -> dict[str, str]:
    """A home of its own, so nothing of the developer's OpenCode is read or written."""
    home = root / "home"
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_CACHE_HOME": str(cache),
        # OpenCode 1 takes its directory from `PWD`, not from the process's working directory.
        "PWD": str(project),
        "TERM": "xterm-256color",
        "AIRA_API_KEY": "not-checked-here",
        **{
            key: value
            for key in (
                "HTTPS_PROXY",
                "HTTP_PROXY",
                "NO_PROXY",
                "https_proxy",
                "http_proxy",
                "no_proxy",
            )
            if (value := os.environ.get(key))
        },
    }


def _command(generation: str) -> list[str]:
    # OpenCode 2 otherwise connects to a background service shared with the developer's sessions.
    # Every `run` below is started with standard input closed: both generations add a piped input
    # to the message and wait for its end, so one left open hangs the run before its first request.
    return [str(OPENCODE[generation]), *(["--standalone"] if generation == "2" else [])]


@pytest.mark.parametrize(
    ("generation", "configuration"),
    [("1", "v1"), ("2", "v2"), ("2", "v1")],
    ids=["opencode-1", "opencode-2", "opencode-2-console-config"],
)
def test_opencode_carries_a_coding_turn(
    opencode: None, opencode_cache: Path, tmp_path: Path, generation: str, configuration: str
) -> None:
    """A tool call, its result, and the answer after it — through the client developers run.

    The third case is the configuration the AIRA console hands out, which is OpenCode 1's shape:
    OpenCode 2 must still read it, or every key issued before the rollout needs a new file.
    """
    with _served() as (base, recorder):
        project = _project(tmp_path, base, configuration=configuration)
        run = [*_command(generation)[:1], "run", *_command(generation)[1:], "read hello.py"]
        completed = subprocess.run(  # noqa: S603 — our own installed binary, fixed arguments
            run,
            cwd=project,
            env=_environment(tmp_path, project, opencode_cache),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output[-3000:]
    assert "acted on the tool result" in output, output[-3000:]
    declared = set().union(*(_declared_with(body) for body in recorder.bodies))
    # OpenCode 2's own runtime declares in JSON Schema whichever configuration shape it read.
    if generation == "2":
        assert declared == {"parametersJsonSchema"}, declared
    else:
        assert declared, "OpenCode 1 declared no tools"


@pytest.mark.parametrize("generation", ["1", "2"], ids=["opencode-1", "opencode-2"])
def test_opencode_carries_a_turn_through_a_model_that_is_quiet_for_a_while(
    opencode: None, opencode_cache: Path, tmp_path: Path, generation: str
) -> None:
    """A tool call and the answer after it, from a model that pauses before every chunk while the
    gateway keeps the stream alive (`ADR-0046`) — OpenCode 1 through the AI SDK, OpenCode 2 through
    its own runtime, which parses the stream itself."""
    with _served(quiet=True) as (base, recorder):
        project = _project(tmp_path, base, configuration="v1")
        run = [*_command(generation)[:1], "run", *_command(generation)[1:], "read hello.py"]
        completed = subprocess.run(  # noqa: S603 — our own installed binary, fixed arguments
            run,
            cwd=project,
            env=_environment(tmp_path, project, opencode_cache),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output[-3000:]
    assert "acted on the tool result" in output, output[-3000:]
    assert recorder.keepalives >= 2, "the stream was never quiet long enough to be kept alive"


@pytest.mark.parametrize("generation", ["1", "2"], ids=["opencode-1", "opencode-2"])
def test_opencode_streams_a_turn_to_a_thinking_model(
    opencode: None, opencode_cache: Path, tmp_path: Path, generation: str
) -> None:
    """A model declared as reasoning: OpenCode asks for its thoughts on the stream (`FRD-145`),
    which this surface refused until streams carried them."""
    with _served() as (base, recorder):
        # The console's configuration for both: OpenCode 2 reads its `reasoning` flag, and has no
        # such field among its own `capabilities`.
        project = _project(tmp_path, base, configuration="v1", reasoning=True)
        run = [*_command(generation)[:1], "run", *_command(generation)[1:], "read hello.py"]
        completed = subprocess.run(  # noqa: S603 — our own installed binary, fixed arguments
            run,
            cwd=project,
            env=_environment(tmp_path, project, opencode_cache),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output[-3000:]
    assert "acted on the tool result" in output, output[-3000:]
    asked = [
        body
        for body in recorder.bodies
        if ((body.get("generationConfig") or {}).get("thinkingConfig") or {}).get("includeThoughts")
    ]
    # OpenCode 2 asks only once a thinking setting is chosen for the model; OpenCode 1 always.
    if generation == "1":
        assert asked, "OpenCode 1 asked a reasoning model for no thoughts"


def _shows_thinking(generation: str, variant: str | None, prompt: str) -> list[str]:
    """`opencode run` printing the reasoning, with the variant chosen the way each generation's
    command line takes it: OpenCode 1 by flag, OpenCode 2 after the model's name."""
    binary, *standalone = _command(generation)
    chosen: list[str] = []
    if variant is not None:
        chosen = (
            ["--variant", variant] if generation == "1" else ["--model", f"aira/mock-1#{variant}"]
        )
    return [binary, "run", *standalone, "--thinking", *chosen, prompt]


def _thinking_sent(recorder: _Recorder, *, turns: bool) -> list[dict[str, Any]]:
    """The thinking setting of each conversation turn — a request that declares the tools — or,
    with ``turns`` false, of each of OpenCode's own small requests, such as the session's title."""
    return [
        ((body.get("generationConfig") or {}).get("thinkingConfig") or {})
        for body in recorder.bodies
        if body.get("contents") and bool(body.get("tools")) is turns
    ]


@pytest.mark.parametrize("generation", ["1", "2"], ids=["opencode-1", "opencode-2"])
def test_opencode_shows_the_reasoning_at_the_variant_somebody_chose(
    opencode: None, opencode_cache: Path, tmp_path: Path, generation: str
) -> None:
    """The console's variants (`FRD-151`), read by both generations from the console's own shape:
    the chosen one is the thinking level sent, and the reasoning the model streams is shown — with
    no `reasoning` flag in the file and no `includeThoughts` from the client."""
    with _served() as (base, recorder):
        project = _project(tmp_path, base, configuration="v1", variants=CONSOLE_VARIANTS)
        completed = subprocess.run(  # noqa: S603 — our own installed binary, fixed arguments
            _shows_thinking(generation, "high", "read hello.py"),
            cwd=project,
            env=_environment(tmp_path, project, opencode_cache),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output[-3000:]
    assert "acted on the tool result" in output, output[-3000:]
    assert MOCK_THOUGHT in output, output[-3000:]
    turns = _thinking_sent(recorder, turns=True)
    assert turns, "OpenCode sent no turn"
    # The call the thinking model signed came back signed (`FRD-152`).
    assert _signatures_handed_back(recorder) == [MOCK_SIGNATURE]
    # OpenCode 2 asks for the thoughts itself once a variant is chosen; the level is the console's.
    assert all(setting.get("thinkingLevel") == "high" for setting in turns), turns
    if generation == "1":
        # OpenCode 1 sends its own small requests with the first variant, which is the cheapest.
        assert _thinking_sent(recorder, turns=False) == [{"thinkingLevel": "low"}]


@pytest.mark.parametrize("generation", ["1", "2"], ids=["opencode-1", "opencode-2"])
def test_opencode_shows_the_reasoning_of_a_model_that_thinks_unasked(
    opencode: None, opencode_cache: Path, tmp_path: Path, generation: str
) -> None:
    """No variant chosen, so nothing OpenCode sends names thinking: the model's declared default
    does (`FRD-151`), and the reasoning the model produces anyway is shown."""
    with _served(thinking=THINKS_UNASKED) as (base, recorder):
        project = _project(tmp_path, base, configuration="v1", variants=CONSOLE_VARIANTS)
        completed = subprocess.run(  # noqa: S603 — our own installed binary, fixed arguments
            _shows_thinking(generation, None, "read hello.py"),
            cwd=project,
            env=_environment(tmp_path, project, opencode_cache),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output[-3000:]
    assert "acted on the tool result" in output, output[-3000:]
    assert MOCK_THOUGHT in output, output[-3000:]
    assert not any(_thinking_sent(recorder, turns=True)), "the client asked, not the default"
    assert _signatures_handed_back(recorder) == [MOCK_SIGNATURE]


@pytest.mark.parametrize("generation", ["1", "2"], ids=["opencode-1", "opencode-2"])
def test_opencode_stops_at_a_model_its_provider_does_not_serve(
    opencode: None, opencode_cache: Path, tmp_path: Path, generation: str
) -> None:
    """A model its provider retired, or never offered in that region: no retry changes that, so it
    answers `404`, which neither generation retries — a `502` each retries as an outage, again and
    again — and the developer reads why (`FRD-628` FR-6)."""
    with _served(gone=True) as (base, recorder):
        project = _project(tmp_path, base, configuration="v1")
        run = [*_command(generation)[:1], "run", *_command(generation)[1:], "read hello.py"]
        completed = subprocess.run(  # noqa: S603 — our own installed binary, fixed arguments
            run,
            cwd=project,
            env=_environment(tmp_path, project, opencode_cache),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    output = completed.stdout + completed.stderr
    turns = [body for body in recorder.bodies if body.get("tools")]
    assert len(turns) == 1, f"the turn was sent {len(turns)} times\n{output[-3000:]}"
    assert "asking again will not change that" in output, output[-3000:]


def _tmux(socket: str, *arguments: str) -> str:
    return subprocess.run(  # noqa: S603 — tmux with fixed arguments
        ["tmux", "-L", socket, *arguments],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout


def _screen_until(socket: str, found: Callable[[str], bool], seconds: float) -> str:
    deadline = time.monotonic() + seconds
    screen = ""
    while time.monotonic() < deadline:
        screen = _tmux(socket, "capture-pane", "-p", "-t", "oc")
        if found(screen):
            return screen
        time.sleep(0.5)
    raise AssertionError(f"not on screen after {seconds}s:\n{screen}")


@pytest.fixture
def tmux() -> None:
    if shutil.which("tmux"):
        return
    if os.environ.get("AIRA_REQUIRE_REAL_CLIENTS"):
        pytest.fail("tmux is required here and not installed")
    pytest.skip("tmux is not installed")


def _shows_the_budget(generation: str, project: Path, env: dict[str, str]) -> None:
    """The sidebar section after a turn, with the use case's limit and the three periods, and the
    `/budget` dialog, read back from a `tmux` pane."""
    socket = f"aira-{uuid.uuid4().hex[:8]}"
    command = " ".join(
        [
            "env",
            "-i",
            *(f"{key}={value}" for key, value in env.items()),
            *_command(generation),
            "; sleep 30",
        ]
    )
    _tmux(
        socket,
        "new-session",
        "-d",
        "-s",
        "oc",
        "-x",
        "170",
        "-y",
        "48",
        "-c",
        str(project),
        command,
    )
    try:
        _screen_until(socket, lambda screen: "mock-1 via AIRA" in screen, 120)
        _tmux(socket, "send-keys", "-t", "oc", "read hello.py", "Enter")
        # The turn is over before anything else is typed: a command entered while a turn runs stays
        # in the prompt, and the dialog it would open never comes. The sidebar can show the budget
        # from the first reading while the turn is still running; the mock's answer to the tool
        # result is its last word.
        sidebar = _screen_until(
            socket,
            lambda screen: (
                "acted on the tool result" in screen
                and "AIRA budget" in screen
                and "of 50 requests left" in screen
            ),
            120,
        )
        assert "use case · per day · resets" in sidebar
        assert "This month" in sidebar
        _tmux(socket, "send-keys", "-t", "oc", "/budget")
        # Entered once the command list offers it, not after a guess at how long that takes.
        _screen_until(socket, lambda screen: "What is left, and what you spent" in screen, 30)
        _tmux(socket, "send-keys", "-t", "oc", "Enter")
        dialog = _screen_until(socket, lambda screen: "Allowances" in screen, 30)
        assert "limits enforced" in dialog
        assert "From the audit trail" in dialog
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"], check=False, timeout=30)  # noqa: S603, S607


@pytest.mark.parametrize("generation", ["1", "2"], ids=["opencode-1", "opencode-2"])
def test_the_budget_plugin_shows_in_opencodes_own_interface(
    opencode: None, tmux: None, opencode_cache: Path, tmp_path: Path, generation: str
) -> None:
    """The plugin as a developer meets it: the terminal interface, read back from the screen.

    The same file in both generations, installed in the project where each one looks.
    """
    with _served(daily_requests=50) as (base, _recorder):
        project = _project(tmp_path, base, configuration="v1", plugin=generation)
        _shows_the_budget(generation, project, _environment(tmp_path, project, opencode_cache))


@pytest.mark.parametrize("generation", ["1", "2"], ids=["opencode-1", "opencode-2"])
def test_the_release_archive_unpacked_for_every_project_shows_the_budget(
    opencode: None, tmux: None, opencode_cache: Path, tmp_path: Path, generation: str
) -> None:
    """The plugin as a rollout installs it: the release's archive (`opencode_plugin_archive.py`)
    unpacked into the user's OpenCode configuration directory with `tar`, and for OpenCode 1 the one
    `tui.json` entry the runbook gives, relative to that directory. Nothing in the project."""
    with _served(daily_requests=50) as (base, _recorder):
        project = _project(tmp_path, base, configuration="v1")
        env = _environment(tmp_path, project, opencode_cache)
        config = Path(env["XDG_CONFIG_HOME"]) / "opencode"
        config.mkdir(parents=True)
        # The command the release job runs, so this unpacks what a release attaches.
        subprocess.run(  # noqa: S603
            [sys.executable, str(ARCHIVE_TOOL), "v0.0.1", "--out", str(tmp_path / "release")],
            check=True,
            capture_output=True,
        )
        built = tmp_path / "release" / "opencode-aira-budget-v0.0.1.tar.gz"
        subprocess.run(["tar", "-xzf", str(built), "-C", str(config)], check=True)  # noqa: S603, S607
        if generation == "1":
            (config / "tui.json").write_text(
                json.dumps({"plugin": ["./plugins/aira-budget/tui.tsx"]})
            )
        _shows_the_budget(generation, project, env)
