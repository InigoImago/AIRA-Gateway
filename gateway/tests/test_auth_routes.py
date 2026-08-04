from aira_gateway.auth import keys

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}
_ROUTE = "/v1beta/models/mock-1:generateContent"


def test_open_mode_allows_unauthenticated(client) -> None:
    assert client.post(_ROUTE, json=_BODY).status_code == 200


def test_auth_required_rejects_missing_credential(authed_client) -> None:
    resp = authed_client.post(_ROUTE, json=_BODY)
    assert resp.status_code == 401
    assert resp.json()["error"]["status"] == "UNAUTHENTICATED"


def test_auth_with_goog_api_key_header(authed_client) -> None:
    resp = authed_client.post(_ROUTE, json=_BODY, headers={"x-goog-api-key": keys.DEMO_API_KEY})
    assert resp.status_code == 200


def test_auth_with_bearer_key(authed_client) -> None:
    resp = authed_client.post(
        _ROUTE, json=_BODY, headers={"authorization": f"Bearer {keys.DEMO_API_KEY}"}
    )
    assert resp.status_code == 200


def test_auth_with_query_key(authed_client) -> None:
    resp = authed_client.post(f"{_ROUTE}?key={keys.DEMO_API_KEY}", json=_BODY)
    assert resp.status_code == 200


def test_auth_rejects_bad_key(authed_client) -> None:
    resp = authed_client.post(_ROUTE, json=_BODY, headers={"x-goog-api-key": "aira_dead_beef"})
    assert resp.status_code == 401


def test_auth_rejects_jwt_until_slice_b(authed_client) -> None:
    resp = authed_client.post(
        _ROUTE, json=_BODY, headers={"authorization": "Bearer eyJhbGciOiJ.not.real"}
    )
    assert resp.status_code == 401


def test_health_open_even_when_auth_required(authed_client) -> None:
    assert authed_client.get("/healthz").status_code == 200
