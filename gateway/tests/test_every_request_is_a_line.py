"""Every request is a line a SIEM receives, whatever is exported beside it (`FRD-631` FR-5, FR-6,
FR-11).

On Stage T traces are not exported, and the record of a request was a span — so the SIEM saw no
request at all, and no refused one. Three lines now, through the same pipeline as every other:

- ``request_recorded`` for every audit row, said where every row passes (`RequestLogWriter.submit`)
  with the row's metadata and never its bodies;
- ``http_request`` for every request the gateway answers, with its route and duration;
- ``auth_refused`` for every attempt refused before attribution, bounded per source.

Read back as the log store receives them — rendered by the pipeline, a person a pseudonym under
strict data mode and an address its network and pseudonym — because what matters is what leaves.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient

from aira_common.pseudonym import pseudonym
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.persistence import writer

BODY = {"contents": [{"parts": [{"text": "a prompt nobody else should read"}]}]}


def _fresh_loggers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loggers first used now, under the configuration the app just set: a module's logger cached
    by an earlier test would write past this test's capture."""
    import aira_common.logging.access as access
    import aira_common.logging.refusals as refusals

    monkeypatch.setattr(writer, "_log", structlog.get_logger("aira_gateway.persistence"))
    monkeypatch.setattr(access, "_log", structlog.get_logger("aira.access"))
    monkeypatch.setattr(refusals, "_log", structlog.get_logger("aira.security"))


def _lines(capsys: Any, event: str) -> list[dict[str, Any]]:
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [line for line in lines if line.get("event") == event]


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = GatewaySettings(
        auth_required=False, strict_data_mode=True, pseudonym_secret="the suite's key"
    )
    with TestClient(create_app(settings)) as client:
        _fresh_loggers(monkeypatch)
        yield client


def test_a_model_call_is_one_record_line_with_its_metadata_and_none_of_its_content(
    served: TestClient, capsys: Any
) -> None:
    capsys.readouterr()
    response = served.post("/v1beta/models/mock-1:generateContent", json=BODY)
    assert response.status_code == 200

    (line,) = _lines(capsys, "request_recorded")
    assert line["category"] == "request" and line["level"] == "info"
    assert line["request_id"] == response.headers["x-aira-request-id"]
    assert (line["model"], line["status"], line["outcome"], line["operation"]) == (
        "mock-1",
        200,
        "served",
        "generateContent",
    )
    assert line["api"] == "gemini" and line["total_tokens"] >= 1
    assert "a prompt nobody else should read" not in json.dumps(line)
    assert line["subject"] == pseudonym(line["subject"]) and line["subject"].startswith("p-")
    assert "source_ip" not in line and line["source_id"].startswith("ip-")


def test_every_request_is_one_access_line_and_a_probe_none(served: TestClient, capsys: Any) -> None:
    capsys.readouterr()
    served.post("/v1beta/models/mock-1:generateContent?alt=sse&key=aira_secret", json=BODY)
    served.get("/healthz")

    (line,) = _lines(capsys, "http_request")
    assert line["route"] == "/v1beta/models/{resource}"
    assert (line["method"], line["status"]) == ("POST", 200)
    assert isinstance(line["duration_ms"], float) and line["request_id"]
    assert "aira_secret" not in json.dumps(line)


def test_a_refused_sign_in_is_a_line_where_no_row_is_written(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    settings = GatewaySettings(
        auth_required=True, pseudonym_secret="the suite's key", max_auth_failures_per_minute=0
    )
    with TestClient(create_app(settings)) as client:
        _fresh_loggers(monkeypatch)
        capsys.readouterr()
        response = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=BODY,
            headers={"x-goog-api-key": "aira_wrong_00112233445566778899aabbccddeeff"},
        )

    assert response.status_code == 401
    (line,) = _lines(capsys, "auth_refused")
    assert (line["category"], line["outcome"], line["status"], line["api"]) == (
        "authentication",
        "unauthenticated",
        401,
        "gemini",
    )
    assert line["request_id"] == response.headers["x-aira-request-id"]
    assert line["reason"] == "Missing or invalid credentials."
    assert line["source_id"].startswith("ip-")
    assert _lines(capsys, "request_recorded") == []
