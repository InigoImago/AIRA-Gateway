"""The Python examples in `examples/` run, in every credential form each one offers (`FRD-155`).

An example nobody runs is documentation that goes stale without a sound: a renamed setting, a
gateway that stops reading a carrier, an SDK that moves its base URL option. So the examples whose
libraries this suite already has — Google's `google-genai` and plain `httpx` — run here as a reader
runs them, a process with nothing but their environment, against the gateway on a real socket with
authentication on: an API key in its header and in the query, a person's token, a service
account's token fetched with the client-credentials grant from an issuer of the test's own, and a
token where the key goes. The examples for other languages and frameworks were run the same way
when they were written; they need toolchains this suite does not install.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import jwt
import pytest
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa

from aira_gateway.api.gemini.schemas import TASK_TYPES
from aira_gateway.app import create_app
from aira_gateway.auth import keys
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import ApiKey, ModelRead, UseCaseMemberRead, UseCaseRead
from aira_gateway.pipeline.engine import PipelineEngine
from aira_gateway.upstreams.base import ProviderRegistry
from aira_gateway.upstreams.mock import MockProvider

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "python"
AUDIENCE = "aira-gateway"
USE_CASE = "examples"
CHAT, EMBEDDING, SPEECH = "mock-chat", "mock-embedding", "mock-speech"
#: The service account's client, as Keycloak's client-credentials grant knows it.
CLIENT_ID, CLIENT_SECRET = "examples-ci", "not-a-secret"

#: Every credential form an example built on an SDK offers, and the two only HTTP by hand can send.
SDK_FORMS = ("key", "bearer", "service-account", "token-as-key")
REST_FORMS = (*SDK_FORMS, "query-key")


class _Issuer:
    """An OIDC issuer of the test's own: a key, its JWKS, and a token endpoint that answers the
    client-credentials grant for one client, as Keycloak's does."""

    def __init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key()))
        jwk.update({"kid": "examples-1", "use": "sig", "alg": "RS256"})
        jwks = json.dumps({"keys": [jwk]}).encode()
        issuer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self._answer(200, jwks if self.path.endswith("/certs") else b"{}")

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                form = dict(
                    pair.split("=", 1)
                    for pair in self.rfile.read(length).decode().split("&")
                    if "=" in pair
                )
                granted = form.get("grant_type") == "client_credentials" and (
                    form.get("client_id"),
                    form.get("client_secret"),
                ) == (CLIENT_ID, CLIENT_SECRET)
                if not granted:
                    self._answer(401, b'{"error":"unauthorized_client"}')
                    return
                token = issuer.token(
                    preferred_username=f"service-account-{CLIENT_ID}",
                    azp=CLIENT_ID,
                    client_id=CLIENT_ID,
                    groups=[f"/use-cases/{USE_CASE}"],
                )
                self._answer(200, json.dumps({"access_token": token}).encode())

            def _answer(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{port}/realms/examples"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def token(self, **claims: Any) -> str:
        now = int(time.time())
        payload = {
            "iss": self.url,
            "aud": AUDIENCE,
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
            "sub": str(uuid.uuid4()),
            "typ": "Bearer",
            **claims,
        }
        return jwt.encode(payload, self._key, algorithm="RS256", headers={"kid": "examples-1"})

    def close(self) -> None:
        self._server.shutdown()


@pytest.fixture(scope="module")
def gateway() -> Iterator[dict[str, str]]:
    """The gateway on a socket with authentication on, a use case, its models, a key, a member;
    the environment an example reads."""
    issuer = _Issuer()
    app = create_app(
        GatewaySettings(
            auth_required=True,
            require_use_case=True,
            oidc_enabled=True,
            oidc_issuer=issuer.url,
            oidc_audience=AUDIENCE,
            oidc_jwks_uri=f"{issuer.url}/certs",
            log_queue_size=0,
        )
    )
    registry = ProviderRegistry([MockProvider(CHAT, EMBEDDING, SPEECH)])
    app.state.providers = registry
    app.state.pipeline_engine = PipelineEngine(registry)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        threading.Event().wait(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]

    key, prefix, digest = keys.generate_api_key()

    async def seed() -> None:
        async with app.state.db_sessionmaker() as session:
            session.add_all(
                [
                    ModelRead(
                        model=CHAT,
                        approved=True,
                        capabilities=["generate", "thinking"],
                        thinking={"modes": ["auto"], "default": {"mode": "auto"}},
                    ),
                    ModelRead(
                        model=EMBEDDING,
                        approved=True,
                        capabilities=["embed"],
                        # As the model reference drafts Google's: every task type, batches, and
                        # the widths Google recommends.
                        embedding={
                            "task_types": list(TASK_TYPES.values()),
                            "supports_batch": True,
                            "dimensions": [768, 1536, 3072],
                        },
                    ),
                    ModelRead(model=SPEECH, approved=True, capabilities=["speech"]),
                    UseCaseRead(
                        slug=USE_CASE,
                        name="Examples",
                        allowed_models=[CHAT, EMBEDDING, SPEECH],
                        include_reasoning=True,
                    ),
                    UseCaseMemberRead(use_case_slug=USE_CASE, subject="alice", role="user"),
                    ApiKey(
                        prefix=prefix,
                        key_hash=digest,
                        subject="alice",
                        use_case=USE_CASE,
                        label="examples",
                        expires_at=datetime.now(UTC) + timedelta(days=1),
                    ),
                ]
            )
            await session.commit()

    asyncio.run(seed())
    try:
        yield {
            "GATEWAY_URL": f"http://127.0.0.1:{port}",
            "USE_CASE": USE_CASE,
            "API_KEY": key,
            "ACCESS_TOKEN": issuer.token(preferred_username="alice", azp="examples-cli"),
            "TOKEN_URL": f"{issuer.url}/protocol/openid-connect/token",
            "CLIENT_ID": CLIENT_ID,
            "CLIENT_SECRET": CLIENT_SECRET,
            "MODEL": CHAT,
            "EMBEDDING_MODEL": EMBEDDING,
            "SPEECH_MODEL": SPEECH,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        issuer.close()


def _run(script: str, settings: dict[str, str], tmp_path: Path, **more: str) -> str:
    """The example as a reader runs it: its own process, its own directory for what it writes,
    and an environment that holds nothing of this gateway's own configuration."""
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("AIRA_")
    }
    environment.update(settings, **more)
    completed = subprocess.run(  # noqa: S603 — this repository's own example, fixed arguments
        [sys.executable, str(EXAMPLES / script)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, f"{script} {more}: {output[-2000:]}"
    return output


@pytest.mark.parametrize("auth", SDK_FORMS)
@pytest.mark.parametrize(
    "script",
    ["google_genai_chat.py", "google_genai_embeddings.py", "google_genai_speech.py"],
)
def test_a_google_genai_example_runs_with_every_credential(
    gateway: dict[str, str], tmp_path: Path, script: str, auth: str
) -> None:
    _run(script, gateway, tmp_path, AUTH=auth)
    if script == "google_genai_speech.py":
        assert (tmp_path / "speech.wav").stat().st_size > 44, "no audio after the header"


@pytest.mark.parametrize("auth", SDK_FORMS)
def test_the_thinking_example_shows_the_reasoning_it_asked_for(
    gateway: dict[str, str], tmp_path: Path, auth: str
) -> None:
    assert "[mock thought]" in _run("google_genai_thinking.py", gateway, tmp_path, AUTH=auth)


@pytest.mark.parametrize("auth", REST_FORMS)
def test_the_rest_example_runs_with_every_credential(
    gateway: dict[str, str], tmp_path: Path, auth: str
) -> None:
    _run("rest_httpx.py", gateway, tmp_path, AUTH=auth)


def test_the_rest_example_names_its_use_case_in_a_header(
    gateway: dict[str, str], tmp_path: Path
) -> None:
    _run("rest_httpx.py", gateway, tmp_path, AUTH="key", USE_CASE_VIA="header")


def test_an_example_says_so_when_its_credential_is_refused(
    gateway: dict[str, str], tmp_path: Path
) -> None:
    """The examples exit non-zero on a refusal: a reader must not read a failure as a pass."""
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("AIRA_")
    }
    environment.update(gateway, API_KEY="aira_00000000_" + "0" * 48, AUTH="key")
    completed = subprocess.run(  # noqa: S603 — this repository's own example, fixed arguments
        [sys.executable, str(EXAMPLES / "rest_httpx.py")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode != 0
    # With the gateway's own words, as the README promises — not a traceback about a missing key.
    assert "401: " in completed.stderr, completed.stderr[-1000:]
