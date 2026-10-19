"""Real Gemini clients, of every generation still in use, against the gateway (`FRD-143`).

The AI SDK's Google provider is what OpenCode and most TypeScript agents use, and its four majors
send four different requests: the first declares tools in Google's OpenAPI subset, the fourth in
JSON Schema, with `responseJsonSchema` for structured output. Each is run here as an application
runs it — `tools/clients/run.mjs`, the provider with the `ai` major it ships for — against the app
on a real socket, through a tool call and its result, a structured answer and a stream. And the
`google-genai` SDK, Google's own, with its JSON Schema fields.

What the gateway received is recorded, so a passing client that happened to send the old form does
not stand in for the new one.

The Node clients need `npm ci` in `tools/clients` (`make test-clients`). Without it they are
skipped — except where `AIRA_REQUIRE_REAL_CLIENTS` is set, as in CI, which makes a skip a failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from google import genai
from google.genai import types

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ModelRead, UseCaseRead

CLIENTS = Path(__file__).resolve().parents[2] / "tools" / "clients"

#: What `run.mjs` should see a provider major send: the vocabulary of its tool declarations, and
#: of its structured output.
SENDS = {
    "1": ("parameters", "responseSchema"),
    "2": ("parameters", "responseSchema"),
    "3": ("parameters", "responseSchema"),
    "4": ("parametersJsonSchema", "responseJsonSchema"),
}


class _Recorder:
    """The app, and the JSON body of every request it was sent."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.bodies: list[dict[str, Any]] = []

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

        await self.app(scope, replay, send)


@contextmanager
def _served() -> Iterator[tuple[str, _Recorder]]:
    app = create_app(GatewaySettings(auth_required=False, enforce_budgets=False, log_queue_size=0))
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
                    thinking={"modes": [], "levels": ["low", "high"]},
                )
            )
            session.add(UseCaseRead(slug="uc", name="uc", tools_enabled=True))
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
