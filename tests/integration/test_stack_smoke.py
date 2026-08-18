"""Integration tests — require the live Compose stack (``make up``).

Excluded from the default hermetic run via the ``integration`` marker; run with
``make test-integration``. This is the pattern for codifying stack-dependent checks
(the manual curl/e2e verifications) into a CI "integration" stage.
"""

import pytest
import stack_addresses
from sqlalchemy import text

from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine

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

    rendered = subprocess.run(
        ["docker", "exec", "aira-frontend", "cat", "/etc/nginx/conf.d/default.conf"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    assert "resolver " in rendered, "no resolver: nginx cannot re-resolve an upstream at all"
    for upstream in ("$gateway_upstream", "$management_upstream"):
        assert f"proxy_pass       {upstream}" in rendered, (
            f"{upstream} is not passed through a variable, so its address is pinned at startup"
        )
