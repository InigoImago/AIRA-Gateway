"""One access line per request, from AIRA and not from the server (`FRD-631` FR-5).

uvicorn's access line was text, outside the export, with the caller's address in plain text and the
query string in it — `?q=<somebody's name>` on a directory search — and without the duration. The
line both planes write instead carries the route with its parameters as names, the status, the
duration and the address as the installation writes one, and is not written for a health probe.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aira_common.logging import configure_logging
from aira_common.logging.access import AccessLog, route_of


class Route:
    path = "/api/v1/use-cases/{slug}/members/{username}/"


async def _app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    scope["route"] = Route()
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b""})


async def _boom(scope: dict[str, Any], receive: Any, send: Any) -> None:
    raise RuntimeError("defect")


async def _call(app: Any, path: str, *, query: bytes = b"", **extra: Any) -> None:
    scope = {"type": "http", "method": "DELETE", "path": path, "query_string": query, **extra}
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b""}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)


def _lines(capsys: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in capsys.readouterr().out.splitlines()
        if line.strip() and json.loads(line).get("event") == "http_request"
    ]


async def test_a_request_is_one_line_with_its_route_status_and_duration(capsys: Any) -> None:
    configure_logging("INFO")

    await _call(
        AccessLog(_app, describe=lambda scope: {"request_id": "r-1", "source_ip": "10.1.2.3"}),
        "/api/v1/use-cases/uc/members/max.mustermann/",
        query=b"q=johannes",
    )

    (line,) = _lines(capsys)
    assert line["route"] == Route.path
    assert (line["method"], line["status"], line["request_id"]) == ("DELETE", 204, "r-1")
    assert isinstance(line["duration_ms"], float)
    written = json.dumps(line)
    assert "mustermann" not in written, "the route, never the name in the path"
    assert "johannes" not in written, "never the query string"
    assert "10.1.2.3" not in written and line["source_net"] == "10.1.2.0/24"


@pytest.mark.parametrize("probe", ["/healthz", "/readyz"])
async def test_a_health_probe_writes_no_line(capsys: Any, probe: str) -> None:
    """Every fifteen seconds in every container, about nobody."""
    configure_logging("INFO")

    await _call(AccessLog(_app), probe)

    assert _lines(capsys) == []


async def test_a_request_no_route_matched_is_written_by_its_path_without_the_query(
    capsys: Any,
) -> None:
    async def unrouted(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 404, "headers": []})

    configure_logging("INFO")
    await _call(AccessLog(unrouted), "/nowhere", query=b"key=aira_secret")

    (line,) = _lines(capsys)
    assert (line["path"], line["status"]) == ("/nowhere", 404)
    assert "route" not in line and "aira_secret" not in json.dumps(line)


async def test_a_request_that_failed_is_a_line_with_the_servers_answer(capsys: Any) -> None:
    configure_logging("INFO")

    with pytest.raises(RuntimeError):
        await _call(AccessLog(_boom), "/x")

    (line,) = _lines(capsys)
    assert line["status"] == 500


async def test_a_detail_that_cannot_be_read_does_not_cost_the_line(capsys: Any) -> None:
    def broken(scope: dict[str, Any]) -> dict[str, Any]:
        raise KeyError("state")

    configure_logging("INFO")
    await _call(AccessLog(_app, describe=broken), "/x")

    assert len(_lines(capsys)) == 1


def test_a_route_is_read_from_what_the_application_matched() -> None:
    assert route_of({"route": Route()}) == Route.path
    assert route_of({}) is None
