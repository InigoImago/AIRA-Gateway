"""Rate limiting and reservation release, wired into the Gemini route (FRD-405).

The unit tests cover the bucket arithmetic and the ledger. What is only observable here is the
route's contract with a client: the status, the ``Retry-After`` header, and the fact that a
request which failed upstream gives its reservation back.
"""

from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_gateway.api.gemini.routes import _stream_response
from aira_gateway.app import create_app
from aira_gateway.audit import AuditTrail
from aira_gateway.auth.attribution import Attribution
from aira_gateway.budgets.errors import BudgetExceeded
from aira_gateway.budgets.service import BudgetService, Reservation
from aira_gateway.config import GatewaySettings
from aira_gateway.core.canonical import CanonicalMessage, CanonicalRequest, Role
from aira_gateway.db.models import RequestLog
from aira_gateway.ratelimit.errors import RateLimited
from aira_gateway.upstreams.base import ProviderRegistry, UpstreamError, UpstreamModel

_BODY = {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]}


def _canonical() -> CanonicalRequest:
    return CanonicalRequest(
        model="mock-1",
        messages=[CanonicalMessage(role=Role.USER, text="hi")],
    )


def _prepared():  # noqa: ANN202
    """The pre-dispatch result the streaming path now takes, instead of a bare reservation.

    `FRD-128` gave the post-dispatch sequence one owner, and it needs the same thing every other
    path hands it: what was prepared, not just what was reserved.
    """
    from aira_gateway.api.serving import Prepared
    from aira_gateway.catalog import ModelDeclaration

    return Prepared(
        canonical=_canonical(),
        embed=None,
        fallbacks=(),
        declaration=ModelDeclaration(name="mock-1"),
        reservation=Reservation(),
    )


