"""API-key generation, parsing, and hashing (FRD-101).

Format: ``aira_<prefix>_<secret>`` with hex-only prefix/secret (no ``_`` in the parts, so
splitting is unambiguous). Keys are high-entropy random, so SHA-256 is an appropriate,
fast hash (unlike passwords, no need for a slow KDF).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

NAMESPACE = "aira"
_PREFIX_BYTES = 4
_SECRET_BYTES = 24

# Deterministic key seeded in demo mode (FRD-101 FR-8) — demo only, never for production.
DEMO_API_KEY = "aira_demo0000_00112233445566778899aabbccddeeff00112233"


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
    return parts[1]


def is_aira_key(token: str) -> bool:
    """True if ``token`` looks like an AIRA API key (vs. an OIDC JWT)."""
    return token.startswith(f"{NAMESPACE}_")


def verify_hash(full: str, stored_hash: str) -> bool:
    """Constant-time comparison of a presented key against a stored hash."""
    return hmac.compare_digest(hash_api_key(full), stored_hash)
