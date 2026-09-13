"""Whether a configured URL would carry credentials in the clear (`ADR-0015`).

One rule, read by both planes' deployment checks. Two URLs decide whether the platform's
authentication holds: the identity provider's JWKS (over plaintext, anyone on the path substitutes
the signing keys and mints tokens that verify) and Vault (the AppRole login and every secret read,
`FRD-116`).

Loopback is exempt: traffic that never leaves the host cannot be read off the network, and refusing
a sidecar would push operators to `AIRA_ENVIRONMENT=local`, which turns every check off.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

#: Hosts whose traffic never reaches a network. `urlsplit` lowercases nothing, so compare folded.
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def is_plaintext(url: str) -> bool:
    """True if ``url`` is `http://` to something other than loopback.

    An empty value is **not** reported: "unset" is a different problem with a different message.
    """
    if not url or not url.strip():
        return False
    parts = urlsplit(url.strip())
    if parts.scheme.lower() != "http":
        return False
    return (parts.hostname or "").lower() not in _LOOPBACK


def plaintext_problems(named_urls: Iterable[tuple[str, str]]) -> list[str]:
    """One reason per plaintext URL, naming the setting and what it costs.

    Returns reasons rather than raising, so a configuration review sees every problem at once.
    Takes **pairs**, not a mapping: one setting can name several URLs (`AIRA_OIDC_ISSUERS`,
    `FRD-118`), and a dict would keep only the last of them.
    """
    return [
        f"{name} is plaintext HTTP ({url}). "
        "Anything on the network path can read and rewrite it — use https://, or a loopback "
        "address if a sidecar terminates TLS."
        for name, url in sorted(named_urls)
        if is_plaintext(url)
    ]
