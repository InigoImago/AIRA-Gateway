"""How a network address is written where AIRA's role checks do not reach (`FRD-631` FR-9).

A log line, a span and a finding's description leave the audit trail's protection behind: whoever
operates the log store, the tracing backend or the SIEM reads them, and an employee's workstation
address names the employee as surely as a username does — a DHCP lease is one look-up away. The
audit row keeps the address as it was (`request_logs.source_ip`), behind `incident.investigate`.

So everything else writes it as ``AIRA_TELEMETRY_IP`` says, in one of three ways:

- **pseudonym** (the default): the exact address as a keyed pseudonym — the same address is always
  the same value, so a SIEM can still count failures per source and follow one machine — beside the
  network it belongs to in plain text (``/24`` for IPv4, ``/48`` for IPv6), which says *where* in
  the network without saying *which* machine;
- **network**: only the network;
- **full**: the address as it is, for an installation whose SIEM correlates with firewall logs.

The field names differ by what they hold, never the value inside one name: ``source_ip`` is always
an address, ``source_net`` a network in CIDR notation, ``source_id`` a pseudonym. A SIEM that maps
``source_ip`` to an address type would otherwise refuse every line the day the mode changed.

The key is the person pseudonyms' (`AIRA_PSEUDONYM_SECRET`), with a domain of its own, so an
address and a username that happen to be spelled alike do not share a pseudonym.
"""

from __future__ import annotations

import ipaddress
from collections.abc import MutableMapping
from enum import StrEnum
from typing import Any

from aira_common.pseudonym import keyed_digest


class AddressMode(StrEnum):
    """What a log line, a span or a finding says about a network address."""

    PSEUDONYM = "pseudonym"
    NETWORK = "network"
    FULL = "full"


DEFAULT_MODE = AddressMode.PSEUDONYM

#: How much of an address is kept as its network: a /24 is one office floor or one subnet of a
#: data centre, a /48 one site — the granularity a provider assigns IPv6 to one customer.
IPV4_PREFIX = 24
IPV6_PREFIX = 48

#: What an address pseudonym starts with: a reader tells it from a person's (`p-`) at a glance.
PREFIX = "ip-"

#: Hex characters kept, as for a person's pseudonym: 64 bits.
LENGTH = 16

#: The domain an address is keyed under, so it never collides with a person's pseudonym.
_DOMAIN = b"address\x00"

_mode = DEFAULT_MODE


def configure_addresses(mode: str | AddressMode) -> AddressMode:
    """Write addresses as ``mode`` says from now on. Returns the mode in force."""
    global _mode
    _mode = AddressMode(mode)
    return _mode


def address_mode() -> AddressMode:
    """The mode in force in this process."""
    return _mode


def network_of(address: str | None) -> str | None:
    """The network ``address`` belongs to, in CIDR notation; ``None`` for what is not an address.

    An IPv4 address carried inside IPv6 (``::ffff:10.1.2.3``) is the IPv4 address it carries, which
    is how a dual-stack socket reports an IPv4 caller.
    """
    if not address:
        return None
    try:
        parsed = ipaddress.ip_address(address.strip())
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    prefix = IPV4_PREFIX if parsed.version == 4 else IPV6_PREFIX
    return str(ipaddress.ip_network(f"{parsed}/{prefix}", strict=False))


def address_pseudonym(address: str) -> str:
    """``address`` as a keyed pseudonym: the same address, the same value, and no address in it."""
    return PREFIX + keyed_digest(_DOMAIN + address.strip().encode("utf-8"))[:LENGTH]


def address_fields(address: str | None, *, prefix: str = "source") -> dict[str, str]:
    """What a line or a span says about ``address``, keyed ``<prefix>_ip``, ``_net``, ``_id``.

    Nothing for no address. A value that is not an address — a test client's name, ``unknown`` —
    has no network, so outside ``full`` only its pseudonym is written.
    """
    if not address:
        return {}
    if _mode is AddressMode.FULL:
        return {f"{prefix}_ip": address}
    fields: dict[str, str] = {}
    network = network_of(address)
    if network is not None:
        fields[f"{prefix}_net"] = network
    if _mode is AddressMode.PSEUDONYM:
        fields[f"{prefix}_id"] = address_pseudonym(address)
    return fields


def address_attributes(address: str | None, *, prefix: str = "aira.source") -> dict[str, Any]:
    """:func:`address_fields` as span attributes: ``aira.source_ip``, ``_net`` and ``_id``.

    Every name present, ``None`` for those the mode does not write — which a span leaves unset.
    """
    written = address_fields(address, prefix=prefix)
    return {f"{prefix}_{part}": written.get(f"{prefix}_{part}") for part in ("ip", "net", "id")}


def describe_address(address: str) -> str:
    """``address`` as a sentence for a reader names it — a finding's description."""
    if _mode is AddressMode.FULL:
        return address
    network = network_of(address)
    if _mode is AddressMode.NETWORK:
        return network or "an address outside IP"
    pseudonymous = address_pseudonym(address)
    return f"{network} ({pseudonymous})" if network else pseudonymous


def write_addresses(
    _logger: Any, _method: str, event: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """The structlog processor: every address field of a line, written as the mode says.

    One place for every line, as for a person's name (`aira_common.pseudonym`): a rule each call
    site had to remember is one the next call site forgets.
    """
    for field in ADDRESS_FIELDS & event.keys():
        value = event.pop(field)
        prefix = field.removesuffix("_ip")
        event.update(address_fields(value if isinstance(value, str) else None, prefix=prefix))
    return event


#: The log fields that carry a network address, each written as ``<prefix>_ip``.
ADDRESS_FIELDS = frozenset({"source_ip"})

#: What :func:`write_addresses` writes in their place.
WRITTEN_ADDRESS_FIELDS = frozenset(
    f"{field.removesuffix('_ip')}_{part}"
    for field in ADDRESS_FIELDS
    for part in ("ip", "net", "id")
)
