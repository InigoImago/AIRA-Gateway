"""The caller's address is read through the proxies an installation names (`FRD-156`).

The ticket's chain: a workstation's request passes the platform's reverse proxy and the console's
nginx, an application's only the platform's proxy. With Docker's network named, both name the
workstation; a connection the list does not name is its own address, whatever its header says.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from aira_common.forwarded import (
    RETIRED_SETTINGS,
    WARN_EVERY_SECONDS,
    WARNED_AT_MOST,
    TrustedProxies,
    address_of,
    trusted_proxies,
)

DOCKER = "172.18.0.0/16"
NGINX = "172.18.0.5"
PLATFORM_PROXY = "172.18.0.1"
WORKSTATION = "10.1.2.3"


def _proxies(value: str = DOCKER) -> TrustedProxies:
    return TrustedProxies.parse(value)


def test_through_the_console_and_straight_to_the_api_the_caller_is_the_workstation() -> None:
    proxies = _proxies()

    console = proxies.client(NGINX, [f"{WORKSTATION}, {PLATFORM_PROXY}"])
    api = proxies.client(PLATFORM_PROXY, [WORKSTATION])

    assert console == api == WORKSTATION


def test_a_caller_connecting_directly_cannot_choose_its_address() -> None:
    """A connection the list does not name is the caller, and its header is its own words."""
    assert _proxies().client("10.9.9.9", ["203.0.113.7"]) == "10.9.9.9"
    assert _proxies().client("203.0.113.9", ["10.1.2.3, 172.18.0.1"]) == "203.0.113.9"


def test_what_a_caller_wrote_behind_the_proxies_is_never_reached() -> None:
    """The platform's proxy appends to what the workstation sent; the reading stops at the first
    address nobody vouches for, which is the workstation's own."""
    forged = [f"203.0.113.7, 10.9.9.9, {WORKSTATION}, {PLATFORM_PROXY}"]

    assert _proxies().client(NGINX, forged) == WORKSTATION


def test_a_list_that_names_the_callers_network_lets_them_choose() -> None:
    """Why the documentation says to name the proxies only: the ticket's example list, with the
    workstations' own network in it, believes what a workstation wrote."""
    forged = [f"203.0.113.7, {WORKSTATION}, {PLATFORM_PROXY}"]

    assert _proxies("172.16.0.0/12,10.0.0.0/8").client(NGINX, forged) == "203.0.113.7"


def test_no_list_is_the_connections_own_address() -> None:
    proxies = _proxies("")

    assert not proxies
    assert proxies.client(NGINX, [f"{WORKSTATION}, {PLATFORM_PROXY}"]) == NGINX
    assert proxies.client(None, [WORKSTATION]) is None


def test_every_entry_named_is_the_leftmost() -> None:
    """A workstation on the Docker host itself is in the named network: the leftmost entry is
    still what the first proxy saw."""
    assert _proxies().client(NGINX, [f"{PLATFORM_PROXY}"]) == PLATFORM_PROXY
    assert _proxies().client(NGINX, []) == NGINX


def test_every_header_line_is_read_in_order() -> None:
    """A proxy that adds a line of its own, rather than extending the last, is read the same way."""
    assert _proxies().client(NGINX, ["203.0.113.7", WORKSTATION, PLATFORM_PROXY]) == WORKSTATION


def test_an_entry_that_is_no_address_ends_the_reading_at_the_proxy_that_wrote_it() -> None:
    assert _proxies().client(NGINX, [f"{WORKSTATION}, unknown"]) == NGINX
    assert _proxies().client(NGINX, [f"{WORKSTATION}, , {PLATFORM_PROXY}"]) == WORKSTATION


@pytest.mark.parametrize(
    ("entry", "address"),
    [
        ("10.1.2.3:52114", "10.1.2.3"),
        ('"10.1.2.3"', "10.1.2.3"),
        ("[2001:db8::7]:443", "2001:db8::7"),
        ("[2001:db8::7]", "2001:db8::7"),
        ("2001:db8::7", "2001:db8::7"),
        ("::ffff:10.1.2.3", "10.1.2.3"),
        (" 10.1.2.3 ", "10.1.2.3"),
    ],
)
def test_an_entry_is_its_address_however_a_proxy_wrote_it(entry: str, address: str) -> None:
    assert str(address_of(entry)) == address
    assert _proxies().client(NGINX, [entry]) == address


@pytest.mark.parametrize("entry", ["unknown", "", "_hidden", "10.1.2.3:x", "[2001:db8::7"])
def test_what_is_no_address_is_none(entry: str) -> None:
    assert address_of(entry) is None


