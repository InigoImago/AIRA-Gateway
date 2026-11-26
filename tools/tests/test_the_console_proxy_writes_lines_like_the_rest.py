"""The console's nginx writes its access line as JSON, and leaves out what nobody may need
(`FRD-631` FR-5).

Its default line was text with the caller's address, the whole request line — a Gemini client's
`?key=`, a directory search's term — `X-Forwarded-For` and the user agent, and the container's own
healthcheck every fifteen seconds. Its own error line about a request carries the same address and
request line, and nginx has no format for it, so it is not written at all. Read from the template
the image renders.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = (
    Path(__file__).resolve().parents[2]
    / "management"
    / "frontend"
    / "deploy"
    / "default.conf.template"
)


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _log_format() -> str:
    match = re.search(r"log_format\s+aira_json\s+escape=json\s+(.*?);\n", _template(), re.S)
    assert match, "no `log_format aira_json escape=json …;` in the template"
    return match.group(1)


def test_the_line_is_json_and_says_what_every_access_line_says() -> None:
    fields = re.findall(r'"([a-z_]+)":', _log_format())
    assert {"timestamp", "level", "event", "method", "path", "status"} <= set(fields)
    assert '"event":"http_request"' in _log_format()


def test_the_line_carries_no_address_no_query_no_forwarded_for_no_user_agent() -> None:
    line = _log_format()
    for variable in (
        "$remote_addr",
        "$http_x_forwarded_for",
        "$http_user_agent",
        "$request_uri",
        "$args",
        "$query_string",
        "$request ",
    ):
        assert variable not in line, variable


def test_an_api_path_is_named_by_its_prefix_and_a_query_never_reaches_the_path() -> None:
    template = _template()
    assert '~^/api/                 "/api/";' in template
    assert '~^/gw/                  "/gw/";' in template
    assert "(?<aira_uri_path>[^?]*)" in template


def test_nginx_writes_none_of_its_own_lines_about_a_request() -> None:
    """An upstream that is down, slow or restarting is an error line naming the caller's address,
    the request line with its query string and the upstream's address with it. Only the levels
    about the process itself — `crit`, `alert`, `emerg` — may be written for the server."""
    server = _template().split("server {", 1)[1]
    levels = re.findall(r"^\s*error_log\s+\S+\s+(\w+);", server, re.M)
    assert levels, "the server keeps nginx's default error log, which names callers and queries"
    assert set(levels) <= {"crit", "alert", "emerg"}, levels


def test_the_healthcheck_writes_no_line_and_every_server_uses_the_format() -> None:
    template = _template()
    assert '"127.0.0.1 /index.html" 0;' in template
    assert "access_log /dev/stdout aira_json if=$aira_loggable;" in template
