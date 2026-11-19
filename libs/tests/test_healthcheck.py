"""`python -m aira_common.healthcheck`: one command for every role an image runs (`FRD-153`).

A worker's container is judged by the heartbeat the worker writes, a server's by its `/healthz`,
and nothing in Compose has to say which is which. What decides between them is whether a heartbeat
can be read — so a file that is there and holds something else must never send the check to a
server that happens to answer, and a file that cannot be read must not stop a server's check.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.error
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from aira_common import healthcheck
from aira_common.healthcheck import PORT_VARIABLE, check, fetch_status, main
from aira_common.heartbeat import PATH_VARIABLE

NOW = 1_000_000.0


def _write(
    path: Path, *, age: float, max_age: float = 90.0, worker: str = "consumer", reason: str = ""
) -> Path:
    path.write_text(
        json.dumps(
            {
                "worker": worker,
                "max_age_seconds": max_age,
                "started_at": NOW - 3600,
                "written_at": NOW - age,
                "reason": reason or None,
            }
        )
    )
    return path


def _no_server(url: str) -> int:
    raise AssertionError(f"the server was asked ({url}), though a heartbeat was there")


def _answers(status: int):  # noqa: ANN202
    asked: list[str] = []

    def fetch(url: str) -> int:
        asked.append(url)
        return status

    fetch.asked = asked  # type: ignore[attr-defined]
    return fetch


# == a heartbeat decides alone =====================================================================


def test_a_young_heartbeat_is_healthy_and_no_server_is_asked(tmp_path: Path) -> None:
    beat = _write(tmp_path / "beat", age=12)

    assert check(path=beat, port="8001", now=NOW, fetch=_no_server) == (
        True,
        "consumer: progress 12 s ago, within 90 s",
    )


def test_a_stale_heartbeat_is_unhealthy_however_the_server_answers(tmp_path: Path) -> None:
    beat = _write(tmp_path / "beat", age=200, worker="relay", max_age=120)

    assert check(path=beat, port="8002", now=NOW, fetch=_no_server) == (
        False,
        "relay: no progress for 200 s, longer than the 120 s it may take",
    )


def test_the_reason_is_the_workers_own(tmp_path: Path) -> None:
    beat = _write(
        tmp_path / "beat",
        age=104,
        reason="an event is being applied or held for retry",
    )

    assert check(path=beat, port=None, now=NOW, fetch=_no_server) == (
        False,
        "consumer: no progress for 104 s, longer than the 90 s it may take — an event is being "
        "applied or held for retry",
    )


@pytest.mark.parametrize("content", [b"", b"garbage", b'{"worker": "relay"}', b"[]"])
def test_a_file_that_is_no_heartbeat_is_unhealthy_and_never_a_reason_to_ask_a_server(
    tmp_path: Path, content: bytes
) -> None:
    beat = tmp_path / "beat"
    beat.write_bytes(content)

    healthy, said = check(path=beat, port="8001", now=NOW, fetch=_no_server)

    assert not healthy
    assert said.startswith(f"{beat} holds no heartbeat: it ")


# == without one, the server is asked ==============================================================


def test_without_a_heartbeat_the_server_is_asked(tmp_path: Path) -> None:
    fetch = _answers(200)

    healthy, said = check(path=tmp_path / "beat", port="8002", now=NOW, fetch=fetch)

    assert healthy
    assert fetch.asked == ["http://127.0.0.1:8002/healthz"]  # type: ignore[attr-defined]
    assert said == "http://127.0.0.1:8002/healthz answered 200"


def test_a_heartbeat_that_cannot_be_read_is_none(tmp_path: Path) -> None:
    """A directory where the file should be cannot be read as one: the server decides."""
    (tmp_path / "beat").mkdir()

    assert check(path=tmp_path / "beat", port="8001", now=NOW, fetch=_answers(200))[0]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_a_heartbeat_nobody_may_read_is_none(tmp_path: Path) -> None:
    beat = _write(tmp_path / "beat", age=500)
    beat.chmod(0)
    try:
        assert check(path=beat, port="8001", now=NOW, fetch=_answers(200))[0]
    finally:
        beat.chmod(0o644)


def test_a_server_that_answers_an_error_is_unhealthy(tmp_path: Path) -> None:
    def fetch(url: str) -> int:
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)  # type: ignore[arg-type]

    assert check(path=tmp_path / "beat", port="8001", now=NOW, fetch=fetch) == (
        False,
        "http://127.0.0.1:8001/healthz answered 503",
    )


@pytest.mark.parametrize(
    ("error", "cause"),
    [
        (urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")), "Connection"),
        (TimeoutError("timed out"), "timed out"),
    ],
)
def test_a_server_that_does_not_answer_is_unhealthy_and_says_so(
    tmp_path: Path, error: OSError, cause: str
) -> None:
    def fetch(url: str) -> int:
        raise error

    healthy, said = check(path=tmp_path / "beat", port="8001", now=NOW, fetch=fetch)

    assert not healthy
    assert said.startswith(
        f"no heartbeat at {tmp_path / 'beat'}, and http://127.0.0.1:8001/healthz did not answer: "
    )
    assert cause in said


def test_a_server_status_outside_success_is_unhealthy(tmp_path: Path) -> None:
    assert not check(path=tmp_path / "beat", port="8001", now=NOW, fetch=_answers(302))[0]


@pytest.mark.parametrize("port", [None, ""])
def test_without_a_heartbeat_or_a_port_there_is_nothing_to_ask(tmp_path: Path, port: str) -> None:
    assert check(path=tmp_path / "beat", port=port, now=NOW, fetch=_no_server) == (
        False,
        f"no heartbeat at {tmp_path / 'beat'}, and {PORT_VARIABLE} names no port to ask",
    )


@pytest.mark.parametrize("port", ["http", "0", "65536", "-1"])
def test_a_port_that_is_not_one_is_said(tmp_path: Path, port: str) -> None:
    healthy, said = check(path=tmp_path / "beat", port=port, now=NOW, fetch=_no_server)

    assert not healthy
    assert said.endswith(f"{PORT_VARIABLE}={port!r} is not a port")


# == the command ===================================================================================


def test_the_command_reads_this_containers_file_and_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    beat = _write(tmp_path / "beat", age=0)
    monkeypatch.setenv(PATH_VARIABLE, str(beat))
    monkeypatch.setattr(healthcheck.time, "time", lambda: NOW + 100)

    assert main(fetch=_no_server) == 1
    assert capsys.readouterr().out == (
        "consumer: no progress for 100 s, longer than the 90 s it may take\n"
    )

    beat.unlink()
    monkeypatch.setenv(PORT_VARIABLE, "8001")
    assert main(fetch=_answers(200)) == 0
    assert capsys.readouterr().out == "http://127.0.0.1:8001/healthz answered 200\n"


@pytest.fixture
def server() -> Iterator[int]:
    class Answer(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the handler's name is the protocol's
            self.send_response(200 if self.path == "/healthz" else 404)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return None

    httpd = HTTPServer(("127.0.0.1", 0), Answer)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(httpd.server_address[1])
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_server_is_asked_directly_whatever_proxy_the_environment_names(
    server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A container behind a corporate proxy carries `HTTP_PROXY`, and a check that honoured it
    would ask the proxy for its own loopback."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)

    assert fetch_status(f"http://127.0.0.1:{server}/healthz") == 200


def _command(tmp_path: Path, **environment: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("AIRA_")}
    env.update(environment)
    return subprocess.run(  # noqa: S603 - this interpreter, fixed arguments
        [sys.executable, "-m", "aira_common.healthcheck"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=tmp_path,
    )


def test_the_command_runs_as_the_images_run_it(tmp_path: Path, server: int) -> None:
    """`python -m aira_common.healthcheck` — the image's own command line, in a process of its
    own: exit status and the one line Docker keeps as the reason."""
    beat = tmp_path / "beat"
    beat.write_text(
        json.dumps(
            {
                "worker": "relay",
                "max_age_seconds": 120,
                "started_at": 0.5,
                "written_at": 1.0,
                "reason": "its last pass failed: broker away",
            }
        )
    )

    stale = _command(tmp_path, **{PATH_VARIABLE: str(beat)})
    assert stale.returncode == 1
    assert stale.stdout.startswith("relay: no progress for ")
    assert stale.stdout.endswith(" — its last pass failed: broker away\n")

    beat.unlink()
    served = _command(tmp_path, **{PATH_VARIABLE: str(beat), PORT_VARIABLE: str(server)})
    assert (served.returncode, served.stdout) == (
        0,
        f"http://127.0.0.1:{server}/healthz answered 200\n",
    )


def test_the_command_loads_nothing_the_service_it_checks_is_made_of(tmp_path: Path) -> None:
    """It runs every fifteen seconds inside the core the service runs on. Importing the package's
    root once cost a third of a second of that core: the settings, OpenTelemetry, an HTTP client."""
    heavy = ("pydantic", "pydantic_settings", "opentelemetry", "structlog", "httpx", "aiokafka")
    probe = subprocess.run(  # noqa: S603 - this interpreter, fixed arguments
        [
            sys.executable,
            "-c",
            "import sys, aira_common.healthcheck; "
            "print(' '.join(sorted({m.split('.')[0] for m in sys.modules})))",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        check=True,
    )

    loaded = set(probe.stdout.split())
    assert "aira_common" in loaded, probe.stdout
    assert not loaded & set(heavy), sorted(loaded & set(heavy))