def test_an_ipv6_network_and_a_single_address_are_named_alike() -> None:
    proxies = _proxies("fd00:aa::/64, 192.0.2.10")

    assert proxies.client("fd00:aa::5", [WORKSTATION]) == WORKSTATION
    assert proxies.client("192.0.2.10", [WORKSTATION]) == WORKSTATION
    assert proxies.client("192.0.2.11", [WORKSTATION]) == "192.0.2.11"
    assert proxies.described() == ["fd00:aa::/64", "192.0.2.10/32"]


def test_a_connection_reported_as_ipv4_inside_ipv6_is_the_ipv4_address() -> None:
    assert _proxies().client("::ffff:172.18.0.5", [WORKSTATION]) == WORKSTATION
    assert _proxies("").client("::ffff:10.9.9.9", []) == "10.9.9.9"


def test_a_name_that_is_no_address_is_kept_as_the_connection_gave_it() -> None:
    """A test client's `testclient`, a Unix socket: no list can name it, so it is the caller."""
    assert _proxies().client("testclient", [WORKSTATION]) == "testclient"


@pytest.mark.parametrize("value", ["172.18.0.0/33", "proxy.internal", "172.18.0.0/16;10.0.0.1"])
def test_an_entry_that_is_neither_an_address_nor_a_network_is_refused_by_name(value: str) -> None:
    with pytest.raises(ValueError, match="AIRA_TRUSTED_PROXIES") as refused:
        TrustedProxies.parse(value)

    assert f"{value!r} is neither an address nor a network" in str(refused.value)


def test_the_list_is_read_once_per_value() -> None:
    assert trusted_proxies(DOCKER) is trusted_proxies(DOCKER)
    assert trusted_proxies(" 172.18.0.0/16 , ").described() == [DOCKER]


def _warnings(capsys: Any) -> list[dict[str, Any]]:
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return [line for line in lines if line.get("event") == "forwarded_for_ignored"]


@pytest.fixture
def lines(monkeypatch: pytest.MonkeyPatch) -> None:
    import structlog

    import aira_common.forwarded as forwarded
    from aira_common.addresses import AddressMode
    from aira_common.logging import configure_logging

    monkeypatch.setattr("aira_common.addresses._mode", AddressMode.NETWORK)
    configure_logging("INFO")
    monkeypatch.setattr(forwarded, "_log", structlog.get_logger("aira_common.forwarded"))


def test_a_private_connection_sending_a_header_nobody_reads_is_said_once_an_hour(
    lines: None, capsys: Any
) -> None:
    """What a proxy the installation forgot looks like — said where an alert sees it."""
    proxies = _proxies("")
    capsys.readouterr()

    for moment in (0.0, 1.0, WARN_EVERY_SECONDS - 1):
        proxies.client(NGINX, [WORKSTATION], now=moment)
    (said,) = _warnings(capsys)
    proxies.client(NGINX, [WORKSTATION], now=WARN_EVERY_SECONDS + 1)

    assert said["level"] == "warning" and "AIRA_TRUSTED_PROXIES" in said["hint"]
    # The address written as the installation writes addresses: here its network alone.
    assert said["source_net"] == "172.18.0.0/24" and "source_ip" not in said
    assert len(_warnings(capsys)) == 1


def test_no_warning_for_a_public_connection_a_header_or_a_named_proxy(
    lines: None, capsys: Any
) -> None:
    """A public address writing the header is a client or a forger, and a flood of them must not
    become a flood of lines; without a header there is nothing unread."""
    capsys.readouterr()
    # A public address — not one of the documentation ranges, which Python counts as private.
    _proxies("").client("8.8.8.8", [WORKSTATION], now=0.0)
    _proxies("").client(NGINX, [], now=0.0)
    _proxies().client(NGINX, [WORKSTATION], now=0.0)

    assert _warnings(capsys) == []


def test_the_connections_remembered_are_bounded(lines: None, capsys: Any) -> None:
    proxies = _proxies("")
    for n in range(WARNED_AT_MOST + 10):
        proxies.client(f"10.0.{n // 250}.{n % 250}", [WORKSTATION], now=0.0)

    assert len(proxies._warned) == WARNED_AT_MOST  # noqa: SLF001


def test_both_retired_settings_name_what_replaced_them() -> None:
    assert set(RETIRED_SETTINGS) == {"AIRA_TRUST_FORWARDED_FOR", "AIRA_TRUSTED_PROXY_HOPS"}
    assert all("AIRA_TRUSTED_PROXIES" in hint for hint in RETIRED_SETTINGS.values())
