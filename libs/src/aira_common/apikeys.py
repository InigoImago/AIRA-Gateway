"""Shared API-key format, generation, and hashing (FRD-205, ADR-0006).

Both the Management control plane (which *issues* keys) and the Gateway data plane (which
*validates* them) depend on this single implementation so the format and hash always agree.

Format: ``aira_<prefix>_<secret>`` with hex-only prefix/secret (no ``_`` inside the parts, so
splitting is unambiguous). Keys are high-entropy random, so SHA-256 is an appropriate, fast
hash (unlike passwords, no need for a slow KDF).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

NAMESPACE = "aira"
_PREFIX_BYTES = 4
_SECRET_BYTES = 24

#: What a key's public half is made of: lowercase letters and digits, no longer than the column
#: that stores it (`api_keys.prefix`, 32 on both planes). Checked before the lookup, because until
#: then the prefix is the caller's own bytes — and Postgres answers a NUL in a text parameter with
#: an error, not with "no such key".
_PREFIX_SHAPE = re.compile(r"[0-9a-z]{1,32}")


class KeyLifetimeOutOfRange(ValueError):
    """A key lifetime this installation does not issue (`ADR-0015`)."""


def check_key_lifetime(days: int, max_days: int) -> int:
    """``days``, if it is a lifetime this installation issues; refused by name otherwise.

    **A key is always bounded** (`ADR-0015`): at least a day, at most ``max_days``
    (`AIRA_API_KEY_MAX_DAYS`), and never silently shortened. One definition for both planes —
    Management's issuing API and the gateway's break-glass CLI — because a second copy of a rule
    is the one that drifts, and a key either plane mints is honoured by the same gateway.
    """
    if days < 1:
        raise KeyLifetimeOutOfRange("A lifetime is a number of days, at least 1.")
    if days > max_days:
        raise KeyLifetimeOutOfRange(
            f"The longest lifetime this installation allows is {max_days} days."
        )
    return days


def generate_api_key() -> tuple[str, str, str]:
    """Return ``(full_key, prefix, key_hash)`` for a freshly generated key."""
    prefix = secrets.token_hex(_PREFIX_BYTES)
    secret = secrets.token_hex(_SECRET_BYTES)
    full = f"{NAMESPACE}_{prefix}_{secret}"
    return full, prefix, hash_api_key(full)


def hash_api_key(full: str) -> str:
    """Return the hex SHA-256 of the full key."""
    return hashlib.sha256(full.encode("utf-8")).hexdigest()


def parse_prefix(full: str) -> str | None:
    """Return the lookup prefix from a full key, or None if it is not an AIRA key."""
    parts = full.split("_")
    if len(parts) != 3 or parts[0] != NAMESPACE:
        return None
    return parts[1] if _PREFIX_SHAPE.fullmatch(parts[1]) else None


def is_aira_key(token: str) -> bool:
    """True if ``token`` looks like an AIRA API key (vs. an OIDC JWT)."""
    return token.startswith(f"{NAMESPACE}_")


def verify_hash(full: str, stored_hash: str) -> bool:
    """Constant-time comparison of a presented key against a stored hash."""
    return hmac.compare_digest(hash_api_key(full), stored_hash)