def _stream_request(app) -> Request:  # noqa: ANN001
    """A minimal ASGI request carrying the app state the streaming path reads."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1beta/models/mock-1:streamGenerateContent",
        "headers": [],
        "query_string": b"",
        "app": app,
        "client": ("127.0.0.1", 1234),
    }
    request = Request(scope)
    request.state.attribution = Attribution(subject="demo", method="demo", use_case=None)
    return request


class _AlwaysLimited:
    async def check(self, use_case, subject, units=1):  # noqa: ANN001, ANN201
        raise RateLimited("Request rate limit exceeded for use case.", retry_after="7")


class _FailingProvider:
    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent",))]

    async def generate(self, request):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream failure", 503)

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream failure", 503)
        yield  # pragma: no cover  (make this an async generator)

    async def embed(self, request):  # noqa: ANN001, ANN201
        raise UpstreamError("upstream failure", 503)


class _TrackingBudgets:
    def __init__(self) -> None:
        self.released = 0
        self.settled = 0

    async def guard(self, use_case, subject, *, estimated=None):  # noqa: ANN001, ANN201
        return Reservation()

    async def settle(self, reservation, tokens, *, cost_nanos=None, now=None, requests=1):  # noqa: ANN001, ANN201, E501
        reservation.resolved = True
        self.settled += 1

    async def release(self, reservation):  # noqa: ANN001, ANN201
        reservation.resolved = True
        self.released += 1

    # The real `hold` is reused rather than re-implemented: a hand-written copy of the
    # release-unless-resolved contract in a test double is exactly how a double drifts away from
    # the thing it stands in for.
    hold = BudgetService.hold

    # `FRD-125c` added a pre-pipeline check to the real service. Inherited rather than stubbed
    # out: a stand-in more permissive than the thing it replaces is how a control comes to be
    # tested against something that cannot refuse. It needs no session here because these stands
    # in carry no budgets, so the real method returns immediately.
    refuse_if_exhausted = BudgetService.refuse_if_exhausted


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


class _BoomProvider:
    """Fails with something that is *not* an UpstreamError — a bug, not an outage."""

    def models(self) -> list[UpstreamModel]:
        return [UpstreamModel("mock-1", "mock-1", ("generateContent",))]

    async def generate(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("a defect nobody anticipated")

    async def stream_generate(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("a defect nobody anticipated")
        yield  # pragma: no cover  (make this an async generator)

    async def embed(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("a defect nobody anticipated")


def test_an_unexpected_error_also_releases_the_reservation() -> None:
    """Only releasing on UpstreamError leaves a reservation behind for every other failure —
    a malformed upstream body, a pricing lookup that hits a database hiccup, an outright bug.
    The budget would then shrink with each defect until the period rolled over."""
    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_BoomProvider()])
    budgets = _TrackingBudgets()
    app.state.budgets = budgets

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert response.status_code == 500
    assert budgets.released == 1
    assert budgets.settled == 0


def test_a_failed_stream_releases_rather_than_charging_for_nothing() -> None:
    """A stream that produced no output consumed nothing. Settling would still book a request
    against a request-limited budget, so a provider outage would eat the allowance."""
    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_FailingProvider()])
    budgets = _TrackingBudgets()
    app.state.budgets = budgets

    url = "/v1beta/models/mock-1:streamGenerateContent"
    with TestClient(app) as client, client.stream("POST", url, json=_BODY) as response:
        response.read()

    assert budgets.released == 1
    assert budgets.settled == 0


async def test_a_client_that_disconnects_mid_stream_does_not_leak_the_reservation() -> None:
    """The settlement and the audit row sat after the streaming loop with nothing guarding them,
    so a client hanging up skipped both: the reservation stayed and the request vanished from the
    log despite having reached the upstream.

    Driven through the response's own iterator and closed explicitly, because going through
    ``TestClient`` buffers the whole body before the test can hang up — the test would then pass
    without ever reaching the path it is about.
    """
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    budgets = _TrackingBudgets()
    app.state.budgets = budgets

    with TestClient(app):  # runs the lifespan so app.state is complete
        request = _stream_request(app)
        response = _stream_response(
            request,
            app.state.providers.provider_for("mock-1"),
            _canonical(),
            _BODY,
            _prepared(),
            AuditTrail(operation="streamGenerateContent", requested_model="mock-1"),
            sse=False,
        )
        iterator = response.body_iterator
        await iterator.__anext__()  # one chunk, then the client goes away
        await iterator.aclose()

        assert budgets.settled + budgets.released == 1, "the reservation must be accounted for"

        # And the request must still appear in the audit log. The upstream was called, so a
        # client hanging up must not make the request disappear from the record.
        async with app.state.db_sessionmaker() as session:
            rows = list((await session.execute(select(RequestLog))).scalars())
        assert len(rows) == 1, "a disconnected stream must still be logged"
        assert rows[0].operation == "streamGenerateContent"


_EMBED_BODY = {"content": {"parts": [{"text": "hi"}]}}


class _BlockingBudgets:
    async def guard(self, use_case, subject, *, estimated=None):  # noqa: ANN001, ANN201
        raise BudgetExceeded("Cost budget exhausted for use_case (month).")

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


def test_embedding_is_rate_limited_like_everything_else() -> None:
    """The controls used to sit inside the generateContent branch, so `:embedContent` was
    unlimited and unbudgeted — a caller could send as fast and as much as it liked simply by
    choosing the other verb."""
    app = create_app(GatewaySettings(auth_required=False))
    app.state.rate_limits = _AlwaysLimited()

    with TestClient(app) as client:
        response = client.post("/v1beta/models/mock-1:embedContent", json=_EMBED_BODY)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"


def test_embedding_passes_the_budget_and_is_settled() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    budgets = _TrackingBudgets()
    app.state.budgets = budgets

    with TestClient(app) as client:
        response = client.post("/v1beta/models/mock-1:embedContent", json=_EMBED_BODY)

    assert response.status_code == 200
    assert (budgets.settled, budgets.released) == (1, 0)


def test_an_over_budget_embedding_is_refused() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.budgets = _BlockingBudgets()

    with TestClient(app) as client:
        response = client.post("/v1beta/models/mock-1:embedContent", json=_EMBED_BODY)

    assert response.status_code == 429
    assert response.json()["error"]["status"] == "RESOURCE_EXHAUSTED"


def test_a_failed_embedding_releases_its_reservation() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    app.state.providers = ProviderRegistry([_FailingProvider()])
    budgets = _TrackingBudgets()
    app.state.budgets = budgets

    with TestClient(app) as client:
        response = client.post("/v1beta/models/mock-1:embedContent", json=_EMBED_BODY)

    assert response.status_code == 503
    assert (budgets.settled, budgets.released) == (0, 1)


def test_a_successful_request_settles_rather_than_releases() -> None:
    app = create_app(GatewaySettings(auth_required=False))
    budgets = _TrackingBudgets()
    app.state.budgets = budgets

    with TestClient(app) as client:
        response = client.post("/v1beta/models/mock-1:generateContent", json=_BODY)

    assert response.status_code == 200
    assert (budgets.settled, budgets.released) == (1, 0)


# == every verb of every surface is metered (FRD-125c) ===========================================


class _RefusingLimiter:
    """A limiter that refuses everything, and remembers what weight it was asked for."""

    def __init__(self) -> None:
        self.weights: list[int] = []

    async def check(self, use_case, subject, units=1):  # noqa: ANN001, ANN201
        self.weights.append(units)
        raise RateLimited("Rate limit exceeded.", retry_after="3")


def _metered_app() -> tuple[object, _RefusingLimiter]:

    limiter = _RefusingLimiter()
    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    app.state.rate_limits = limiter
    return app, limiter


_CASES = [
    ("/v1beta/models/mock-1:generateContent", _BODY),
    ("/v1beta/models/mock-1:embedContent", {"content": {"parts": [{"text": "hi"}]}}),
    ("/kira/api/external/chat", {"request": {"parts": [{"text": "hi"}]}, "model_id": 1}),
    ("/kira/api/external/embed", {"text": "hi", "model_id": 1}),
]


async def test_every_verb_of_every_surface_is_rate_limited() -> None:
    """**The regression this exists for.**

    The rate-limit take moved out of `enforce_pre_dispatch` and into the Gemini routes, which left
    the KIRA surface unmetered on all three of its verbs. Nothing failed, because every test that
    asked whether a surface was limited asked it of the Gemini one. A control applied to some
    entry points and not others has to be impossible to write by accident — so this asks all of
    them, and the next surface is one line here.
    """
    from aira_gateway.db.models import ModelRead

    for path, body in _CASES:
        app, limiter = _metered_app()
        with TestClient(app) as client:
            async with app.state.db_sessionmaker() as session:
                # One row: the model is the primary key, and this surface addresses it by an
                # integer id, so both verbs resolve through the same entry.
                session.add(
                    ModelRead(model="mock-1", numeric_id=1, capabilities=["generate", "embed"])
                )
                await session.commit()
            response = client.post(path, json=body)

        assert response.status_code == 429, f"{path} was served without being metered"
        assert limiter.weights, f"{path} never reached the limiter"


async def test_reaching_a_reservation_without_the_early_gate_is_a_loud_failure() -> None:
    """The backstop, exercised directly.

    It never fires while every surface is wired correctly, which is the point — and also why a
    mutation removing it survived: with nothing reaching it, deleting it changes nothing anybody
    observes. So it is called on its own, without the gate, and asserted to refuse.

    What it protects is not a caller's request but a future surface: `FRD-106` will add a third,
    and a reservation taken without the gate is a surface serving traffic nobody meters. Failing
    here is how that becomes a test failure instead of an invoice.
    """
    import pytest

    from aira_gateway.api.serving import enforce_pre_dispatch

    app = create_app(GatewaySettings(auth_required=False, log_queue_size=0))
    with TestClient(app):
        request = _stream_request(app)  # a bare request: no gate has run on it

        with pytest.raises(RuntimeError, match="guard_before_work"):
            await enforce_pre_dispatch(request, model="mock-1", max_output_tokens=None)
