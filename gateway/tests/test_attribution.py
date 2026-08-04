from fastapi.testclient import TestClient
from starlette.requests import Request

from aira_gateway.app import create_app
from aira_gateway.auth import keys
from aira_gateway.auth.attribution import resolve_use_case, usecases_from_groups
from aira_gateway.auth.principal import Principal
from aira_gateway.config import GatewaySettings

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
_OIDC_HEADERS = {"authorization": "Bearer tok"}


# ---- unit: group extraction ------------------------------------------------------------


def test_usecases_from_groups() -> None:
    assert usecases_from_groups(["/use-cases/demo-uc", "/use-cases/other-uc", "/random"]) == (
        "demo-uc",
        "other-uc",
    )
    assert usecases_from_groups(["/use-cases/demo-uc", "/use-cases/demo-uc"]) == ("demo-uc",)
    assert usecases_from_groups(["/use-cases/"]) == ()
    assert usecases_from_groups([]) == ()


# ---- unit: selector precedence ---------------------------------------------------------


def _request(headers: dict[str, str] | None = None, path_slug: str | None = None) -> Request:
    scope: dict = {
        "type": "http",
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
        "query_string": b"",
    }
    if path_slug is not None:
        scope["aira_use_case_path"] = path_slug
    return Request(scope)


def test_header_overrides_path() -> None:
    assert resolve_use_case(_request({"x-aira-use-case": "h"}, path_slug="p")) == "h"


def test_path_used_when_no_header() -> None:
    assert resolve_use_case(_request(path_slug="p")) == "p"


def test_blank_header_falls_back_to_path() -> None:
    assert resolve_use_case(_request({"x-aira-use-case": "   "}, path_slug="p")) == "p"


def test_no_selector_is_none() -> None:
    assert resolve_use_case(_request()) is None


# ---- integration: OIDC membership via routes -------------------------------------------


class _OidcStub:
    def __init__(self, use_cases: tuple[str, ...]) -> None:
        self._use_cases = use_cases

    def validate(self, token: str) -> Principal | None:
        if token != "tok":
            return None
        return Principal("oidc-user", "oidc", use_cases=self._use_cases)


def _oidc_client(
    use_cases: tuple[str, ...] = ("demo-uc",), *, require_use_case: bool = False
) -> TestClient:
    app = create_app(
        GatewaySettings(log_json=True, auth_required=True, require_use_case=require_use_case)
    )
    app.state.oidc_validator = _OidcStub(use_cases)
    return TestClient(app)


def _generate(client: TestClient, path: str, headers: dict[str, str]):
    return client.post(f"{path}/v1beta/models/mock-1:generateContent", json=_BODY, headers=headers)


def test_oidc_member_via_path() -> None:
    with _oidc_client() as client:
        assert _generate(client, "/uc/demo-uc", _OIDC_HEADERS).status_code == 200


def test_oidc_non_member_forbidden() -> None:
    with _oidc_client() as client:
        resp = _generate(client, "/uc/other-uc", _OIDC_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["error"]["status"] == "PERMISSION_DENIED"


def test_header_overrides_path_for_membership() -> None:
    with _oidc_client() as client:
        resp = _generate(client, "/uc/other-uc", {**_OIDC_HEADERS, "x-aira-use-case": "demo-uc"})
    assert resp.status_code == 200


def test_oidc_without_use_case_allowed_by_default() -> None:
    with _oidc_client() as client:
        assert _generate(client, "", _OIDC_HEADERS).status_code == 200


def test_require_use_case_rejects_missing() -> None:
    with _oidc_client(require_use_case=True) as client:
        resp = _generate(client, "", _OIDC_HEADERS)
    assert resp.status_code == 400
    assert resp.json()["error"]["status"] == "INVALID_ARGUMENT"


def test_require_use_case_skipped_for_demo() -> None:
    app = create_app(GatewaySettings(auth_required=False, require_use_case=True))
    with TestClient(app) as client:
        assert _generate(client, "", {}).status_code == 200


def test_demo_use_case_not_membership_checked(client) -> None:
    # demo principal (auth off): any use case is attributed, not authorized
    assert _generate(client, "/uc/anything", {}).status_code == 200


def test_apikey_use_case_not_membership_checked(authed_client) -> None:
    resp = _generate(authed_client, "/uc/anything", {"x-goog-api-key": keys.DEMO_API_KEY})
    assert resp.status_code == 200
