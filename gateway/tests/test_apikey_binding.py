"""API-key → use-case binding in attribution (FRD-205, FRD-149).

An API key issued by Management is bound to one use case. A model call still names it — the
binding checks the selector rather than standing in for one — and a selector that disagrees with the
binding is rejected. A reading names nothing and is the key's own use case's. Unbound keys fall
back to the selector.
"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from aira_gateway.api.gemini.errors import GeminiHTTPError
from aira_gateway.auth.attribution import USE_CASE_PATH_KEY
from aira_gateway.auth.dependencies import require_attribution
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings


def _request(
    *, path_slug: str | None = None, header: str | None = None, method: str = "POST"
) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(settings=GatewaySettings()))
    headers = [(b"x-aira-use-case", header.encode())] if header else []
    scope: dict = {
        "type": "http",
        "method": method,
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


async def test_a_bound_keys_model_call_names_its_use_case() -> None:
    """The binding is not a selector (`FRD-149`): a model call with none is refused, and the
    refusal names the key's own use case as the prefix to put in front of the path."""
    with pytest.raises(GeminiHTTPError) as exc_info:
        await require_attribution(_request(), _bound("demo-uc"))
    assert exc_info.value.code == 400
    assert exc_info.value.status == "INVALID_ARGUMENT"
    assert "/uc/demo-uc" in exc_info.value.message


async def test_a_bound_keys_reading_is_its_own_use_case() -> None:
    """A reading spends nothing, so it names nothing — and a bound key's is attributed to the one
    use case it can reach, which is how `/v1beta/me/budget` answers a client that named none."""
    attribution = await require_attribution(_request(method="GET"), _bound("demo-uc"))
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
