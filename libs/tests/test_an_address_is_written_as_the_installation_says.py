"""A network address outside the audit trail: a pseudonym and its network by default (`FRD-631`
FR-9).

The owner's decision: the exact address as a keyed pseudonym — one address, one value, so a SIEM can
count failures per source — beside its /24 (IPv4) or /48 (IPv6) network in plain text, which says
where without saying which machine. `network` keeps only the network, `full` the address.
"""

from __future__ import annotations

import pytest

from aira_common.addresses import (
    AddressMode,
    address_attributes,
    address_fields,
    address_pseudonym,
    configure_addresses,
    describe_address,
    network_of,
)
from aira_common.pseudonym import configure_pseudonyms, pseudonym


@pytest.mark.parametrize(
    ("address", "network"),
    [
        ("10.1.2.3", "10.1.2.0/24"),
        ("2001:db8:1:2::5", "2001:db8:1::/48"),
        # A dual-stack socket reports an IPv4 caller this way; its network is the IPv4 one.
        ("::ffff:10.1.2.3", "10.1.2.0/24"),
        (" 192.168.7.200 ", "192.168.7.0/24"),
    ],
)
def test_the_network_is_the_first_three_parts(address: str, network: str) -> None:
    assert network_of(address) == network


@pytest.mark.parametrize("value", [None, "", "testclient", "unknown", "10.1.2"])
def test_what_is_not_an_address_has_no_network(value: str | None) -> None:
    assert network_of(value) is None


def test_by_default_a_line_carries_the_network_and_a_pseudonym_and_no_address() -> None:
    configure_addresses(AddressMode.PSEUDONYM)

    fields = address_fields("10.1.2.3")

    assert fields == {"source_net": "10.1.2.0/24", "source_id": address_pseudonym("10.1.2.3")}
    assert fields["source_id"].startswith("ip-") and "10.1.2.3" not in fields["source_id"]


def test_one_address_is_one_pseudonym_and_two_are_two() -> None:
    configure_addresses(AddressMode.PSEUDONYM)
    assert address_pseudonym("10.1.2.3") == address_pseudonym("10.1.2.3")
    assert address_pseudonym("10.1.2.3") != address_pseudonym("10.1.2.4")


def test_the_installations_key_decides_so_the_address_space_cannot_be_hashed_back() -> None:
    """An IPv4 network is a few million addresses; an unkeyed hash of one is reversed by hashing
    them all. The person pseudonyms' key, under a domain of its own."""
    configure_pseudonyms(enabled=False, secret="one")
    under_one = address_pseudonym("10.1.2.3")
    configure_pseudonyms(enabled=False, secret="two")

    assert address_pseudonym("10.1.2.3") != under_one


def test_an_address_and_a_name_spelled_alike_do_not_share_a_pseudonym() -> None:
    configure_pseudonyms(enabled=True, secret="key")

    assert address_pseudonym("10.1.2.3").removeprefix("ip-") != pseudonym("10.1.2.3").removeprefix(
        "p-"
    )


def test_network_mode_writes_only_the_network() -> None:
    configure_addresses(AddressMode.NETWORK)
    assert address_fields("10.1.2.3") == {"source_net": "10.1.2.0/24"}


def test_full_mode_writes_the_address() -> None:
    configure_addresses(AddressMode.FULL)
    assert address_fields("10.1.2.3") == {"source_ip": "10.1.2.3"}


def test_no_address_is_no_field() -> None:
    assert address_fields(None) == {}


def test_a_span_is_given_every_name_and_a_value_only_for_what_the_mode_writes() -> None:
    configure_addresses(AddressMode.PSEUDONYM)

    attributes = address_attributes("10.1.2.3")

    assert attributes["aira.source_ip"] is None
    assert attributes["aira.source_net"] == "10.1.2.0/24"
    assert attributes["aira.source_id"] == address_pseudonym("10.1.2.3")


@pytest.mark.parametrize(
    ("mode", "described"),
    [
        (AddressMode.FULL, "10.1.2.3"),
        (AddressMode.NETWORK, "10.1.2.0/24"),
        (AddressMode.PSEUDONYM, None),
    ],
)
def test_a_findings_description_names_the_address_as_the_mode_says(
    mode: AddressMode, described: str | None
) -> None:
    configure_addresses(mode)
    text = describe_address("10.1.2.3")
    if described is not None:
        assert text == described
    else:
        assert text == f"10.1.2.0/24 ({address_pseudonym('10.1.2.3')})"
        assert "10.1.2.3" not in text


def test_an_unknown_mode_is_refused() -> None:
    with pytest.raises(ValueError):
        configure_addresses("hashed")
