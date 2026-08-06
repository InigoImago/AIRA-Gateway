"""Canonical AIRA roles — re-exported from the shared definition.

The definition moved to :mod:`aira_common.roles` when the gateway came to need the same answer
to "is this caller governance" for reporting across use cases (ADR-0009). This module stays so
the Django side keeps importing from where it always has, and so there is one obvious place to
look from inside Management.
"""

from __future__ import annotations

from aira_common.roles import ALL_ROLES, GOVERNANCE_ROLES, Role, is_governance

__all__ = ["ALL_ROLES", "GOVERNANCE_ROLES", "Role", "is_governance"]
