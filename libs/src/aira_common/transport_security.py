"""Whether a configured URL would carry credentials in the clear.

One rule, read by both planes' deployment checks (`ADR-0015`). Two URLs decide whether the whole
platform's authentication holds, and neither was checked until 2026-08-09:

- **The identity provider.** The JWKS is where signing keys come from. Fetched over plaintext,
  anyone on the path substitutes a key set of their own and mints tokens that verify — every role,
  every use case, every audit identity. It is the one misconfiguration that defeats authentication
  outright rather than degrading it.
- **Vault.** The AppRole login and every secret read cross that address, so plaintext hands over
  the credentials the platform is built to keep (`FRD-116`).

Loopback is exempt, deliberately. A sidecar or a mesh proxy on `127.0.0.1` is a normal deployment,
and traffic that never leaves the host cannot be read off the network — refusing it would push
operators towards `AIRA_ENVIRONMENT=local`, which turns *every* check off. A rule that is worked
around is worse than a narrower rule that is kept.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Hosts whose traffic never reaches a network. `urlsplit` lowercases nothing, so compare folded.
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def is_plaintext(url: str) -> bool:
    """True if ``url`` is `http://` to something other than loopback.

    An empty or unparseable value is **not** reported here: "unset" is a different problem with a
    different message, and a check that conflates them tells an operator to add TLS to a setting
    they never filled in.
    """
    if not url or not url.strip():
        return False
    parts = urlsplit(url.strip())
    if parts.scheme.lower() != "http":
        return False
    return (parts.hostname or "").lower() not in _LOOPBACK


def plaintext_problems(named_urls: dict[str, str]) -> list[str]:
    """One reason per plaintext URL, naming the setting and what it costs.

    Returns reasons rather than raising so a configuration review sees every problem at once —
    the same shape `unsafe_settings` uses on both planes, and the reason a deployment is not four
    attempts long.
    """
    return [
        f"{name} is plaintext HTTP ({url}). "
        "Anything on the network path can read and rewrite it — use https://, or a loopback "
        "address if a sidecar terminates TLS."
        for name, url in sorted(named_urls.items())
        if is_plaintext(url)
    ]
