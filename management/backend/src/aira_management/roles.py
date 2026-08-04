"""Canonical AIRA roles.

These five roles map to Keycloak groups + Django ``Group``s (object-level scoping via
``django-guardian`` arrives with the RBAC work in Phase 2 / FRD-201). Defined here so both
seeding (FRD-002) and later RBAC share one source of truth.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    GLOBAL_ADMIN = "global-admin"
    IT_SECURITY = "it-security"
    IT_STEUERUNG = "it-steuerung"
    USE_CASE_ADMIN = "use-case-admin"
    USE_CASE_USER = "use-case-user"


ALL_ROLES: tuple[Role, ...] = tuple(Role)
