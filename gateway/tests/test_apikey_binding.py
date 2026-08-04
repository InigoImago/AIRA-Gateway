"""API-key → use-case binding in attribution (FRD-205).

An API key issued by Management is bound to one use case: it needs no ``/uc`` selector, and a
selector that disagrees with the binding is rejected. Unbound keys fall back to the selector.
"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.attribution import USE_CASE_PATH_KEY
from aira_gateway.auth.dependencies import require_attribution
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings


def _request(*, path_slug: str | None = None, header: str | None = None) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(settings=GatewaySettings()))
    headers = [(b"x-aira-use-case", header.encode())] if header else []
    scope: dict = {
        "type": "http",
        "headers": headers,
        "query_string": b"",
        "app": app,
        "state": {},
    }
    if path_slug is not None:
        scope[USE_CASE_PATH_KEY] = path_slug
    return Request(scope)


def _bound(*use_cases: str) -> Principal:
    return Principal(subject="svc", method="api_key", label="k", use_cases=use_cases)


async def test_bound_key_needs_no_selector() -> None:
    attribution = await require_attribution(_request(), _bound("demo-uc"))
    assert attribution.use_case == "demo-uc"
    assert attribution.method == "api_key"


async def test_bound_key_with_matching_selector_ok() -> None:
    attribution = await require_attribution(_request(path_slug="demo-uc"), _bound("demo-uc"))
    assert attribution.use_case == "demo-uc"


async def test_bound_key_with_mismatched_selector_is_forbidden() -> None:
    with pytest.raises(GeminiHTTPError) as exc_info:
        await require_attribution(_request(path_slug="other-uc"), _bound("demo-uc"))
    assert exc_info.value.code == 403
    assert exc_info.value.status == "PERMISSION_DENIED"
    assert "demo-uc" in exc_info.value.message


async def test_unbound_key_falls_back_to_selector() -> None:
    attribution = await require_attribution(_request(path_slug="anything"), _bound())
    assert attribution.use_case == "anything"
