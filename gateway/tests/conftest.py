from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings


@pytest.fixture(autouse=True, scope="session")
def _no_redis_from_this_machine() -> Iterator[None]:
    """This layer is the **hermetic** one, so it must not find a Redis that happens to be running.

    `redis_url` defaults to `redis://localhost:6379/0`, and `make up` publishes a Redis on exactly
    that address. So a developer with the stack up ran the "unit" suite against a **shared and
    durable** bucket store: `test_a_dry_run_takes_the_rate_limit_like_any_other_request` sets one
    request per minute, and the first request of the run was refused because a previous run had
    already spent the bucket — `retry_after: 32`, a number that can only come from outside the
    process.

    It passed in CI, where there is no Redis, and it passed on a machine whose stack was down. That
    is the whole shape of `LESSONS.md` §7's *"a unit test that reads the developer's machine"*: the
    verdict depended on what else the machine was doing, and the failure surfaced from the mutation
    harness — as a **red baseline**, which is the report that says nothing at all.

    Empty is this codebase's existing spelling for "no Redis" (`test_error_responses_are_headered`,
    `test_auth_attempt_bound` both pass it explicitly), and it leaves the process on
    `InMemoryTokenBucket`, which is per process and therefore per test run. Set through the
    environment rather than by editing a default, so a test that *wants* to say something about
    Redis still passes `redis_url=` explicitly and wins — an explicit keyword outranks the env.
    """
    import os

    previous = os.environ.get("AIRA_REDIS_URL")
    os.environ["AIRA_REDIS_URL"] = ""
    try:
        yield
    finally:
        if previous is None:
            del os.environ["AIRA_REDIS_URL"]
        else:
            os.environ["AIRA_REDIS_URL"] = previous


@pytest.fixture
def settings() -> GatewaySettings:
    # API-surface tests run with auth disabled; auth itself is tested via `authed_client`.
    return GatewaySettings(log_json=True, auth_required=False)


@pytest.fixture
def client(settings: GatewaySettings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def authed_client() -> Iterator[TestClient]:
    # Auth required; demo mode seeds the deterministic demo API key on startup.
    settings = GatewaySettings(log_json=True, auth_required=True, demo_mode=True)
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def instrumentation_restored() -> Iterator[None]:
    """Put the **global** client instrumentation back the way it was found.

    `HTTPXClientInstrumentor` and `SQLAlchemyInstrumentor` patch modules rather than applications,
    so a test that builds an app with `otel_enabled=True` leaves every later test's httpx call
    wrapped and its spans queued for a collector nothing is listening to — the shape the
    repository-root `conftest.py` was written about, one library along.

    It also makes the *next* test that means to instrument a **no-op**: both instrumentors are
    singletons that refuse a second `instrument()` and return without a word, so the second test
    gets the first one's tracer provider and its own in-memory exporter stays empty. That is how
    `test_outgoing_calls_are_traced.py` came to pass alone and fail in the suite.

    Cleared on the way **in** as well as on the way out, because a test that leaves the
    instrumentation on is exactly what this exists to survive.
    """
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    def _clear() -> None:
        for instrumentor in (HTTPXClientInstrumentor(), SQLAlchemyInstrumentor()):
            if instrumentor.is_instrumented_by_opentelemetry:
                instrumentor.uninstrument()

    _clear()
    try:
        yield
    finally:
        _clear()
