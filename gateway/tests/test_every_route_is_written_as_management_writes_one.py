"""The gateway writes a route as Management does: parameters as `{name}`, a leading `/` (`FRD-631`
FR-5).

A log store filters both planes' `http_request` and `auth_refused` lines by `route`, so one
spelling serves both — the one this framework writes already, which Management's lines now match.
A converter (`{name:path}`) would be a second spelling, and is written as its name.
"""

from __future__ import annotations

import re

from starlette.routing import Mount, Route

from aira_common.logging.access import readable_route, route_of
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings

#: What a route written for a reader may hold: the path's own characters and `{name}`; the
#: Gemini surface's verb is part of a resource name (`models/{model}:{verb}`).
READABLE = re.compile(r"/(?:[A-Za-z0-9._~:-]|\{[a-z_][a-z0-9_]*\}|/)*")


def test_every_route_the_gateway_answers_is_written_readably() -> None:
    app = create_app(GatewaySettings(test_database=True))
    templates = [route.path_format for route in app.routes if isinstance(route, Route | Mount)]
    assert templates

    unreadable = {
        template: route_of({"route": _Matched(template)})
        for template in templates
        if not READABLE.fullmatch(route_of({"route": _Matched(template)}) or "")
    }

    assert not unreadable, f"routes a line would write unreadably: {unreadable}"


class _Matched:
    def __init__(self, path_format: str) -> None:
        self.path_format = path_format


def test_a_converter_is_written_as_its_name() -> None:
    assert readable_route("/files/{path:path}") == "/files/{path}"
    assert readable_route("/v1beta/usage/{use_case}") == "/v1beta/usage/{use_case}"
