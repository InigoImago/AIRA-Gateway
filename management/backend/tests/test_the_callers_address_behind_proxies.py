"""Through the console, Management names the workstation as the gateway does — in its access line
and in its bound on failed sign-ins (`FRD-156`).

Management is reached through the console's nginx only: the platform's reverse proxy, which Docker
shows as `172.18.0.1`, and nginx at `172.18.0.5`. With Docker's network in `AIRA_TRUSTED_PROXIES`,
the caller is the workstation; a connection the list does not name is its own address, whatever its
header says; with no list it is the connection, as the gateway reads it too.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog
from aira_management.apps.api import access
from aira_management.config.app_settings import ManagementSettings
from django.core.cache import cache
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

BASE = "/api/v1/use-cases/"
DOCKER = "172.18.0.0/16"
NGINX = "172.18.0.5"
WORKSTATION = "10.1.2.3"
THROUGH_THE_CONSOLE = {"REMOTE_ADDR": NGINX, "HTTP_X_FORWARDED_FOR": f"{WORKSTATION}, 172.18.0.1"}


class _RejectsEverything:
    def verify(self, token: str) -> None:
        del token


@pytest.fixture
def named(monkeypatch: pytest.MonkeyPatch) -> None:
    """The installation names Docker's network, as `AIRA_TRUSTED_PROXIES` would."""
    monkeypatch.setattr(access, "get_settings", lambda: ManagementSettings(trusted_proxies=DOCKER))


@pytest.fixture
def refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from aira_management.apps.api import authentication
    from aira_management.apps.api.attempts import FailedAuthentications

    cache.clear()
    monkeypatch.setattr(authentication, "build_management_verifier", lambda: _RejectsEverything())
    monkeypatch.setattr(
        authentication, "build_attempt_bound", lambda: FailedAuthentications("2/minute")
    )


def _attempt(client: APIClient, **where: str) -> int:
    return client.get(BASE, HTTP_AUTHORIZATION="Bearer not-a-real-token", **where).status_code


def test_the_access_line_names_the_workstation(named: None, refused: None) -> None:
    import aira_common.logging.access as common_access

    common_access._log = structlog.get_logger("aira.access")  # noqa: SLF001 — a fresh logger
    with structlog.testing.capture_logs() as logs:
        _attempt(APIClient(), **THROUGH_THE_CONSOLE)

    (line,) = [entry for entry in logs if entry["event"] == "http_request"]
    assert line["source_ip"] == WORKSTATION


def test_the_bound_counts_the_workstation_not_the_proxy(named: None, refused: None) -> None:
    client = APIClient()
    neighbour = {"REMOTE_ADDR": NGINX, "HTTP_X_FORWARDED_FOR": "10.1.2.4, 172.18.0.1"}

    statuses = [_attempt(client, **THROUGH_THE_CONSOLE) for _ in range(3)]

    assert statuses == [401, 401, 429]
    assert _attempt(client, **neighbour) == 401, "a neighbour behind the same proxy was locked out"


def test_a_caller_connecting_directly_cannot_choose_its_address(named: None, refused: None) -> None:
    """Rotating the header from an address nobody named buys no fresh bucket: it is not read."""
    client = APIClient()

    statuses = [
        _attempt(client, REMOTE_ADDR="10.9.9.9", HTTP_X_FORWARDED_FOR=f"203.0.113.{n}")
        for n in range(3)
    ]

    assert statuses == [401, 401, 429]
    assert access.source_of(_Request("10.9.9.9", "203.0.113.7")) == "10.9.9.9"  # type: ignore[arg-type]


def test_without_a_named_proxy_the_caller_is_the_connection(refused: None) -> None:
    """As the gateway reads it: behind the console that is its nginx, and everybody behind it
    shares one bucket until the installation names its proxies."""
    client = APIClient()
    neighbour = {"REMOTE_ADDR": NGINX, "HTTP_X_FORWARDED_FOR": "10.1.2.4, 172.18.0.1"}

    assert [_attempt(client, **THROUGH_THE_CONSOLE), _attempt(client, **THROUGH_THE_CONSOLE)] == [
        401,
        401,
    ]
    assert _attempt(client, **neighbour) == 429


class _Request:
    def __init__(self, peer: str, forwarded: str) -> None:
        self.META: dict[str, Any] = {"REMOTE_ADDR": peer, "HTTP_X_FORWARDED_FOR": forwarded}


def test_an_entry_that_is_no_address_is_refused_by_the_start_up_checks() -> None:
    from aira_management.config.security import misconfigured_settings

    problems = misconfigured_settings(ManagementSettings(trusted_proxies="proxy.internal"))

    assert any("AIRA_TRUSTED_PROXIES: 'proxy.internal'" in problem for problem in problems)


def test_a_proxys_scheme_is_believed_once_a_proxy_is_named() -> None:
    from aira_management.config.security import proxy_ssl_header

    assert proxy_ssl_header(ManagementSettings(trusted_proxies=DOCKER)) == (
        "HTTP_X_FORWARDED_PROTO",
        "https",
    )
    assert proxy_ssl_header(ManagementSettings()) is None
