"""Through the console and straight to the API, the gateway names the same workstation — in the
access line, on the audit row and in the bound on failed sign-ins (`FRD-156`).

The ticket's chain, with Docker's network named in `AIRA_TRUSTED_PROXIES`: a workstation reaches
the console's nginx (`172.18.0.5`) through the platform's reverse proxy, which Docker shows as
`172.18.0.1`; an application reaches the gateway through that proxy alone. Each request below says
where it connected from, as the network would.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import select

from aira_common.addresses import address_pseudonym
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.db.models import RequestLog

PATH = "/v1beta/models/mock-1:generateContent"
BODY = {"contents": [{"parts": [{"text": "hi"}]}]}
DOCKER = "172.18.0.0/16"
NGINX = "172.18.0.5"
PLATFORM_PROXY = "172.18.0.1"
WORKSTATION = "10.1.2.3"

#: The two paths of the ticket: where each connects from, and what the proxies on it appended.
CONSOLE = {"x-test-peer": NGINX, "x-forwarded-for": f"{WORKSTATION}, {PLATFORM_PROXY}"}
API = {"x-test-peer": PLATFORM_PROXY, "x-forwarded-for": WORKSTATION}


class _ConnectedFrom:
    """Each request connects from the address its `x-test-peer` names — the network's part."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            peer = dict(scope["headers"]).get(b"x-test-peer")
            if peer is not None:
                scope = {**scope, "client": (peer.decode(), 50000)}
        await self.app(scope, receive, send)


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, TestClient]]:
    import aira_common.logging.access as access

    app = create_app(GatewaySettings(auth_required=False, trusted_proxies=DOCKER, log_queue_size=0))
    with TestClient(_ConnectedFrom(app)) as client:
        monkeypatch.setattr(access, "_log", structlog.get_logger("aira.access"))
        yield app, client


def _access_lines(capsys: Any) -> list[dict[str, Any]]:
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [line for line in lines if line.get("event") == "http_request"]


async def test_both_paths_name_the_workstation_on_the_row_and_in_the_line(
    gateway: tuple[Any, TestClient], capsys: Any
) -> None:
    app, client = gateway
    capsys.readouterr()

    assert client.post(PATH, json=BODY, headers=CONSOLE).status_code == 200
    assert client.post(PATH, json=BODY, headers=API).status_code == 200

    async with app.state.db_sessionmaker() as session:
        rows = (await session.execute(select(RequestLog))).scalars().all()
    assert [row.source_ip for row in rows] == [WORKSTATION, WORKSTATION]
    lines = _access_lines(capsys)
    assert len(lines) == 2
    assert {(line["source_net"], line["source_id"]) for line in lines} == {
        ("10.1.2.0/24", address_pseudonym(WORKSTATION))
    }


async def test_a_caller_connecting_directly_is_recorded_as_itself_whatever_it_writes(
    gateway: tuple[Any, TestClient],
) -> None:
    app, client = gateway

    forged = {"x-test-peer": "10.9.9.9", "x-forwarded-for": "203.0.113.7"}
    assert client.post(PATH, json=BODY, headers=forged).status_code == 200

    async with app.state.db_sessionmaker() as session:
        (row,) = (await session.execute(select(RequestLog))).scalars().all()
    assert row.source_ip == "10.9.9.9"


def test_the_bound_on_failed_sign_ins_counts_the_workstation_on_both_paths() -> None:
    """One workstation's wrong key is bounded whichever path it takes — and it locks out nobody
    else behind the same proxies."""
    app = create_app(
        GatewaySettings(
            auth_required=True,
            redis_url="",
            trusted_proxies=DOCKER,
            max_auth_failures_per_minute=2,
        )
    )
    wrong = {"x-goog-api-key": "aira_x_y"}
    neighbour = {"x-test-peer": NGINX, "x-forwarded-for": f"10.1.2.4, {PLATFORM_PROXY}"}

    with TestClient(_ConnectedFrom(app)) as client:
        statuses = [
            client.post(PATH, json=BODY, headers={**wrong, **CONSOLE}).status_code,
            client.post(PATH, json=BODY, headers={**wrong, **CONSOLE}).status_code,
            client.post(PATH, json=BODY, headers={**wrong, **API}).status_code,
        ]
        other = client.post(PATH, json=BODY, headers={**wrong, **neighbour}).status_code

    assert statuses == [401, 401, 429]
    assert other == 401


def test_without_a_named_proxy_every_caller_is_the_connection_as_before() -> None:
    app = create_app(
        GatewaySettings(auth_required=True, redis_url="", max_auth_failures_per_minute=1)
    )
    wrong = {"x-goog-api-key": "aira_x_y"}

    with TestClient(_ConnectedFrom(app)) as client:
        first = client.post(PATH, json=BODY, headers={**wrong, **CONSOLE}).status_code
        # Another workstation through the same nginx shares its one address.
        second = client.post(
            PATH,
            json=BODY,
            headers={**wrong, "x-test-peer": NGINX, "x-forwarded-for": "10.1.2.4, 172.18.0.1"},
        ).status_code

    assert (first, second) == (401, 429)


def test_an_entry_that_is_no_address_refuses_to_start() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="AIRA_TRUSTED_PROXIES: 'proxy.internal'"):
        GatewaySettings(trusted_proxies="172.18.0.0/16,proxy.internal")


def test_the_first_line_names_the_proxies_the_gateway_believes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`process_started` says how the address is read — in the API and in every worker."""
    import aira_gateway.config as config_module

    said: dict[str, Any] = {}
    monkeypatch.setattr(config_module, "configure_observability", lambda **kwargs: False)
    monkeypatch.setattr(config_module, "say_started", lambda **kwargs: said.update(kwargs))

    config_module.configure_process(GatewaySettings(trusted_proxies=DOCKER))

    assert said["trusted_proxies"].described() == [DOCKER]
