"""Which address a request came from, through the proxies an installation names (`FRD-156`).

A request that passed proxies arrives from the last of them, and who sent it is in
``X-Forwarded-For``, which every proxy on the way **appends** to: the address it saw at the right,
whatever the caller wrote at the left. So the header is read from the right, and only as far as the
installation vouches for who wrote it. The connection's own address comes first: when
``AIRA_TRUSTED_PROXIES`` names it, the entry it appended is believed, and so on leftwards — the
first address the list does not name is the caller. A count of proxies could not do that:

- **Every path is right at once.** Through the console a request passes the platform's proxy and
  the console's nginx; straight to the gateway's API only the first. A fixed count is wrong on one
  of them — one too few names a proxy, one too many lets the caller write the address.
- **Nobody chooses their address.** A connection from an address the list does not name is the
  caller, whatever its header says; what a caller writes into the header stays to the left of the
  first entry a proxy of the installation wrote, and is never reached.

**Name the proxies, never the networks the callers are in**: a listed address is believed when it
says whom it forwarded for, so a caller inside a listed network can choose the address recorded for
it. Both planes read the address here, with one setting and one default — none, which records the
connection's own address, as both did before an installation named its proxies.

A connection from a private address that carries the header while the list does not name it is
said as a warning, once an hour per address: it is what a proxy the installation forgot looks like,
and every caller behind such a proxy shares its one address — in the audit trail, and in the bound
on failed sign-ins, where one wrong credential locks all of them out.
"""

from __future__ import annotations

import ipaddress
import time
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache

from aira_common.logging.pipeline import get_logger

_log = get_logger("aira_common.forwarded")

#: The setting both planes read, for the messages that name it.
SETTING = "AIRA_TRUSTED_PROXIES"

#: How often one connection's unused header is said again, and how many such connections are
#: remembered at once — a bound, so a flood of addresses cannot grow it.
WARN_EVERY_SECONDS = 3600.0
WARNED_AT_MOST = 256

#: The settings this replaced, and what to set instead — said at start where one is still set,
#: because a setting that silently does nothing is one somebody believes took effect.
RETIRED_SETTINGS = {
    "AIRA_TRUST_FORWARDED_FOR": (
        "no longer read: name the proxies whose X-Forwarded-For is believed in "
        "AIRA_TRUSTED_PROXIES, on both planes"
    ),
    "AIRA_TRUSTED_PROXY_HOPS": (
        "no longer read: a count of proxies is wrong on one path or the other — name the proxies "
        "in AIRA_TRUSTED_PROXIES, on both planes"
    ),
}

#: What a connection that carries the header and is not named is told to do.
_HINT = (
    "this address sends X-Forwarded-For and AIRA_TRUSTED_PROXIES does not name it, so every "
    "request from it is recorded under this address — if it is a proxy of this installation, add "
    "it (an address or its network) to AIRA_TRUSTED_PROXIES on both planes"
)

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def address_of(text: str | None) -> Address | None:
    """``text`` as an address, or ``None`` where it is none — ``unknown``, a name, nothing.

    An entry may carry a port (``198.51.100.7:52114``, ``[2001:db8::7]:443``) or quotes, as some
    proxies write it; an IPv4 address carried inside IPv6 (``::ffff:10.1.2.3``) is the IPv4 address,
    as a dual-stack socket reports an IPv4 caller.
    """
    if not text:
        return None
    entry = text.strip().strip('"')
    if entry.startswith("["):
        end = entry.find("]")
        if end == -1:
            return None
        entry = entry[1:end]
    elif entry.count(":") == 1:
        host, _, port = entry.partition(":")
        if port.isdigit():
            entry = host
    try:
        address = ipaddress.ip_address(entry)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


@dataclass(eq=False)
class TrustedProxies:
    """The proxies an installation vouches for, and the address a request came from through them."""

    networks: tuple[Network, ...] = ()
    #: When each unnamed connection that sent the header was last said, oldest first.
    _warned: OrderedDict[str, float] = field(default_factory=OrderedDict, repr=False)

    @classmethod
    def parse(cls, value: str) -> TrustedProxies:
        """``172.18.0.0/16, 10.20.30.40`` → the networks, a single address as a network of one.

        Raises :class:`ValueError` naming the first entry that is neither, so a typo stops the
        process rather than leaving every request recorded under a proxy's address.
        """
        networks: list[Network] = []
        for entry in (part.strip() for part in value.split(",")):
            if not entry:
                continue
            try:
                networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                raise ValueError(
                    f"{SETTING}: {entry!r} is neither an address nor a network — write "
                    "addresses and networks separated by commas, e.g. 172.18.0.0/16,10.20.30.40"
                ) from None
        return cls(tuple(networks))

    def __bool__(self) -> bool:
        return bool(self.networks)

    def trusts(self, address: Address | None) -> bool:
        """Whether ``address`` is a proxy this installation named."""
        return address is not None and any(address in network for network in self.networks)

    def described(self) -> list[str]:
        """The networks as the setting would write them, for the line a process starts with."""
        return [str(network) for network in self.networks]

    def client(
        self, peer: str | None, forwarded_for: Iterable[str], *, now: float | None = None
    ) -> str | None:
        """The address a request came from: ``peer`` — the connection's — or, where the list names
        it, the first address left of it in ``forwarded_for`` that the list does not name.

        ``forwarded_for`` is every ``X-Forwarded-For`` header line, in order: a proxy that adds a
        line of its own instead of extending the last is read the same way. An entry that is no
        address ends the reading at the proxy that wrote it — the nearest address known.
        """
        if not peer:
            return None
        current = address_of(peer)
        chain = [entry for line in forwarded_for for entry in line.split(",") if entry.strip()]
        if current is None or not self.trusts(current):
            if chain and current is not None and current.is_private:
                self._say_unused(str(current), time.monotonic() if now is None else now)
            return str(current) if current is not None else peer
        for entry in reversed(chain):
            written = address_of(entry)
            if written is None:
                break
            current = written
            if not self.trusts(current):
                break
        return str(current)

    def _say_unused(self, address: str, now: float) -> None:
        last = self._warned.get(address)
        if last is not None and now - last < WARN_EVERY_SECONDS:
            return
        self._warned[address] = now
        self._warned.move_to_end(address)
        while len(self._warned) > WARNED_AT_MOST:
            self._warned.popitem(last=False)
        _log.warning("forwarded_for_ignored", source_ip=address, hint=_HINT)


@lru_cache(maxsize=32)
def trusted_proxies(value: str) -> TrustedProxies:
    """The proxies ``value`` names, parsed once per value: every request of a process reads them."""
    return TrustedProxies.parse(value)
