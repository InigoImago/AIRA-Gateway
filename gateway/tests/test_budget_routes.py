"""Budget enforcement wired into the Gemini route (FRD-401)."""

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.app import create_app
from aira_gateway.auth.principal import Principal
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.service import BudgetService, Reservation
from aira_gateway.config import GatewaySettings

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


@pytest_asyncio.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker]:
    from aira_gateway.db.base import build_engine, build_sessionmaker, create_all

    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await create_all(engine)
    yield build_sessionmaker(engine)
    await engine.dispose()


class _MemberOf:
    """Stand-in OIDC validator resolving every token to a member of ``use_case``."""

    def __init__(self, use_case: str) -> None:
        self._use_case = use_case

    def validate(self, token: str) -> Principal:
        return Principal(subject="someone", method="oidc", use_cases=(self._use_case,))


class _BlockingBudgets:
    async def guard(self, use_case, subject, *, estimated=None, username=None):  # noqa: ANN001, ANN201
        raise BudgetExceeded("Request budget exhausted for use_case (day).")

    # The real signature, including `requests` — a stand-in narrower than the thing it replaces
    # is how a control comes to be tested against something that cannot express what it does.
    async def settle(self, reservation, tokens, *, cost_nanos=None, now=None, requests=1):  # noqa: ANN001, ANN201, E501
        reservation.resolved = True

    async def release(self, reservation):  # noqa: ANN001, ANN201
        reservation.resolved = True

    hold = BudgetService.hold

    # `FRD-125c` added a pre-pipeline check to the real service. Inherited rather than stubbed
    # out: a stand-in more permissive than the thing it replaces is how a control comes to be
    # tested against something that cannot refuse. It needs no session here because these stands
    # in carry no budgets, so the real method returns immediately.
    refuse_if_exhausted = BudgetService.refuse_if_exhausted


class _RecordingBudgets:
    def __init__(self) -> None:
        self.recorded: list[int] = []
        self.costs: list[int | None] = []
        self.released = 0
        self.estimates: list[object] = []

    async def guard(self, use_case, subject, *, estimated=None, username=None):  # noqa: ANN001, ANN201
        self.estimates.append(estimated)
        return Reservation()

    # The real signature, including `requests` — a stand-in narrower than the thing it replaces
    # is how a control comes to be tested against something that cannot express what it does.
    async def settle(self, reservation, tokens, *, cost_nanos=None, now=None, requests=1):  # noqa: ANN001, ANN201, E501
        reservation.resolved = True
        self.recorded.append(tokens)
        self.costs.append(cost_nanos)

    async def release(self, reservation):  # noqa: ANN001, ANN201
        reservation.resolved = True
        self.released += 1

    hold = BudgetService.hold

    # `FRD-125c` added a pre-pipeline check to the real service. Inherited rather than stubbed
    # out: a stand-in more permissive than the thing it replaces is how a control comes to be
    # tested against something that cannot refuse. It needs no session here because these stands
    # in carry no budgets, so the real method returns immediately.
    refuse_if_exhausted = BudgetService.refuse_if_exhausted


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


def test_the_reservation_uses_the_caller_s_own_output_bound() -> None:
    """A caller that bounds its response must be reserved against that bound, not against the
    installation default. Ignoring it over-reserves by a wide margin on short requests, and a
    budget that refuses traffic it has room for is as wrong as one that lets too much through."""
    app = create_app(GatewaySettings(auth_required=False, budget_estimate_output_tokens=4096))
    recorder = _RecordingBudgets()
    app.state.budgets = recorder

    body = {**_BODY, "generationConfig": {"maxOutputTokens": 7}}
    with TestClient(app) as client:
        client.post("/v1beta/models/mock-1:generateContent", json=body)

    assert recorder.estimates[0].tokens == 7


def test_an_unbounded_request_falls_back_to_the_configured_estimate() -> None:
    app = create_app(GatewaySettings(auth_required=False, budget_estimate_output_tokens=321))
    recorder = _RecordingBudgets()
    app.state.budgets = recorder

    with TestClient(app) as client:
        client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert recorder.estimates[0].tokens == 321


