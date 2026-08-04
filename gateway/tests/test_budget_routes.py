"""Budget enforcement wired into the Gemini route (FRD-401)."""

from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.config import GatewaySettings

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


class _BlockingBudgets:
    async def guard(self, use_case, subject):  # noqa: ANN001, ANN201
        raise BudgetExceeded("Request budget exhausted for use_case (day).")

    async def record(self, budgets, tokens):  # noqa: ANN001, ANN201
        return None


class _RecordingBudgets:
    def __init__(self) -> None:
        self.recorded: list[int] = []

    async def guard(self, use_case, subject):  # noqa: ANN001, ANN201
        return []

    async def record(self, budgets, tokens):  # noqa: ANN001, ANN201
        self.recorded.append(tokens)


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
