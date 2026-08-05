"""Budget enforcement wired into the Gemini route (FRD-401)."""

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.auth.principal import Principal
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.service import BudgetService, Reservation
from aira_gateway.config import GatewaySettings

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


class _MemberOf:
    """Stand-in OIDC validator resolving every token to a member of ``use_case``."""

    def __init__(self, use_case: str) -> None:
        self._use_case = use_case

    def validate(self, token: str) -> Principal:
        return Principal(subject="someone", method="oidc", use_cases=(self._use_case,))


class _BlockingBudgets:
    async def guard(self, use_case, subject, *, estimated=None):  # noqa: ANN001, ANN201
        raise BudgetExceeded("Request budget exhausted for use_case (day).")

    async def settle(self, reservation, tokens, *, cost_nanos=None, now=None):  # noqa: ANN001, ANN201
        reservation.resolved = True

    async def release(self, reservation):  # noqa: ANN001, ANN201
        reservation.resolved = True

    hold = BudgetService.hold


class _RecordingBudgets:
    def __init__(self) -> None:
        self.recorded: list[int] = []
        self.costs: list[int | None] = []
        self.released = 0
        self.estimates: list[object] = []

    async def guard(self, use_case, subject, *, estimated=None):  # noqa: ANN001, ANN201
        self.estimates.append(estimated)
        return Reservation()

    async def settle(self, reservation, tokens, *, cost_nanos=None, now=None):  # noqa: ANN001, ANN201
        reservation.resolved = True
        self.recorded.append(tokens)
        self.costs.append(cost_nanos)

    async def release(self, reservation):  # noqa: ANN001, ANN201
        reservation.resolved = True
        self.released += 1

    hold = BudgetService.hold


def test_over_budget_returns_429() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.budgets = _BlockingBudgets()
    with TestClient(app) as client:
        resp = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)
    assert resp.status_code == 429
    assert resp.json()["error"]["status"] == "RESOURCE_EXHAUSTED"


def test_within_budget_records_usage() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    recorder = _RecordingBudgets()
    app.state.budgets = recorder
    with TestClient(app) as client:
        resp = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)
    assert resp.status_code == 200
    assert len(recorder.recorded) == 1  # usage recorded once


def test_stream_records_usage() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    recorder = _RecordingBudgets()
    app.state.budgets = recorder
    with TestClient(app) as client:
        resp = client.post("/v1beta/models/mock-1:streamGenerateContent", json=_BODY)
    assert resp.status_code == 200
    assert len(recorder.recorded) == 1


class _UsageBudgets:
    async def usage(self, use_case):  # noqa: ANN001, ANN201
        return [{"id": 1, "used_tokens": 5, "used_requests": 2}]


def test_usage_endpoint_reports_consumption() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.budgets = _UsageBudgets()
    with TestClient(app) as client:
        resp = client.get("/v1beta/usage/demo-uc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["use_case"] == "demo-uc"
    assert body["usage"][0]["used_requests"] == 2


def test_usage_endpoint_requires_authentication() -> None:
    """Consumption is per-use-case operational data, not public (ADR-0007)."""
    app = create_app(GatewaySettings(auth_required=True))
    app.state.budgets = _UsageBudgets()
    with TestClient(app) as client:
        resp = client.get("/v1beta/usage/demo-uc")
    assert resp.status_code == 401


def test_usage_endpoint_rejects_other_use_case() -> None:
    app = create_app(GatewaySettings(auth_required=True, demo_mode=True))
    app.state.budgets = _UsageBudgets()
    with TestClient(app) as client:
        # The demo key is unbound, so bind a principal explicitly via a bound key instead:
        # an OIDC principal that is not a member must be refused.
        app.state.oidc_validator = _MemberOf("other-uc")
        resp = client.get("/v1beta/usage/demo-uc", headers={"authorization": "Bearer jwt"})
    assert resp.status_code == 403


def test_usage_endpoint_rejects_invalid_slug() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.budgets = _UsageBudgets()
    with TestClient(app) as client:
        resp = client.get("/v1beta/usage/Not%20A%20Slug")
    assert resp.status_code == 400
