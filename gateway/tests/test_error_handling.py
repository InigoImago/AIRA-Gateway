from fastapi import Query
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


class _BoomProvider:
    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent",))]

    async def generate(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("upstream exploded with a secret detail")

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("boom")
        yield  # pragma: no cover  (make this an async generator)

    async def embed(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("boom")


def test_unexpected_error_on_api_returns_gemini_500() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_BoomProvider()])
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["status"] == "INTERNAL"
    assert body["error"]["code"] == 500
    # the raw exception detail must not leak to the client
    assert "secret detail" not in resp.text


class _RaisingProvider:
    """Provider whose methods raise an ``UpstreamError`` with a configurable upstream status."""

    def __init__(self, status: int | None) -> None:
        self._status = status

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent",))]

    async def generate(self, request):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream failure", self._status)

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream failure", self._status)
        yield  # pragma: no cover  (make this an async generator)

    async def embed(self, request):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream failure", self._status)


def _raising_client(status: int | None) -> TestClient:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_RaisingProvider(status)])
    return TestClient(app, raise_server_exceptions=False)


def test_upstream_error_without_status_maps_to_502() -> None:
    with _raising_client(None) as client:
        resp = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["status"] == "UNAVAILABLE"
    assert body["error"]["message"] == "upstream failure"


def test_upstream_429_passes_through_as_resource_exhausted() -> None:
    with _raising_client(429) as client:
        resp = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)
    assert resp.status_code == 429
    assert resp.json()["error"]["status"] == "RESOURCE_EXHAUSTED"


def test_upstream_503_passes_through_as_unavailable() -> None:
    with _raising_client(503) as client:
        resp = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)
    assert resp.status_code == 503
    assert resp.json()["error"]["status"] == "UNAVAILABLE"


def test_an_upstream_400_is_a_precondition_failure_not_an_outage() -> None:
    """This test used to assert the opposite, and its own comment gave the reason to change it:
    "a 400 from the upstream reflects *our* config". It does — and calling that `UNAVAILABLE` sends
    whoever reads it to the provider's status page instead of to the declaration that is wrong.

    Found live: the catalog declared a thinking mode the server rejects by name, and the caller was
    told the provider was unavailable.
    """
    with _raising_client(400) as client:
        resp = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)
    assert resp.status_code == 400
    assert resp.json()["error"]["status"] == "FAILED_PRECONDITION"


def test_an_upstream_credential_failure_stays_masked() -> None:
    """The half the old test was right about. A 401 is about *our* credentials: the caller cannot
    act on it, and the provider's message may name the credential itself."""
    for code in (401, 403):
        with _raising_client(code) as client:
            resp = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)
        assert resp.status_code == 502, code
        assert resp.json()["error"]["status"] == "UNAVAILABLE"


def test_upstream_error_on_embed_maps_status() -> None:
    with _raising_client(429) as client:
        resp = client.post(
            "/v1beta/models/mock-1:embedContent",
            json={"content": {"parts": [{"text": "hi"}]}},
        )
    assert resp.status_code == 429
    assert resp.json()["error"]["status"] == "RESOURCE_EXHAUSTED"


def test_upstream_error_on_stream_terminates_cleanly() -> None:
    with _raising_client(503) as client:
        resp = client.post("/v1beta/models/mock-1:streamGenerateContent", json=_BODY)
    # Stream headers are already sent when the upstream fails: status stays 200, body is an
    # empty JSON array (the error is logged server-side, never leaked to the client).
    assert resp.status_code == 200
    assert resp.text == "[]"


def test_unexpected_error_on_non_api_returns_envelope() -> None:
    app = create_app(GatewaySettings(auth_required=False))

    @app.get("/kaboom")
    async def _kaboom() -> None:
        raise RuntimeError("internal secret")

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/kaboom")

    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"
    assert "internal secret" not in resp.text


# ---- a parameter the server will not take (2026-08-08) --------------------------------------
#
# FastAPI answers query validation with `422` and its own `{"detail": [...]}` list — a shape no
# other error on this API uses. A Google client reads `error.code` and `error.message`, so it
# reports "unknown error" and the caller never learns that `limit` has a maximum. The same finding
# as the routing handler, one layer in; found by a live round asking for `limit=100000`.


def test_a_rejected_query_parameter_uses_the_surface_s_envelope_and_names_the_field() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    with TestClient(app) as client:
        resp = client.get("/v1beta/traces", params={"limit": 100_000})

    assert resp.status_code == 400, "the caller's job is to fix the request"
    body = resp.json()
    assert body["error"]["status"] == "INVALID_ARGUMENT"
    # Naming the parameter is the whole point: "validation failed" is a correct answer and a
    # useless one when six parameters could be at fault.
    assert "limit" in body["error"]["message"]


def test_the_kira_surface_answers_a_bad_body_in_its_own_shape() -> None:
    """Two surfaces, two contracts (`FRD-129`). KIRA's routes parse their own bodies and answer
    `422` in the predecessor's envelope — deliberately, since a client migrating by changing a URL
    must keep getting the errors it already handles. What must never appear is the *framework's*
    shape."""
    app = create_app(GatewaySettings(auth_required=False))
    with TestClient(app) as client:
        resp = client.post("/kira/api/external/chat", json={"model_id": "not-an-integer"})

    body = resp.json()
    assert resp.status_code == 422
    assert body["code"] == "VALIDATION_ERROR"
    assert "model_id" in str(body["details"])
    assert "detail" not in body, "the framework's own shape reached a KIRA client"


def test_a_rejected_parameter_on_a_kira_path_uses_the_kira_envelope() -> None:
    """Every KIRA route reads its parameters raw today, so this branch is unreachable through the
    published surface — and that is exactly why it is asserted here rather than assumed. The next
    route that takes a typed parameter would otherwise answer KIRA clients in Gemini's envelope,
    silently, which is the `FRD-129` finding that produced the routing handler above."""
    app = create_app(GatewaySettings(auth_required=False))

    @app.get("/kira/api/external/probe")
    async def _probe(count: int = Query(..., le=5)) -> None:  # pragma: no cover - shape only
        return None

    with TestClient(app) as client:
        resp = client.get("/kira/api/external/probe", params={"count": 99})

    body = resp.json()
    assert resp.status_code == 400
    assert body["code"] == "INVALID_REQUEST"
    assert "count" in body["message"]
