"""API-key helpers for the gateway (FRD-101).

The format/generation/hash logic is shared with Management via ``aira_common.apikeys``
(ADR-0006/FRD-205); this module re-exports it and adds the gateway-only demo key.
"""

from __future__ import annotations

from aira_common.apikeys import (
    NAMESPACE,
    generate_api_key,
    hash_api_key,
    is_aira_key,
    parse_prefix,
    verify_hash,
)

__all__ = [
    "DEMO_API_KEY",
    "NAMESPACE",
    "generate_api_key",
    "hash_api_key",
    "is_aira_key",
    "parse_prefix",
    "verify_hash",
]

# Deterministic key seeded in demo mode (FRD-101 FR-8) — demo only, never for production.
DEMO_API_KEY = "aira_demo0000_00112233445566778899aabbccddeeff00112233"
