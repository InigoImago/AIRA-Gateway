"""Rate limiting and reservation release, wired into the Gemini route (FRD-405).

The unit tests cover the bucket arithmetic and the ledger. What is only observable here is the
route's contract with a client: the status, the ``Retry-After`` header, and the fact that a
request which failed upstream gives its reservation back.
"""

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.budgets.service import Reservation
from aira_gateway.config import GatewaySettings
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


class _AlwaysLimited:
    async def check(self, use_case, subject):  # noqa: ANN001, ANN201
        raise RateLimited("Request rate limit exceeded for use case.", retry_after="7")


class _FailingProvider:
    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent",))]

    async def generate(self, request):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream failure", 503)

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream failure", 503)
        yield  # pragma: no cover  (make this an async generator)

    async def embed(self, model, text):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream failure", 503)


class _TrackingBudgets:
    def __init__(self) -> None:
        self.released = 0
        self.settled = 0

    async def guard(self, use_case, subject, *, estimated=None):  # noqa: ANN001, ANN201
        return Reservation()

    async def settle(self, reservation, tokens, *, cost_nanos=None, now=None):  # noqa: ANN001, ANN201
        self.settled += 1

    async def release(self, reservation):  # noqa: ANN001, ANN201
        self.released += 1


def test_over_the_rate_limit_returns_429_with_retry_after() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.rate_limits = _AlwaysLimited()
    with TestClient(app) as client:
        response = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert response.status_code == 429
    assert response.json()["error"]["status"] == "RESOURCE_EXHAUSTED"
    # Without this a well-behaved client can only retry immediately, against the very thing the
    # limit is protecting.
    assert response.headers["Retry-After"] == "7"


def test_the_limit_is_checked_before_the_upstream_is_called() -> None:
    """A limit whose cost is paid anyway protects nothing."""
    app = create_app(GatewaySettings(auth_required=False))
    app.state.rate_limits = _AlwaysLimited()
    app.state.providers = ProviderRegistry([_FailingProvider()])
    budgets = _TrackingBudgets()
    app.state.budgets = budgets

    with TestClient(app) as client:
        response = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert response.status_code == 429
    assert budgets.settled == 0  # the budget was never even consulted for a settlement


def test_a_failed_upstream_releases_the_reservation() -> None:
    """Otherwise a provider outage is indistinguishable, to a use case, from having spent its
    whole month: requests that produced nothing would still have consumed the budget."""
    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_FailingProvider()])
    budgets = _TrackingBudgets()
    app.state.budgets = budgets

    with TestClient(app) as client:
        response = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert response.status_code == 503
    assert budgets.released == 1
    assert budgets.settled == 0


def test_a_successful_request_settles_rather_than_releases() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    budgets = _TrackingBudgets()
    app.state.budgets = budgets

    with TestClient(app) as client:
        response = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert response.status_code == 200
    assert (budgets.settled, budgets.released) == (1, 0)
