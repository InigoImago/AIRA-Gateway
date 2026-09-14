"""Canonical AIRA roles, re-exported from the shared definition in :mod:`aira_common.roles`.

The gateway needs the same answers (`ADR-0009`), so the definition is shared; this module is where
Management imports them from.
"""

from __future__ import annotations

from aira_common.roles import ALL_ROLES, Role

__all__ = ["ALL_ROLES", "Role"]
