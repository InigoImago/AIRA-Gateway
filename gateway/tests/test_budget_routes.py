"""Budget enforcement wired into the Gemini route (FRD-401)."""

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from aira_gateway.api.serving.controls import text_tokens
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

    # The caller's bound, plus the prompt the request sends: "hi" is input it is billed for too.
    assert recorder.estimates[0].tokens == 7 + text_tokens(["hi"])


def test_an_unbounded_request_falls_back_to_the_configured_estimate() -> None:
    app = create_app(GatewaySettings(auth_required=False, budget_estimate_output_tokens=321))
    recorder = _RecordingBudgets()
    app.state.budgets = recorder

    with TestClient(app) as client:
        client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert recorder.estimates[0].tokens == 321 + text_tokens(["hi"])


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


def test_the_budget_view_is_told_whether_budgets_refuse_at_all() -> None:
    """`FRD-610` §3.3: a limit with enforcement off keeps its bar and stops nothing.

    The console cannot answer this from its own settings — Management has a copy of no authority,
    and the plane that enforces is the one that must say so. Asserted on the route rather than the
    settings object, because what is open is the console's *screen*, and the screen reads this.
    """
    for enforce in (True, False):
        app = create_app(GatewaySettings(auth_required=False, enforce_budgets=enforce))
        app.state.budgets = _UsageBudgets()
        with TestClient(app) as client:
            body = client.get("/v1beta/usage/demo-uc").json()

        assert body["enforced"] is enforce
        # The limits and figures are unchanged: they are what somebody configured, and they will
        # refuse the moment enforcement is switched on.
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


# == a per-person allowance is that person's, whichever credential they used ====================


def _oidc_app(subject: str, username: str | None, sessionmaker) -> tuple[object, object]:
    """A gateway whose caller is an OIDC principal, with the real budget service behind it."""
    from aira_gateway.auth.dependencies import require_principal

    # `log_queue_size=0`: the audit writer's queue belongs to the event loop of the client that
    # started it, and a case that calls one app from more than one client would find it on another.
    app = create_app(GatewaySettings(auth_required=False, require_use_case=False, log_queue_size=0))
    app.state.budgets = BudgetService(sessionmaker)
    app.dependency_overrides[require_principal] = lambda: Principal(
        subject=subject, method="oidc", username=username, use_cases=("uc",)
    )
    return app, app.state.budgets


async def _each_member_budget(sessionmaker) -> None:
    from aira_gateway.db.models import BudgetRead

    async with sessionmaker() as session:
        session.add(
            BudgetRead(
                id=1,
                use_case="uc",
                scope="each_member",
                subject="",
                period="day",
                limit_requests=1,
                enabled=True,
            )
        )
        await session.commit()


async def test_that_budget_still_binds_nobody_else(sessionmaker) -> None:
    """The half that must not have been widened: one person spending their own allowance leaves
    everybody else's where it was. Asked of `each_member`, the scope that keys a counter on the
    person — a row naming somebody (`member`) is a scope this build no longer has, and asking it
    tested only that an unknown scope binds nobody."""
    await _each_member_budget(sessionmaker)

    def call(app) -> int:  # noqa: ANN001
        with TestClient(app) as client:
            return client.post(
                "/v1beta/models/mock-1:generateContent",
                json=_BODY,
                headers={"X-AIRA-Use-Case": "uc"},
            ).status_code

    alice, _ = _oidc_app("6f1c-alice", "alice", sessionmaker)
    bob, _ = _oidc_app("2fc398cc-717d-5350-a1db-c0d48a2bb4e1", "bob", sessionmaker)

    assert call(alice) == 200
    assert call(alice) == 429, "alice's own allowance of one is spent"
    assert call(bob) == 200, "alice's spent allowance refused bob"


# == what a reservation sets aside =================================================================


def test_the_reservation_counts_the_prompt_it_will_be_billed_for() -> None:
    """The prompt is input, and input is billed. An agent's context is most of what it costs —
    the reservation set aside the output cap alone, a few percent of the real figure."""
    app = create_app(GatewaySettings(auth_required=False, budget_estimate_output_tokens=100))
    recorder = _RecordingBudgets()
    app.state.budgets = recorder
    prompt = "x" * 400_000
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    with TestClient(app) as client:
        client.post("/v1beta/models/mock-1:generateContent", json=body)

    assert recorder.estimates[0].tokens == 100 + text_tokens([prompt])
    assert recorder.estimates[0].tokens >= 100_000


async def test_concurrent_large_prompts_cannot_pass_a_limit_with_room_for_one() -> None:
    """`FRD-405` §4.2, the guarantee the reservation exists for, with the prompt in it. Twelve
    agentic requests reserving their output cap each were admitted together against a
    50 000-token limit and booked 1.2 million; a reservation that counts the context admits one,
    and the rest see it."""
    import asyncio

    import fakeredis.aioredis as fakeredis
    import httpx

    from aira_gateway.budgets.ledger import BudgetLedger
    from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage
    from aira_gateway.db.models import BudgetRead, ModelRead
    from aira_gateway.upstreams.base import ProviderRegistry, UpstreamModel

    class _Runner:
        def __init__(self) -> None:
            self.client = fakeredis.FakeRedis(decode_responses=True)

        async def run(self, script, keys, args):  # noqa: ANN001, ANN201
            return await self.client.register_script(script)(keys=list(keys), args=list(args))

    class _Agent:
        """Answers only once every request has been either admitted or refused."""

        is_test_double = True

        def __init__(self) -> None:
            self.admitted = 0
            self.answer = asyncio.Event()

        def models(self) -> list[UpstreamModel]:
            return [UpstreamModel("agent-1", "1", ("generateContent",), provider="t")]

        async def generate(self, request):  # noqa: ANN001, ANN201
            self.admitted += 1
            await self.answer.wait()
            return CanonicalResponse(
                model="agent-1",
                text="ok",
                usage=CanonicalUsage(prompt_tokens=100_000, completion_tokens=900),
            )

    agent = _Agent()
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.providers = ProviderRegistry([agent])
    app.state.budgets = BudgetService(app.state.db_sessionmaker, ledger=BudgetLedger(_Runner()))
    body = {
        "contents": [{"role": "user", "parts": [{"text": "x" * 400_000}]}],
        "generationConfig": {"maxOutputTokens": 4096},
    }
    async with app.router.lifespan_context(app):
        async with app.state.db_sessionmaker() as session:
            session.add(ModelRead(model="agent-1", capabilities=["generate"]))
            session.add(
                BudgetRead(
                    id=1,
                    use_case="uc",
                    scope="use_case",
                    subject="",
                    period="month",
                    limit_tokens=50_000,
                    enabled=True,
                )
            )
            await session.commit()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            sent = [
                asyncio.create_task(
                    client.post(
                        "/v1beta/models/agent-1:generateContent",
                        json=body,
                        headers={"x-aira-use-case": "uc"},
                    )
                )
                for _ in range(5)
            ]
            for _ in range(500):
                if agent.admitted + sum(task.done() for task in sent) == len(sent):
                    break
                await asyncio.sleep(0.01)
            agent.answer.set()
            statuses = sorted(response.status_code for response in await asyncio.gather(*sent))

    assert statuses == [200, 429, 429, 429, 429]
