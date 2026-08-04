"""Typed runtime access to management settings.

Provides a single cached :class:`ManagementSettings` instance so views and Django's
``settings.py`` share one source of truth without relying on dynamically-attached
attributes (which the type checker cannot see).
"""

from __future__ import annotations

from functools import lru_cache

from aira_management.config.app_settings import ManagementSettings


@lru_cache(maxsize=1)
def get_settings() -> ManagementSettings:
    """Return the process-wide management settings instance."""
    return ManagementSettings()