def test_stream_records_usage() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    recorder = _RecordingBudgets()
    app.state.budgets = recorder
    with TestClient(app) as client:
        resp = client.post("/v1beta/models/mock-1:streamGenerateContent", json=_BODY)
    assert resp.status_code == 200
    assert len(recorder.recorded) == 1


class _UsageBudgets:
    def __init__(self) -> None:
        self.asked_for: list[str | None] = []
        self.named: list[str | None] = []

    async def usage(self, use_case, *, subject=None, username=None):  # noqa: ANN001, ANN201
        self.asked_for.append(subject)
        self.named.append(username)
        return [{"id": 1, "used_tokens": 5, "used_requests": 2, "measured_for": subject}]


def test_usage_endpoint_reports_consumption() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.budgets = _UsageBudgets()
    with TestClient(app) as client:
        resp = client.get("/v1beta/usage/demo-uc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["use_case"] == "demo-uc"
    assert body["usage"][0]["used_requests"] == 2


def test_usage_is_asked_whose_figures_to_report() -> None:
    """A per-person budget is one row and N counters, so the route has to say who is asking.

    Asserted at the route rather than only on the service, because the two are the failure the
    `FRD-124` lesson names: the service can resolve the caller perfectly and still be asked for
    nobody, and the answer to that — no figures — looks exactly like a fresh allowance.
    """
    app = create_app(GatewaySettings(auth_required=False))
    budgets = _UsageBudgets()
    app.state.budgets = budgets
    with TestClient(app) as client:
        assert client.get("/v1beta/usage/demo-uc").status_code == 200
    assert budgets.asked_for == ["demo"], "the route reported figures without saying whose"


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


# == a rule written about a person by name finds them, whichever credential they used ============


def _oidc_app(subject: str, username: str | None, sessionmaker) -> tuple[object, object]:
    """A gateway whose caller is an OIDC principal, with the real budget service behind it."""
    from aira_gateway.auth.dependencies import require_principal

    app = create_app(GatewaySettings(auth_required=False, require_use_case=False))
    app.state.budgets = BudgetService(sessionmaker)
    app.dependency_overrides[require_principal] = lambda: Principal(
        subject=subject, method="oidc", username=username, use_cases=("uc",)
    )
    return app, app.state.budgets


async def _member_budget(sessionmaker, subject: str) -> None:
    from aira_gateway.db.models import BudgetRead

    async with sessionmaker() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case="uc",
                scope="member",
                subject=subject,
                period="day",
                limit_requests=1,
                enabled=True,
            )
        )
        await session.commit()


async def test_a_member_budget_written_by_name_refuses_an_oidc_caller(sessionmaker) -> None:
    """The defect, at the route rather than in the service — which is where it lived.

    An administrator writes a budget about `alice`. Her API-key traffic bound, because a key's
    subject *is* her username; her browser and service-account traffic did not, because an OIDC
    subject is the directory's user id. Measured live before this was written: a limit of one
    request, four calls, four 200s, with the console showing the budget as active.

    Asserted here because the service can resolve the name perfectly and the **route** can still
    fail to hand it over — the same shape as `FRD-124`'s export, and the reason that lesson is
    written down twice.
    """
    await _member_budget(sessionmaker, "alice")
    app, _ = _oidc_app("1361bd47-388d-554e-a6b4-93efdf9a6605", "alice", sessionmaker)

    with TestClient(app) as client:
        first = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_BODY,
            headers={"X-AIRA-Use-Case": "uc"},
        )
        second = client.post(
            "/v1beta/models/mock-1:generateContent",
            json=_BODY,
            headers={"X-AIRA-Use-Case": "uc"},
        )

    assert first.status_code == 200
    assert second.status_code == 429, "the budget named her and did not find her"


async def test_that_budget_still_binds_nobody_else(sessionmaker) -> None:
    """The half that must not have been widened: matching a *name* is not matching anyone."""
    await _member_budget(sessionmaker, "alice")
    app, _ = _oidc_app("2fc398cc-717d-5350-a1db-c0d48a2bb4e1", "bob", sessionmaker)

    with TestClient(app) as client:
        for _ in range(3):
            resp = client.post(
                "/v1beta/models/mock-1:generateContent",
                json=_BODY,
                headers={"X-AIRA-Use-Case": "uc"},
            )
            assert resp.status_code == 200, "alice's budget refused bob"
