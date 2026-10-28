"""Integration tests — require the live Compose stack (``make up``).

Excluded from the default hermetic run via the ``integration`` marker; run with
``make test-integration``. This is the pattern for codifying stack-dependent checks
(the manual curl/e2e verifications) into a CI "integration" stage.
"""

import uuid

import pytest
import stack_addresses
from sqlalchemy import text

from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine

from .conftest import wait_for_row

pytestmark = pytest.mark.integration


async def test_postgres_reachable_and_queryable() -> None:
    engine = build_engine(GatewaySettings().database_url(use_sqlite=False))
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            assert result.scalar() == 1
    finally:
        await engine.dispose()


FRONTEND_URL = stack_addresses.url("console")


async def test_the_spa_reaches_both_services_through_its_own_origin() -> None:
    """The SPA calls `/api` and `/gw` same-origin so the bearer token never crosses to a third
    origin. If either proxy is broken the browser sees a 502 and the screens report the backend
    as unreachable — while both services are perfectly healthy, which is what makes it hard to
    diagnose from either side alone."""
    import httpx

    async with httpx.AsyncClient(base_url=FRONTEND_URL, timeout=15.0) as client:
        gateway = await client.get("/gw/healthz")
        management = await client.get("/api/v1/me")

    assert gateway.status_code == 200, "the /gw proxy does not reach the gateway"
    # Unauthenticated, so 401 is the *success* condition here: the request arrived and was judged.
    assert management.status_code == 401, "the /api proxy does not reach management"


def test_the_spa_proxy_resolves_its_upstreams_at_request_time() -> None:
    """nginx resolves a literal hostname in `proxy_pass` once, at configuration load, and keeps
    that address for the life of the process. Every redeploy of the gateway or of management then
    left this proxy talking to an address nobody was listening on, and only a restart of nginx
    fixed it — observed as 502s while both services were healthy.

    Passing the upstream through a variable is what defers resolution to request time. That is a
    property of the shape of the config, so it is asserted on the rendered file: the behaviour it
    protects only shows up after a container has actually moved, which no test can arrange
    cheaply.
    """
    import subprocess

    import stack_addresses

    rendered = subprocess.run(
        [
            "docker",
            "exec",
            stack_addresses.container("frontend"),
            "cat",
            "/etc/nginx/conf.d/default.conf",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert "resolver " in rendered, "no resolver: nginx cannot re-resolve an upstream at all"
    for upstream in ("$gateway_upstream", "$management_upstream"):
        assert f"proxy_pass       {upstream}" in rendered, (
            f"{upstream} is not passed through a variable, so its address is pinned at startup"
        )


def _request_of(size: int) -> bytes:
    """A generation request whose body is ``size`` bytes or a few more: one long user text."""
    return b'{"contents":[{"parts":[{"text":"' + b"x" * size + b'"}]}]}'


async def test_the_spa_proxy_lets_a_large_request_reach_the_gateway() -> None:
    """Every client the console sets up is pointed at `/gw`, and nginx refuses a body over 1 MiB
    unless told otherwise — with an HTML page of its own, before the gateway has seen the request,
    so nothing records it. A coding assistant resends its whole conversation with every step, and a
    session of about 300 000 tokens is past 1 MiB. Such a body must reach the gateway: refused here
    for want of a credential, in the gateway's envelope."""
    import httpx

    async with httpx.AsyncClient(base_url=FRONTEND_URL, timeout=60.0) as client:
        response = await client.post(
            "/gw/v1beta/models/any:generateContent",
            content=_request_of(2 * 1024 * 1024),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 401, (response.status_code, response.text[:200])
    assert "error" in response.json(), "the refusal is not the gateway's"


async def test_the_gateway_refuses_through_the_spa_proxy_what_is_over_its_ceiling() -> None:
    """With no limit of its own, the proxy hands the gateway a body over the gateway's ceiling as
    well, and the gateway refuses it in its own words and on the record. The proxy does not buffer
    the request, so it must pass on a refusal that comes while it is still sending."""
    import httpx

    ceiling = GatewaySettings().max_request_bytes
    probe = f"proxy-ceiling-probe-{uuid.uuid4().hex[:8]}"
    engine = build_engine(GatewaySettings().database_url(use_sqlite=False))
    try:
        async with httpx.AsyncClient(base_url=FRONTEND_URL, timeout=180.0) as client:
            response = await client.post(
                f"/gw/v1beta/models/{probe}:generateContent",
                content=_request_of(ceiling + 1024),
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 413, (response.status_code, response.text[:200])
        assert response.json()["error"]["message"].startswith("Request body too large"), (
            response.text[:200]
        )
        row = await wait_for_row(
            engine,
            "SELECT outcome FROM request_logs WHERE model = :model",
            {"model": probe},
            timeout=20.0,
        )
        assert row.outcome == "request_too_large"
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM request_logs WHERE model = :model"), {"model": probe}
            )
        await engine.dispose()
